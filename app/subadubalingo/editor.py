"""The subtitle editor: the lines of a .srt (number, start, end, text) next to a picture of the video, a small timeline
around the chosen line, and the other languages made for the same video one switch away.

Click a line: the picture shows its start and its sound can be played. The text is edited in the line's box or on the
row itself (double-click). The start and end move in steps of 100 ms or by dragging the line's edges on the timeline
(the picture and the sound follow the edge, as in the cut review). A line can be split at the cursor, merged with the
next and deleted. Save writes every changed language with consistent numbering and no overlaps, keeps the first
original as "<name>.srt.bak", and offers the words corrected in the spoken language's text to corrections.txt.

The editor is shown in the main window's left column (like the cut review), opened from the done view (its result card
and the .srt rows of its files list) and from the setup view when the chosen video has subtitles. What is being edited
lives in a Session, so a theme or language switch (which rebuilds the window) keeps it; the widget is rebuilt around it.

The pure functions at the top (split, merge, overlaps, the corrections diff) have no Tk and are tested in
tests/test_editor.py.
"""
from __future__ import annotations

import bisect
import copy
import difflib
import queue
import re
import shutil
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk

from . import cutting, i18n, scrub, skin, textstate
from .common import (FFMPEG, LANGUAGES, Cue, Language, Runner, find_srt, guess_srt_language, log, read_srt,
                     srt_names, write_srt)
from .i18n import t
from .pickers import Tooltip, _Picker
from .theme import IMG, P, PIXEL_FONT, TERMINAL_FONT

try:
    import winsound
except ImportError:   # not on Windows: the editor is silent
    winsound = None

MIN_CUE_MS = 200        # the shortest line the editor makes (split, drag, overlap fixing)
NUDGE_MS = 100          # the +/- buttons
MAX_PLAY_MS = 15_000    # "Afspil linjen" plays at most this much of a long line
MAX_WORDS = 3           # a corrected word is a replacement of at most this many words by at most this many
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv")
VIEW_MIN_MS = 10_000    # the timeline shows at least this much around the chosen line
EDGE_PX = 8             # how near (design px) the mouse must be to grab a line's edge
DRAG_FRAME_MS = 60      # while dragging, a new picture at most this often
DRAG_SOUND_MS = 120     # and a sound snippet at most this often
# Told to Claude (MCP) when it sends a job while the user is editing (the job protocol's messages are Danish)
BUSY_MESSAGE = ("Brugeren retter undertekster i appen lige nu. Bed brugeren om at gemme og lukke "
                "\"Ret underteksterne\" og prøv så igen.")

# --- Pure functions ----------------------------------------------------------------------------


def wrap(text: str) -> str:
    """Text as one or two lines (engine.wrap: split near the middle when longer than 42 characters)."""
    from .engine import wrap as engine_wrap
    return engine_wrap(" ".join(text.split()))


def clean_text(text: str) -> str:
    """The text as written to the file: each line stripped, no empty lines."""
    return "\n".join(line.strip() for line in text.replace("\r", "").split("\n") if line.strip())


def flat(text: str) -> str:
    return " ".join(text.split())


def split_cue(cue: Cue, pos: int) -> tuple[Cue, Cue] | None:
    """The line split at character pos of its text, or None when one side would be empty or the line is too short.
    The time is shared in proportion to the characters on each side (a proxy for how long each part is spoken)."""
    left, right = flat(cue.text[:pos]), flat(cue.text[pos:])
    if not left or not right or cue.end - cue.start < 2 * MIN_CUE_MS:
        return None
    at = cue.start + (cue.end - cue.start) * len(left) / (len(left) + len(right))
    at = int(round(at / 10) * 10)
    at = min(max(at, cue.start + MIN_CUE_MS), cue.end - MIN_CUE_MS)
    return Cue(cue.start, at, wrap(left)), Cue(at, cue.end, wrap(right))


def merge_cues(a: Cue, b: Cue) -> Cue:
    """Two lines as one: from the first's start to the later end, the texts joined and wrapped again."""
    return Cue(min(a.start, b.start), max(a.end, b.end), wrap(flat(a.text) + " " + flat(b.text)))


def edge_limits(cues: list[Cue], i: int, side: str, duration: int | None = None) -> tuple[int, int]:
    """Where line i's start or end may go: not over its neighbours and not shorter than MIN_CUE_MS."""
    c = cues[i]
    if side == "start":
        lo = cues[i - 1].end if i > 0 else 0
        return lo, c.end - MIN_CUE_MS
    hi = cues[i + 1].start if i + 1 < len(cues) else (duration if duration else 10 ** 9)
    return c.start + MIN_CUE_MS, hi


def set_edge(cues: list[Cue], i: int, side: str, ms: int, duration: int | None = None) -> int:
    """Move line i's start or end to ms, kept within edge_limits; returns where it went. A line that already
    overlaps (lo > hi) keeps its value."""
    lo, hi = edge_limits(cues, i, side, duration)
    old = getattr(cues[i], side)
    if lo > hi:
        return old
    ms = int(min(max(ms, lo), hi))
    setattr(cues[i], side, ms)
    return ms


def fix_overlaps(cues: list[Cue], duration: int | None = None) -> tuple[list[Cue], int, int]:
    """The lines as written to the file: in time order, without empty ones, each at least MIN_CUE_MS long and ending
    no later than the next one starts. Returns (lines, how many had their times changed, how many empty were
    dropped). The input is not changed."""
    kept = [Cue(c.start, c.end, clean_text(c.text)) for c in cues]
    dropped = sum(1 for c in kept if not c.text)
    out = sorted((c for c in kept if c.text), key=lambda c: (c.start, c.end))
    before = [(c.start, c.end) for c in out]
    for c in out:
        c.start = max(0, int(c.start))
        if duration and c.start > duration - MIN_CUE_MS:
            c.start = max(0, duration - MIN_CUE_MS)
        if duration:
            c.end = min(int(c.end), duration)
        if c.end < c.start + MIN_CUE_MS:
            c.end = c.start + MIN_CUE_MS
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            if b.start - a.start >= MIN_CUE_MS:
                a.end = b.start
            else:   # starting (almost) together: the second moves on
                b.start = a.start + MIN_CUE_MS
                b.end = max(b.end, b.start + MIN_CUE_MS)
                a.end = b.start
    changed = sum(1 for c, (s, e) in zip(out, before) if (c.start, c.end) != (s, e))
    return out, changed, dropped


WORD_RE = re.compile(r"\w(?:[\w'’-]*\w)?")


def words_of(cues: list[Cue]) -> list[str]:
    return [w for c in cues for w in WORD_RE.findall(c.text)]


def word_changes(before: list[Cue], after: list[Cue], known: set[str] | None = None) -> list[tuple[str, str]]:
    """The words the user corrected: (wrong, right) for each place where at most MAX_WORDS words were replaced by at
    most MAX_WORDS others, in the order they occur, once each. Left out: only the first letter's case changed (a
    sentence start after a split), numbers, words already in the list (`known`: wrong words, lower case), and
    insertions or deletions (the user rewrote a sentence, not a misheard word)."""
    a, b = words_of(before), words_of(after)
    known = known or set()
    out: list[tuple[str, str]] = []
    seen = set()
    # Matched with the first letter in lower case, so a new sentence start ("fedt" -> "Fedt") is no change and does
    # not join the correction next to it
    norm = lambda w: w[:1].lower() + w[1:]
    sm = difflib.SequenceMatcher(None, [norm(w) for w in a], [norm(w) for w in b], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or i2 - i1 > MAX_WORDS or j2 - j1 > MAX_WORDS:
            continue
        wrong, right = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if wrong == right or wrong[1:] == right[1:] and wrong[:1].lower() == right[:1].lower():
            continue
        if wrong.replace(" ", "").isdigit() or right.replace(" ", "").isdigit():
            continue
        if wrong.lower() in known or wrong.lower() in seen:
            continue
        seen.add(wrong.lower())
        out.append((wrong, right))
    return out


def backup_path(srt: Path) -> Path:
    return srt.with_name(srt.name + ".bak")


def video_of(srt: Path) -> Path | None:
    """The video a subtitle file was made for (named by this app, in any of its languages), next to it."""
    lang = guess_srt_language(srt)
    if lang is None or not srt.parent.is_dir():
        return None
    try:
        videos = [p for p in srt.parent.iterdir() if p.suffix.lower() in VIDEO_EXTS and p.is_file()]
    except OSError:
        return None
    # The longest matching name first would be wrong ("X - klippet.mp4" vs "X.mp4"): srt_names is exact
    for v in sorted(videos, key=lambda p: (not srt.name.lower().startswith(p.stem.lower()), p.name)):
        if any(p == srt for p in srt_names(v, lang)):
            return v
    return None


def srts_for(video: Path) -> list[tuple[Language, Path]]:
    """The subtitles made for video, one per language, in the app's language order."""
    out = []
    for lang in LANGUAGES:
        p = find_srt(video, lang)
        if p is not None:
            out.append((lang, p))
    return out

# --- What is being edited ------------------------------------------------------------------------


@dataclass
class Doc:
    """One language's subtitles: the lines as edited, as last saved, and an undo history."""
    lang: Language | None
    path: Path
    cues: list[Cue]
    saved: list[Cue]
    undo: list = field(default_factory=list)

    @classmethod
    def load(cls, lang: Language | None, path: Path) -> "Doc":
        cues = read_srt(path)
        return cls(lang, path, cues, copy.deepcopy(cues))

    @property
    def dirty(self) -> bool:
        return [(c.start, c.end, clean_text(c.text)) for c in self.cues] != \
               [(c.start, c.end, clean_text(c.text)) for c in self.saved]

    def push_undo(self, sel: int):
        self.undo.append((copy.deepcopy(self.cues), sel))
        del self.undo[:-100]


@dataclass
class SaveResult:
    path: Path
    moved: int          # lines whose times were changed so they do not overlap
    dropped: int        # empty lines left out
    backup: Path | None  # made now (the first save)


def save_doc(doc: Doc, duration: int | None = None) -> SaveResult:
    """Write doc's lines to its file: the first original is kept as <name>.srt.bak, the lines are numbered from 1,
    in order, without overlaps or empty lines. The text state keeps the file current for its video."""
    cues, moved, dropped = fix_overlaps(doc.cues, duration)
    bak = backup_path(doc.path)
    made = None
    if doc.path.exists() and not bak.exists():
        shutil.copy2(doc.path, bak)
        made = bak
    write_srt(doc.path, cues)
    textstate.record_edit(doc.path)
    doc.cues = cues
    doc.saved = copy.deepcopy(cues)
    log.info("editor: saved %s (%d lines, %d moved, %d empty dropped)", doc.path.name, len(cues), moved, dropped)
    return SaveResult(doc.path, moved, dropped, made)


class Media:
    """The video behind the editor: its length, its sound (a 16 kHz mono WAV, for the timeline's loudness and the
    snippets) and single frames, all made in the background in a temporary folder. Events arrive in `events`."""

    def __init__(self, video: Path | None):
        self.video = video if video and video.exists() else None
        self.tmp = Path(tempfile.mkdtemp(prefix="sdl-editor-"))
        self.events: queue.Queue = queue.Queue()
        self.runner, self.frame_runner = Runner(), Runner()
        self.duration: int | None = None
        self.wav: Path | None = None
        self.env = None
        self.playing_until = 0.0
        self._sound_n = 0
        self._frames = scrub.Latest(self._frame_work)
        self._sound = scrub.Latest(self._sound_work)
        if self.video:
            threading.Thread(target=self._prepare, daemon=True).start()

    def _prepare(self):
        from . import engine
        try:
            self.duration = engine.probe(self.runner, self.video).duration_ms or None
            self.events.put(("duration",))
            wav = self.tmp / "sound.wav"
            code, _ = self.runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-threads", "2", "-i",
                                       self.video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
                                      quiet=True)
            if code == 0 and wav.exists():
                self.env = cutting.envelope(wav)
                self.wav = wav
            self.events.put(("sound",))
        except Exception:   # closed meanwhile, or ffmpeg missing: the editor works without picture and sound
            log.info("editor: no media for %s", self.video, exc_info=True)

    def frame(self, ms: int, width: int, height: int):
        """Ask for the picture at ms, fitted into width x height; arrives as ("frame", ms, path or None). Only the
        newest request is served."""
        if self.video:
            self._frames.put((int(ms), width, height))

    def _frame_work(self, item):
        ms, width, height = item
        ms = int(round(ms / 40) * 40)   # one frame at 25 fps: dragging back and forth reuses the files
        out = self.tmp / f"frame-{width}x{height}-{ms}.png"
        if not out.exists():
            try:
                self.frame_runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-ss", f"{ms / 1000:.3f}", "-i",
                                       self.video, "-frames:v", "1", "-vf",
                                       f"scale={width}:{height}:force_original_aspect_ratio=decrease", out],
                                      quiet=True)
            except Exception:   # the editor closed (the runner is cancelled) or ffmpeg is missing
                return
        self.events.put(("frame", item[0], out if out.exists() else None))

    def play(self, ms: int, length_ms: int):
        """Play length_ms of the sound from ms, cutting off whatever plays (silent until the sound is extracted)."""
        if winsound is not None and self.wav is not None:
            self._sound.put((int(ms), int(length_ms)))

    def stop(self):
        if winsound is not None:
            self._sound.put("stop")

    @property
    def playing(self) -> bool:
        return time.monotonic() < self.playing_until

    def _sound_work(self, item):
        if item == "stop":
            self.playing_until = 0.0
            winsound.PlaySound(None, 0)
            return
        ms, length = item
        samples, sr = scrub.read_snippet(self.wav, ms, length, fade_ms=10)
        if not len(samples):
            return
        self._sound_n = (self._sound_n + 1) % 3   # never rewrite the file that plays
        f = self.tmp / f"sound{self._sound_n}.wav"
        f.write_bytes(scrub.wav_bytes(samples, sr))
        winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self.playing_until = time.monotonic() + len(samples) / sr

    def close(self):
        self._frames.stop()
        self._sound.stop()
        if winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        self.runner.cancel()
        self.frame_runner.cancel()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Session:
    """Everything being edited for one video: a Doc per language, the chosen language and line, and the Media. It
    outlives the editor widget across a theme or language switch."""

    def __init__(self, srt: Path | None = None, video: Path | None = None, source: Language | None = None):
        self.video = video or (video_of(srt) if srt else None)
        found = srts_for(self.video) if self.video else []
        if srt is not None and not any(p == srt for _, p in found):
            found.insert(0, (guess_srt_language(srt), srt))
        if not found:
            raise FileNotFoundError(srt or video)
        self.docs = [Doc.load(lang, p) for lang, p in found]
        self.source = source
        first = next((i for i, d in enumerate(self.docs) if srt is not None and d.path == srt), 0)
        self.cur, self.sel = first, 0
        self.media = Media(self.video)
        self.back = "setup"   # the view to go back to
        self.remake: set = set()   # languages the run's video was made from (the save dialog offers to remake it)

    @property
    def doc(self) -> Doc:
        return self.docs[self.cur]

    @property
    def dirty(self) -> bool:
        return any(d.dirty for d in self.docs)

    def source_doc(self) -> Doc | None:
        return next((d for d in self.docs if self.source is not None and d.lang == self.source), None)

    def close(self):
        self.media.close()

# --- Entry points used by gui.App ----------------------------------------------------------------


def is_open(app) -> bool:
    return getattr(app, "_editor_session", None) is not None


def open_in(app, srt: Path | None = None, video: Path | None = None, source: Language | None = None,
            remake: set | None = None):
    """Open the editor in the main window for srt (or the subtitles of video), going back to the current view on
    close. remake: the languages whose subtitles the run's video was made from (burned in, or the dub's text); after
    saving one of them, the save dialog offers to make the video again."""
    if is_open(app) or getattr(app, "_running", False):
        return
    try:
        session = Session(srt, video, source)
    except Exception:
        log.exception("editor: could not open %s / %s", srt, video)
        if srt is not None:
            app._open(srt)   # still readable in another program
        return
    session.back = getattr(app, "view", "setup")
    session.remake = set(remake or ())
    app._editor_session = session
    show(app)


def show(app):
    """Build the editor widget around the open session (again, after a theme or language switch)."""
    session = app._editor_session
    ed = app._editor = SubtitleEditor(app.left, app, session, on_close=lambda: close(app))
    ed.grid(row=0, column=0, sticky="nsew")
    for frame in app.views.values():
        frame.grid_remove()
    app.claude_wrap.grid_remove()   # nothing for the Claude card meanwhile: the editor gets the whole width
    app.left.grid_configure(columnspan=2, padx=0)
    app.view = "editor"


def close(app, ask: bool = True) -> bool:
    """Close the editor (Annullér/Luk, or the main window closing). With unsaved changes it asks whether to save
    them first (Yes, No: throw them away, Cancel: stay). False when the user chose to stay."""
    session = getattr(app, "_editor_session", None)
    if session is None:
        return True
    ed = getattr(app, "_editor", None)
    if ask and ed is not None:
        ed.commit()
    if ask and session.dirty and ed is not None:
        from .gui import boxes
        answer = boxes.askyesnocancel(t("editor.title"), t("editor.unsaved"), parent=app._owner())
        if answer is None:
            return False
        if answer and not ed.save(dialog=False):
            return False
    session.close()
    app._editor_session = None
    if ed is not None:
        try:
            ed.destroy()
        except tk.TclError:
            pass
    app._editor = None
    app._show(session.back if session.back in app.views else "setup")
    app._refresh()
    return True


def may_close_app(app) -> bool:
    """Called when the main window closes: unsaved edits are offered to be saved first."""
    return close(app)


def file_menu(app, path: Path, event):
    """The right-click menu of a .srt row in the done view's files list."""
    m = tk.Menu(app.root, tearoff=False, background=P.card, foreground=P.text, activebackground=P.selection_bg,
                activeforeground=P.selection_text, borderwidth=1)
    m.add_command(label=t("editor.open_button"), command=lambda: app.open_editor(path))
    m.add_command(label=t("editor.open_external"), command=lambda: app._open(path))
    m.add_command(label=t("gui.show_in_folder"), command=lambda: app._show_in_folder(path))
    try:
        m.tk_popup(event.x_root, event.y_root)
    finally:
        m.grab_release()


def row_button(row, command) -> ttk.Button:
    """The small "Ret" button (30 px, edit icon) of a .srt row in the done view's files list; the row shows it while
    it is under the mouse or has focus."""
    k = 2 if IMG.two else 1
    style = ttk.Style(row)
    style.configure("Row.TButton", padding=(8 * k, 2 * k, 11 * k, 5 * k), background=P.picker_hover_bg)
    b = skin.IconButton(row, "edit", text=t("editor.row_edit"), style="Row.TButton", command=command,
                        takefocus=False)
    return b


def fmt_time(ms: int) -> str:
    """0:06,25 (the UI language's decimal sign; hours only when there are any)."""
    s = scrub.fmt_fine(ms)
    return s if i18n.current() == "en" else s.replace(".", ",")


def fmt_seconds(ms: int, decimals: int = 1) -> str:
    s = f"{ms / 1000:.{decimals}f}"
    return s if i18n.current() == "en" else s.replace(".", ",")


def plural_t(key: str, n: int, **kw) -> str:
    return t(key + ("_one" if n == 1 else ""), n=n, **kw)


def chars_per_second(c: Cue) -> float:
    """How fast a line must be read: its characters (spaces and punctuation too) per second on screen."""
    return len(flat(c.text)) / max(0.001, (c.end - c.start) / 1000)


def is_joining(wrong: str, right: str) -> bool:
    """A correction that only splits or joins whole words ("tids planen" -> "tidsplanen"): not ticked by default."""
    return wrong.replace(" ", "").replace("-", "").lower() == right.replace(" ", "").replace("-", "").lower()

# --- The editor --------------------------------------------------------------------------------
# Layout and sizes: assets/ui/README.md "1. Subtitle editor", mockups design/mockups/*/editor-*.png.

CPS_WARN = 20          # over this many characters a second a line is hard to read in time
ROW_MIN = 38           # the list's rows, design px
NUM_X, TIME_X, TEXT_X = 10, 42, 202
WARN_W = 96            # room at a row's end for "23 tegn/s"
VIDEO_W, VIDEO_H = 620, 349
TRACK_RULER, TRACK_H, BOX_H = 24, 150, 38


class DocPicker(_Picker):
    """The languages made for the video, as the header's picker: DA SV NO in the pixel font."""

    def __init__(self, parent, editor: "SubtitleEditor"):
        super().__init__(parent)
        self.editor = editor
        for n, d in enumerate(editor.s.docs):
            code = d.lang.code if d.lang else "?"
            self.add(str(n), lambda d=d: d.lang.name if d.lang else d.path.name, text=code.upper())
        self.refresh(str(editor.s.cur))

    def pick(self, value: str):
        self.editor.switch_lang(int(value))
        self.refresh(str(self.editor.s.cur))


class LineList(ttk.Frame):
    """The lines as rows on a Canvas (only the rows in view are drawn, so a long webinar scrolls smoothly): the number
    in the pixel font, "0:04,90 - 0:06,20" in VT323 and the wrapped text; a line too fast to read gets a warning at
    its end. The chosen row is highlighted and its text is a field edited in place (editor.field)."""

    def __init__(self, parent, editor: "SubtitleEditor"):
        super().__init__(parent)
        self.ed = editor
        k, kd = editor.k, editor.kd
        self.k = k
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.f_text = tkfont.nametofont("SunValleyBodyFont")
        self.f_num = (PIXEL_FONT, -10 * kd)
        self.f_time = (TERMINAL_FONT, -20 * kd)
        self.f_warn = ("Segoe UI", -int(12 * k))
        self.f_head = ("Segoe UI", -int(13 * k))
        head = tk.Frame(self, background=P.card, height=int(26 * k))
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        for x, key in ((NUM_X, None), (TIME_X, "editor.col_time"), (TEXT_X, "editor.col_text")):
            tk.Label(head, text=t(key) if key else "#", background=P.card, foreground=P.hint, font=self.f_head,
                     borderwidth=0, padx=0).place(x=int(x * k), y=0)
        self.c = tk.Canvas(self, background=P.card, highlightthickness=0, borderwidth=0, takefocus=1)
        self.c.grid(row=1, column=0, sticky="nsew")
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.sb.grid(row=1, column=1, sticky="ns")
        self.c.configure(yscrollcommand=self.sb.set)
        self.foot = tk.Label(self, text="", background=P.card, foreground=P.hint, font=self.f_head, anchor="w",
                             borderwidth=0, padx=int(NUM_X * k))
        self.foot.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(int(10 * k), 0))
        self._wrap_cache: dict = {}
        self.tops: list[int] = []
        self.heights: list[int] = []
        self.c.bind("<Configure>", lambda e: self.layout())
        self.c.bind("<Button-1>", self._click)
        self.c.bind("<MouseWheel>", lambda e: self._wheel(e))
        self.c.bind("<Up>", lambda e: self.ed.select(self.ed.s.sel - 1, focus=False))
        self.c.bind("<Down>", lambda e: self.ed.select(self.ed.s.sel + 1, focus=False))
        self.c.bind("<Return>", lambda e: self.ed.field.focus_set())

    # Geometry

    def _text_w(self) -> int:
        return max(120, self.c.winfo_width() - int((TEXT_X + WARN_W + 8) * self.k))

    def _lines(self, text: str, width: int) -> int:
        """How many lines text wraps to in width (as the Canvas wraps it: at spaces, each paragraph apart)."""
        key = (text, width)
        if key not in self._wrap_cache:
            n = 0
            for para in (text or "-").split("\n"):
                line, count = "", 1
                for word in para.split():
                    trial = f"{line} {word}" if line else word
                    if line and self.f_text.measure(trial) > width:
                        count, line = count + 1, word
                    else:
                        line = trial
                n += count
            if len(self._wrap_cache) > 5000:
                self._wrap_cache.clear()
            self._wrap_cache[key] = n
        return self._wrap_cache[key]

    def _height(self, c: Cue) -> int:
        lines = self._lines(c.text, self._text_w() - int(16 * self.k))
        return max(int(ROW_MIN * self.k), lines * self.f_text.metrics("linespace") + int(18 * self.k) + 4 * self.ed.kd)

    def layout(self):
        """Row heights and tops for every line (after a resize, an edit that changes a row's height, or a split)."""
        cues = self.ed.s.doc.cues
        self.heights = [self._height(c) for c in cues]
        self.tops, y = [], 0
        for h in self.heights:
            self.tops.append(y)
            y += h
        self.c.configure(scrollregion=(0, 0, self.c.winfo_width(), max(y, 1)))
        self.draw()

    def relayout_row(self, i: int):
        cues = self.ed.s.doc.cues
        if len(self.heights) != len(cues) or 0 <= i < len(cues) and self._height(cues[i]) != self.heights[i]:
            self.layout()
        else:
            self.draw()

    def row_at(self, y: float) -> int:
        i = bisect.bisect_right(self.tops, y) - 1
        return i if 0 <= i < len(self.tops) else -1

    def see(self, i: int):
        if not (0 <= i < len(self.tops)):
            return
        total = self.tops[-1] + self.heights[-1]
        top, bottom = self.c.canvasy(0), self.c.canvasy(self.c.winfo_height())
        y0, y1 = self.tops[i], self.tops[i] + self.heights[i]
        if y0 < top:
            self.c.yview_moveto(y0 / max(1, total))
        elif y1 > bottom:
            self.c.yview_moveto((y1 - self.c.winfo_height()) / max(1, total))
        self.draw()

    def _yview(self, *args):
        self.c.yview(*args)
        self.draw()

    def _wheel(self, e):
        self.c.yview_scroll(int(-e.delta / 40), "units")
        self.draw()

    def _click(self, e):
        i = self.row_at(self.c.canvasy(e.y))
        if i >= 0:
            self.ed.select(i)

    # Drawing

    def draw(self):
        c, k, kd = self.c, self.k, self.ed.kd
        cues = self.ed.s.doc.cues
        if len(self.tops) != len(cues):   # lines were added or removed since the last layout
            self.layout()
            return
        c.delete("row")
        w = c.winfo_width()
        top, bottom = c.canvasy(0), c.canvasy(c.winfo_height())
        i = max(0, self.row_at(top))
        sel = self.ed.s.sel
        tw = self._text_w() - int(16 * k)
        field_shown = False
        while 0 <= i < len(cues) and self.tops[i] < bottom:
            q, y, h = cues[i], self.tops[i], self.heights[i]
            chosen = i == sel
            fg = P.picker_selected_text if chosen else P.text
            if chosen:
                c.create_rectangle(0, y, w, y + h, fill=P.picker_selected_bg, outline="", tags="row")
            mid = y + h / 2
            c.create_text(int(NUM_X * k), mid, text=str(i + 1), anchor="w", font=self.f_num,
                          fill=fg if chosen else P.hint, tags="row")
            c.create_text(int(TIME_X * k), mid, text=f"{fmt_time(q.start)} - {fmt_time(q.end)}", anchor="w",
                          font=self.f_time, fill=fg, tags="row")
            if chosen:
                self.ed.place_field(c, int(TEXT_X * k) - int(8 * k), y + int(5 * k), tw + int(16 * k), h - int(10 * k))
                field_shown = True
            else:
                c.create_text(int(TEXT_X * k), mid, text=q.text or "-", anchor="w", font=self.f_text, fill=fg,
                              width=tw, tags="row")
            cps = chars_per_second(q)
            if cps > CPS_WARN:
                x = w - int((WARN_W - 6) * k)
                s = 10 * kd // 2 * 2
                c.create_rectangle(x, mid - s / 2, x + s, mid + s / 2, fill=P.warning, outline="", tags="row")
                c.create_text(x + s + int(6 * k), mid, text=t("editor.cps", n=round(cps)), anchor="w",
                              font=self.f_warn, fill=P.picker_selected_text if chosen else P.warning, tags="row")
            i += 1
        if not field_shown:
            self.ed.hide_field(c)
        fast = sum(1 for q in cues if chars_per_second(q) > CPS_WARN)
        foot = plural_t("editor.footer", len(cues))
        if fast:
            foot += "  ·  " + plural_t("editor.footer_fast", fast, cps=CPS_WARN)
        self.foot.configure(text=foot)


class SubtitleEditor(ttk.Frame):
    def __init__(self, master, app, session: Session, on_close):
        super().__init__(master)
        self.app, self.s, self.on_close = app, session, on_close
        self.k = app.scale
        self.kd = 2 if IMG.two else 1
        self.media = session.media
        self._drag: dict | None = None
        self._drag_frame_t = self._drag_sound_t = 0.0
        self._img = None
        self._typing = False
        self._field_window = None
        self._field_for = -1   # the line whose text the field holds (none until the first select)
        self._want_ms: int | None = None   # the time of the picture asked for last (the playhead)
        self.v0, self.v1 = 0, VIEW_MIN_MS
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()
        skin.skin_cards(self)
        for w, colour in self._own_colours:   # skin_cards gives plain tk widgets in a card the card's colour
            w.configure(background=colour)
        self.after_idle(lambda: self.select(min(max(session.sel, 0), len(self.s.doc.cues) - 1), focus=False))
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after(100, self._poll)

    # --- Layout ---------------------------------------------------------------------------

    def _build(self):
        k, kd = self.k, self.kd
        self._own_colours = []
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=t("editor.title").upper(), style="Section.TLabel", foreground=P.heading,
                  font=(PIXEL_FONT, -16 * kd)).grid(row=0, column=0, sticky="w")
        self.picker = DocPicker(head, self)
        self.picker.grid(row=0, column=1, sticky="e", padx=(0, int(14 * k)))
        if len(self.s.docs) < 2:
            self.picker.grid_remove()
        self.b_close = ttk.Button(head, text=t("editor.cancel"), command=self.on_close)
        self.b_close.grid(row=0, column=2, sticky="e", padx=(0, int(14 * k)))
        self.b_save = skin.IconButton(head, "file-subtitles", text=t("editor.save"), style="Accent.TButton",
                                      command=self.save)
        self.b_save.grid(row=0, column=3, sticky="e")
        sub = ttk.Frame(self)
        sub.grid(row=1, column=0, sticky="ew", pady=(int(10 * k), int(14 * k)))
        self.l_file = ttk.Label(sub, text="", font="SunValleyBodyStrongFont")
        self.l_file.pack(side="left")
        hint = t("editor.hint") + ("" if self.s.video else "  " + t("editor.no_video"))
        ttk.Label(sub, text=hint, style="Hint.TLabel").pack(side="left", padx=(int(6 * k), 0))

        mid = ttk.Frame(self)
        mid.grid(row=2, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        lc = skin.Card(mid, padding=(int(14 * k), int(16 * k), int(18 * k), int(18 * k)))
        lc.grid(row=0, column=0, sticky="nsew")
        lc.columnconfigure(0, weight=1)
        lc.rowconfigure(0, weight=1)
        self.lines = LineList(lc, self)
        self.lines.grid(row=0, column=0, sticky="nsew")
        self.field = tk.Text(self.lines.c, wrap="word", undo=False, font="SunValleyBodyFont", height=1,
                             background=P.input_bg, foreground=P.text, insertbackground=P.text,
                             selectbackground=P.selection_bg, selectforeground=P.selection_text, borderwidth=0,
                             highlightthickness=2 * kd, highlightcolor=P.focus, highlightbackground=P.input_border,
                             padx=int(6 * k), pady=int(4 * k))
        self._field_keys(self.field)

        # Right column (628 px): the video with the line as a subtitle, the play row, the timeline and the tools
        right = ttk.Frame(mid, width=int(628 * k))
        right.grid(row=0, column=1, sticky="ns", padx=(int(28 * k), 0))
        vw, vh = int(VIDEO_W * k), int(VIDEO_H * k)
        self.video = tk.Canvas(right, width=vw, height=vh, background="#0b0320", highlightthickness=0, borderwidth=0)
        self.video.grid(row=0, column=0, sticky="w")
        self._own_colours.append((self.video, "#0b0320"))
        row = ttk.Frame(right)
        row.grid(row=1, column=0, sticky="ew", pady=(int(12 * k), int(12 * k)))
        row.columnconfigure(2, weight=1)
        self.b_play = skin.IconButton(row, "play", width=0, command=self.play_line)
        self.b_play.grid(row=0, column=0, sticky="w")
        if not self.s.video:
            self.b_play.state(["disabled"])
        self.l_time = ttk.Label(row, text="", font=(TERMINAL_FONT, -22 * kd))
        self.l_time.grid(row=0, column=1, sticky="w", padx=(int(14 * k), 0))
        self.l_line = ttk.Label(row, text="", style="Hint.TLabel")
        self.l_line.grid(row=0, column=2, sticky="e")
        tl = ttk.Frame(right, style="Timeline.TFrame", padding=(6 * kd, 6 * kd, 10 * kd, 10 * kd))
        tl.grid(row=2, column=0, sticky="ew")
        tl.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(tl, width=vw - 16 * kd, height=int((TRACK_RULER + TRACK_H) * k) + 8 * kd,
                                background=P.timeline_bg, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="ew")
        c = self.canvas
        c.bind("<Configure>", lambda e: self._redraw())
        c.bind("<Motion>", self._on_motion)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<MouseWheel>", lambda e: self._pan(-e.delta / 120 * 0.2))
        tools = ttk.Frame(right)
        tools.grid(row=3, column=0, sticky="w", pady=(int(14 * k), 0))
        self.b_split = skin.IconButton(tools, "split", text=t("editor.split"), command=lambda: self.split())
        self.b_split.grid(row=0, column=0)
        self.b_merge = skin.IconButton(tools, "merge", text=t("editor.merge"), command=self.merge)
        self.b_merge.grid(row=0, column=1, padx=(int(12 * k), 0))
        self.b_delete = skin.IconButton(tools, "trash", width=0, command=self.delete)
        self.b_delete.grid(row=0, column=2, padx=(int(12 * k), 0))
        Tooltip(self.b_delete, t("editor.delete"))
        keys = ttk.Label(right, text=t("editor.keys"), style="Hint.TLabel", justify="left")
        keys.grid(row=4, column=0, sticky="ew", pady=(int(12 * k), 0))
        keys.configure(wraplength=vw)
        for seq, fn in (("<Control-s>", self._key_save), ("<space>", self._key_space), ("<Control-z>", self._key_undo)):
            self.bind_all(seq, fn, add="+")

    def _field_keys(self, w: tk.Text):
        """Enter: on to the next line; Shift+Enter: a new line in the text; Ctrl+Enter: split at the cursor;
        Up/Down on the field's first/last line: the line before/after; Alt(+Shift)+Left/Right: the start (end)
        100 ms earlier/later; Esc: the line as it was when it was chosen."""
        w.bind("<KeyRelease>", lambda e: self._text_changed())
        w.bind("<Return>", lambda e: (self.select(self.s.sel + 1), "break")[1])
        w.bind("<Shift-Return>", lambda e: None)
        w.bind("<Control-Return>", lambda e: (self.split(), "break")[1])
        w.bind("<Up>", lambda e: (self.select(self.s.sel - 1), "break")[1] if w.index("insert").startswith("1.") else None)
        w.bind("<Down>", lambda e: (self.select(self.s.sel + 1), "break")[1]
               if int(w.index("insert").split(".")[0]) == int(w.index("end-1c").split(".")[0]) else None)
        for key, side, delta in (("<Alt-Left>", "start", -NUDGE_MS), ("<Alt-Right>", "start", NUDGE_MS),
                                 ("<Alt-Shift-Left>", "end", -NUDGE_MS), ("<Alt-Shift-Right>", "end", NUDGE_MS)):
            w.bind(key, lambda e, s=side, d=delta: (self.nudge(s, d), "break")[1])
        w.bind("<Escape>", lambda e: (self._revert_line(), "break")[1])
        w.bind("<FocusOut>", lambda e: self.commit())

    def place_field(self, canvas: tk.Canvas, x: int, y: int, w: int, h: int):
        if self._field_window is None:
            self._field_window = canvas.create_window(x, y, window=self.field, anchor="nw", width=w, height=h)
        else:
            canvas.coords(self._field_window, x, y)
            canvas.itemconfigure(self._field_window, width=w, height=h, state="normal")

    def hide_field(self, canvas: tk.Canvas):
        if self._field_window is not None:
            canvas.itemconfigure(self._field_window, state="hidden")

    # --- Choosing a line ------------------------------------------------------------------

    def select(self, i: int, focus: bool = True, show_frame: bool = True):
        cues = self.s.doc.cues
        if not cues:
            self.s.sel, self._field_for = -1, -1
            self.lines.layout()
            self._show_line_info()
            return
        if not (0 <= i < len(cues)):
            return
        self.commit()
        self.s.sel = i
        self._typing = False
        self._loaded = cues[i].text
        self.field.delete("1.0", "end")
        self.field.insert("1.0", cues[i].text)
        self._field_for = i
        if not self.lines.tops or len(self.lines.tops) != len(cues):
            self.lines.layout()
        self.lines.see(i)
        if focus:
            self.field.focus_set()
            self.field.mark_set("insert", "end-1c")
        self.b_merge.state(["!disabled"] if i + 1 < len(cues) else ["disabled"])
        self._view_line(cues[i])
        self._show_line_info()
        if show_frame:
            self._picture(cues[i].start)
        else:
            self._draw_video()

    def _show_line_info(self):
        cues, i = self.s.doc.cues, self.s.sel
        self.l_file.configure(text=self.s.doc.path.name)
        if 0 <= i < len(cues):
            q = cues[i]
            self.l_line.configure(text=t("editor.line_info", n=i + 1, start=fmt_time(q.start), end=fmt_time(q.end),
                                         s=fmt_seconds(q.end - q.start)))
        else:
            self.l_line.configure(text="")
        self._draw_video()

    # --- Editing --------------------------------------------------------------------------

    def _text_changed(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)) or i != self._field_for:
            return
        text = self.field.get("1.0", "end-1c")
        if text != cues[i].text:
            if not self._typing:   # one undo step per line visit, not per key
                self.s.doc.push_undo(i)
                self._typing = True
            cues[i].text = text
            self.lines.relayout_row(i)
            self._changed()

    def commit(self):
        """Take what is in the field (it is also taken on every key)."""
        try:
            self._text_changed()
        except tk.TclError:
            pass

    def _revert_line(self):
        cues, i = self.s.doc.cues, self.s.sel
        if 0 <= i < len(cues) and cues[i].text != self._loaded:
            cues[i].text = self._loaded
            self.field.delete("1.0", "end")
            self.field.insert("1.0", self._loaded)
            self.lines.relayout_row(i)
            self._changed()

    def _changed(self):
        """After any change: the Annullér/Luk wording, the line's info, the subtitle in the video, the timeline."""
        self.b_close.configure(text=t("editor.cancel") if self.s.dirty else t("editor.close"))
        self._show_line_info()
        self._redraw()

    def split(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        pos = len(self.field.get("1.0", "insert"))
        self.commit()
        if pos <= 0 or pos >= len(cues[i].text):
            self._say(t("editor.split_where"))
            return
        parts = split_cue(cues[i], pos)
        if parts is None:
            self._say(t("editor.split_short"))
            return
        self.s.doc.push_undo(i)
        cues[i:i + 1] = list(parts)
        self._typing = True   # the next line's field must not take the old text back
        self.lines.layout()
        self._typing = False
        self.s.sel = -1
        self.select(i + 1)
        self.field.mark_set("insert", "1.0")
        self._changed()

    def merge(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues) - 1):
            return
        self.commit()
        self.s.doc.push_undo(i)
        cues[i:i + 2] = [merge_cues(cues[i], cues[i + 1])]
        self.lines.layout()
        self.s.sel = -1
        self.select(i)
        self._changed()

    def delete(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        self.commit()
        self.s.doc.push_undo(i)
        del cues[i]
        self.lines.layout()
        self.s.sel = -1
        if cues:
            self.select(min(i, len(cues) - 1))
        else:
            self.select(0)
        self._changed()

    def undo(self):
        doc = self.s.doc
        self.commit()
        if not doc.undo:
            return
        doc.cues, sel = doc.undo.pop()
        self._typing = False
        self.lines.layout()
        self.s.sel = -1
        self.select(min(sel, len(doc.cues) - 1), focus=False)
        self._changed()

    def nudge(self, side: str, delta: int):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        self.commit()
        old = getattr(cues[i], side)
        self.s.doc.push_undo(i)
        new = set_edge(cues, i, side, old + delta, self.media.duration)
        if new == old:
            self.s.doc.undo.pop()
            self._say(t("editor.edge_blocked"))
            return
        self.lines.draw()
        self._changed()
        self._picture(new)

    def switch_lang(self, n: int):
        if n < 0 or n >= len(self.s.docs) or n == self.s.cur:
            return
        self.commit()
        # The same moment in the other language: the line that covers the chosen line's start
        at = self.s.doc.cues[self.s.sel].start if 0 <= self.s.sel < len(self.s.doc.cues) else 0
        self.s.cur = n
        cues = self.s.doc.cues
        self.lines.layout()
        self.s.sel = -1
        self.select(next((j for j, c in enumerate(cues) if c.end > at), len(cues) - 1), focus=False)
        self.picker.refresh(str(n))
        self._changed()

    def _say(self, text: str):
        """A short note in the hint under the title (there is no status bar)."""
        from .gui import boxes
        boxes.showinfo(t("editor.title"), text, parent=self.app._owner())

    # --- Keys -----------------------------------------------------------------------------

    def _mine(self, e) -> bool:
        """A key pressed in the main window while the editor is shown (not in a dialog over it)."""
        try:
            return bool(self.winfo_exists() and self.winfo_ismapped()
                        and e.widget.winfo_toplevel() is self.winfo_toplevel())
        except (tk.TclError, AttributeError, KeyError):   # e.widget can be a name of a menu's clone
            return False

    def _key_save(self, e=None):
        if self._mine(e):
            self.save()
            return "break"

    def _key_undo(self, e=None):
        if self._mine(e):
            self.undo()
            return "break"

    def _key_space(self, e=None):
        """Space outside the text plays the line (in a field or on a button it does what it always does)."""
        if not self._mine(e):
            return None
        if e.widget.winfo_class() in ("Text", "Entry", "TEntry", "TCombobox", "TButton", "TCheckbutton", "TLabel"):
            return None
        self.play_line()
        return "break"

    # --- Saving ---------------------------------------------------------------------------

    def save(self, dialog: bool = True) -> bool:
        """Save every changed language; then the dialog: where it was saved, the words corrected in the spoken
        language's text for the word list, and (when the run's video was made from these subtitles) making it again."""
        self.commit()
        dirty = [d for d in self.s.docs if d.dirty]
        if not dirty:
            if dialog:
                self._say(t("editor.nothing_to_save"))
            return True
        src = self.s.source_doc()
        before = copy.deepcopy(src.saved) if src in dirty else None
        notes, saved = [], []
        for d in dirty:
            try:
                r = save_doc(d, self.media.duration)
            except OSError as e:
                log.exception("editor: save failed")
                from .gui import boxes
                boxes.showerror(t("editor.title"), t("editor.save_failed", file=d.path.name, error=e),
                                parent=self.app._owner())
                return False
            saved.append(d)
            if r.backup:
                notes.append(t("editor.backup", backup=r.backup.name))
            if r.moved:
                notes.append(plural_t("editor.moved", r.moved))
            if r.dropped:
                notes.append(plural_t("editor.dropped", r.dropped))
        self.lines.layout()
        self.s.sel = min(self.s.sel, len(self.s.doc.cues) - 1)
        self._loaded = self.s.doc.cues[self.s.sel].text if self.s.doc.cues else ""
        self._changed()
        if not dialog:
            return True
        pairs = []
        if before is not None:
            from . import claude
            known = {w.lower() for w, _ in claude.word_rules()}
            pairs = word_changes(before, src.cues, known)
        remake = any(d.lang in self.s.remake for d in saved)
        chosen, again = save_dialog(self, [d.path.name for d in saved], notes, pairs, remake)
        if chosen:
            from . import claude
            claude.add_words(chosen)
        if again:
            app = self.app
            close(app, ask=False)
            app.root.after_idle(app._start)
        return True

    # --- The picture, the subtitle on it and the sound ------------------------------------------

    def _picture(self, ms: int):
        self._want_ms = ms
        self._time_label()
        if self.s.video:
            self.media.frame(ms, int(VIDEO_W * self.k) - 4 * self.kd, int(VIDEO_H * self.k) - 4 * self.kd)
        self._redraw()

    def _time_label(self):
        ms = self._want_ms or 0
        total = self.media.duration
        self.l_time.configure(text=fmt_time(ms) + (f" / {cutting.fmt_clock(total)}" if total else ""))

    def _draw_video(self):
        """The frame, a 2 px border with the small pixel corners, and the chosen line as a subtitle."""
        c, k, kd = self.video, self.k, self.kd
        try:
            c.delete("all")
        except tk.TclError:
            return
        w, h = int(VIDEO_W * k), int(VIDEO_H * k)
        if self._img is not None:
            c.create_image(w // 2, h // 2, image=self._img)
        elif self.s.video:
            c.create_text(w // 2, h // 2, text="...", fill="#9c8cc9", font=(PIXEL_FONT, -10 * kd))
        b = 2 * kd
        c.create_rectangle(b / 2, b / 2, w - b / 2, h - b / 2, outline=P.card_border, width=b)
        for x, y in ((0, 0), (w - b, 0), (0, h - b), (w - b, h - b)):   # the corners cut by one pixel step
            c.create_rectangle(x, y, x + b, y + b, fill=P.window, outline="")
        cues, i = self.s.doc.cues, self.s.sel
        if 0 <= i < len(cues) and cues[i].text.strip():
            font = ("Segoe UI", -int(22 * k))
            tid = c.create_text(w // 2, h - int(28 * k), text=clean_text(cues[i].text), fill="#ffffff", font=font,
                                justify="center", anchor="s", width=w - int(60 * k))
            x0, y0, x1, y1 = c.bbox(tid)
            box = c.create_rectangle(x0 - int(10 * k), y0 - int(4 * k), x1 + int(10 * k), y1 + int(4 * k),
                                     fill="#0b0320", outline="")
            c.tag_lower(box, tid)

    def _key_play(self, e=None):
        self.play_line()

    def play_line(self):
        cues, i = self.s.doc.cues, self.s.sel
        if self.media.playing:
            self.media.stop()
            self.b_play.icon = "play"
            self.b_play._set_icon()
            return
        if not (0 <= i < len(cues)) or self.media.wav is None:
            return
        c = cues[i]
        self.media.play(c.start, min(MAX_PLAY_MS, c.end - c.start))
        self._picture(c.start)
        self.b_play.icon = "stop"
        self.b_play._set_icon()
        self.after(200, self._play_tick)

    def _play_tick(self):
        if not self.winfo_exists():
            return
        if self.media.playing:
            self.after(200, self._play_tick)
        else:
            self.b_play.icon = "play"
            self.b_play._set_icon()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                ev = self.media.events.get_nowait()
                if ev[0] == "frame":
                    _, ms, path = ev
                    if path is not None:
                        try:
                            self._img = tk.PhotoImage(master=self, file=str(path))
                        except tk.TclError:
                            pass
                        self._draw_video()
                elif ev[0] in ("duration", "sound"):
                    self._time_label()
                    self._redraw()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    # --- The timeline -----------------------------------------------------------------------

    def _m(self) -> int:
        return 8 * self.kd

    def _width(self) -> int:
        return max(100, self.canvas.winfo_width() - 2 * self._m())

    def _dur(self) -> int:
        cues = self.s.doc.cues
        return self.media.duration or ((cues[-1].end + 2000) if cues else VIEW_MIN_MS)

    def x_of(self, ms: float) -> float:
        return self._m() + (ms - self.v0) / max(1, self.v1 - self.v0) * self._width()

    def t_of(self, x: float) -> int:
        return int(min(max(self.v0 + (x - self._m()) / self._width() * (self.v1 - self.v0), 0), self._dur()))

    def _view_line(self, c: Cue):
        """Show the line with some of its neighbours: at least VIEW_MIN_MS, the line in the middle."""
        if self._drag:
            return
        span = max(VIEW_MIN_MS * 0.8, 3 * (c.end - c.start))
        mid = (c.start + c.end) / 2
        self._set_view(mid - span / 2, mid + span / 2)

    def _set_view(self, v0: float, v1: float):
        dur = self._dur()
        span = min(max(VIEW_MIN_MS / 4, v1 - v0), max(dur, VIEW_MIN_MS / 4))
        v0 = min(max(0, v0), max(0, dur - span))
        self.v0, self.v1 = int(v0), int(v0 + span)
        self._redraw()

    def _pan(self, frac: float):
        span = self.v1 - self.v0
        self._set_view(self.v0 + span * frac, self.v0 + span * (1 + frac))

    def _rows(self):
        """(ruler bottom, track top, box top, box bottom) in canvas px."""
        k, kd = self.k, self.kd
        ruler = int(TRACK_RULER * k)
        bottom = self.canvas.winfo_height() - 4 * kd
        return ruler, ruler + 2 * kd, bottom - int(BOX_H * k), bottom

    def _redraw(self):
        c = self.canvas
        try:
            if not c.winfo_exists():
                return
        except tk.TclError:
            return
        c.delete("all")
        k, kd, m = self.k, self.kd, self._m()
        w = c.winfo_width()
        ruler, top, box0, box1 = self._rows()
        env = self.media.env
        wave_mid = (top + box0) // 2 + int(6 * k)
        if env is not None and len(env):
            import numpy as np
            cols = max(2, int(self._width() / max(1, 2 * kd)))
            idx = np.clip(np.linspace(self.v0 / cutting.ENVELOPE_MS, self.v1 / cutting.ENVELOPE_MS,
                                      cols + 1)[:-1].astype(int), 0, len(env) - 1)
            amp = (box0 - top) / 2 + int(10 * k)
            level = np.clip((np.maximum.reduceat(env, idx) + 60) / 60, 0.04, 1.0) * amp
            xs = m + np.arange(cols) * self._width() / cols
            pts = [v for x, l in zip(xs, level) for v in (x, wave_mid - l)] + \
                  [v for x, l in zip(xs[::-1], level[::-1]) for v in (x, wave_mid + l)]
            if len(pts) >= 8:
                c.create_polygon(pts, fill=P.timeline_waveform, outline="")
        # The lines: 38 px boxes at the bottom of the track (card fill, 2 px border); the chosen one in
        # your_turn_card with a 3 px accent border and 8 x 26 drag handles
        num_font, text_font = (PIXEL_FONT, -8 * kd), ("Segoe UI", -int(12 * k))
        edge = P.heading if P.name == "light" else P.card_border
        sel = self.s.sel
        for i, q in enumerate(self.s.doc.cues):
            if q.end <= self.v0 or q.start >= self.v1 or i == sel:
                continue
            self._box(i, q, P.card, edge, 2 * kd, num_font, text_font)
        if 0 <= sel < len(self.s.doc.cues):
            q = self.s.doc.cues[sel]
            x0, x1 = self._box(sel, q, P.your_turn_card, P.accent, 3 * kd, num_font, text_font)
            ymid = (box0 + box1) // 2
            dragging = self._drag["side"] if self._drag else None
            for side, x in (("start", x0), ("end", x1)):
                hw, hh = (6 if dragging == side else 4) * kd, 13 * kd
                c.create_rectangle(x - hw, ymid - hh, x + hw, ymid + hh, fill=P.accent, outline=P.card_border
                                   if dragging == side else "", width=kd)
        # The ruler (24 px), over anything that reaches up there
        c.create_rectangle(0, 0, w, top, fill=P.timeline_bg, outline="")
        c.create_rectangle(m, ruler, w - m, top, fill=P.card_border, outline="")
        px_per_s = self._width() / max(0.001, (self.v1 - self.v0) / 1000)
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * px_per_s >= 56 * k), 600)
        sec = (self.v0 // 1000 // step) * step
        while sec * 1000 <= self.v1:
            x = round(self.x_of(sec * 1000))
            if m - 1 <= x <= w - m - 2 * kd:
                c.create_rectangle(x, 4 * kd, x + 2 * kd, ruler, fill=P.timeline_ruler, outline="")
                c.create_text(x + 5 * kd, ruler - 2 * kd, text=cutting.fmt_clock(sec * 1000), anchor="sw",
                              fill=P.timeline_ruler, font=(TERMINAL_FONT, -16 * kd))
            sec += step
        # The playhead: where the picture is from, 2 px with a 12 x 8 cap
        if self._want_ms is not None and self.v0 <= self._want_ms <= self.v1:
            x = round(self.x_of(self._want_ms))
            colour = P.progress_fill_current
            c.create_rectangle(x - kd, top, x + kd, box1, fill=colour, outline="")
            c.create_rectangle(x - 6 * kd, top, x + 6 * kd, top + 8 * kd, fill=colour, outline="")

    def _box(self, i, q, fill, border, width, num_font, text_font):
        c, kd = self.canvas, self.kd
        _, _, y0, y1 = self._rows()
        x0, x1 = self.x_of(q.start), max(self.x_of(q.end), self.x_of(q.start) + 2)
        c.create_rectangle(x0 + width / 2, y0 + width / 2, x1 - width / 2, y1 - width / 2, fill=fill, outline=border,
                           width=width)
        room = x1 - x0 - 12 * kd
        if room > 10 * kd:
            num_w = self._measure(str(i + 1), num_font) + 6 * kd
            c.create_text(x0 + 6 * kd, (y0 + y1) / 2, text=str(i + 1), anchor="w", fill=border, font=num_font)
            rest = room - num_w
            words = flat(q.text)
            if rest > 14 * kd and words:
                while words and self._measure(words, text_font) > rest:
                    words = words[:-1]
                c.create_text(x0 + 6 * kd + num_w, (y0 + y1) / 2, text=words, anchor="w", fill=P.text, font=text_font)
        return x0, x1

    def _measure(self, text: str, font) -> int:
        key = str(font)
        if not hasattr(self, "_fonts"):
            self._fonts = {}
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(root=self, font=font)
        return self._fonts[key].measure(text)

    def _hit(self, x: float, y: float):
        """('edge', side) on the chosen line's edge, ('line', i) on a line's box, or None (the track)."""
        cues, sel = self.s.doc.cues, self.s.sel
        if 0 <= sel < len(cues):
            q = cues[sel]
            for side, ms in (("start", q.start), ("end", q.end)):
                if abs(self.x_of(ms) - x) <= EDGE_PX * self.kd:
                    return ("edge", side)
        _, _, y0, _ = self._rows()
        if y < y0:
            return None
        ms = self.t_of(x)
        for i, q in enumerate(cues):
            if q.start <= ms <= q.end:
                return ("line", i)
        return None

    def _on_motion(self, e):
        if self._drag:
            return
        hit = self._hit(e.x, e.y)
        self.canvas.configure(cursor="sb_h_double_arrow" if hit and hit[0] == "edge" else
                              "hand2" if hit else "crosshair")

    def _on_press(self, e):
        self.commit()
        hit = self._hit(e.x, e.y)
        if hit and hit[0] == "edge":
            self.s.doc.push_undo(self.s.sel)
            self._drag = {"side": hit[1], "moved": False}
            self._redraw()
        elif hit:
            self.select(hit[1], focus=False)
        else:
            self._picture(self.t_of(e.x))

    def _on_drag(self, e):
        d = self._drag
        if not d:
            return
        i = self.s.sel
        ms = int(round(self.t_of(e.x) / 10) * 10)
        new = set_edge(self.s.doc.cues, i, d["side"], ms, self.media.duration)
        d["moved"] = True
        self.lines.draw()
        self._show_line_info()
        now = time.monotonic()
        if now - self._drag_frame_t >= DRAG_FRAME_MS / 1000:
            self._drag_frame_t = now
            self._picture(new)
        else:
            self._redraw()
        if now - self._drag_sound_t >= DRAG_SOUND_MS / 1000 and new != d.get("sound_at"):
            self._drag_sound_t, d["sound_at"] = now, new
            # The sound up to the end edge, or from the start edge: what the viewer hears at that moment
            self.media.play(new - scrub.SNIPPET_MS if d["side"] == "end" else new, scrub.SNIPPET_MS)

    def _on_release(self, e):
        d, self._drag = self._drag, None
        if not d:
            return
        if not d["moved"]:
            self.s.doc.undo.pop()
        else:
            self.media.stop()
            q = self.s.doc.cues[self.s.sel]
            self._picture(q.start if d["side"] == "start" else max(q.start, q.end - 40))
            self._changed()
        self._redraw()

    def _on_destroy(self, e):
        if e.widget is self:
            for seq in ("<Control-s>", "<space>", "<Control-z>"):
                try:
                    self.unbind_all(seq)
                except tk.TclError:
                    pass


def save_dialog(parent, files: list[str], notes: list[str], pairs: list[tuple[str, str]],
                remake: bool) -> tuple[list[tuple[str, str]], bool]:
    """After Gem (700 px wide): "Gemt" and where; the corrected words as check box rows (the old word struck through,
    an arrow, the new one; all ticked except splits and joins of whole words); "Lav videoen igen" when the run's video
    was made from these subtitles. Returns (the words for the word list, make the video again)."""
    from .gui import place_over
    k = parent.k if hasattr(parent, "k") else 1
    win = tk.Toplevel(parent)
    win.title(t("editor.title"))
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    wrap = int(640 * k)
    frame = ttk.Frame(win, padding=(int(30 * k), int(24 * k), int(30 * k), int(24 * k)))
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text=t("editor.saved_title"), font=("Segoe UI Semibold", -int(22 * k)),
              foreground=P.heading).grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text=t("editor.saved_in")).grid(row=1, column=0, sticky="w", pady=(int(10 * k), 0))
    ttk.Label(frame, text="\n".join(files), font="SunValleyBodyStrongFont", justify="left",
              wraplength=wrap).grid(row=2, column=0, sticky="w")
    if notes:
        ttk.Label(frame, text=" ".join(notes), style="Hint.TLabel", justify="left",
                  wraplength=wrap).grid(row=3, column=0, sticky="w", pady=(int(6 * k), 0))
    vars_: list = []
    n_var = tk.StringVar()
    if pairs:
        card = skin.Card(frame, text=t("editor.words_title"), padding=(int(18 * k), int(14 * k), int(22 * k),
                                                                         int(18 * k)))
        card.grid(row=4, column=0, sticky="ew", pady=(int(18 * k), 0))
        card.columnconfigure(3, weight=1)
        ttk.Label(card, text=t("editor.words_text"), style="Hint.TLabel", justify="left",
                  wraplength=wrap - int(40 * k)).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, int(10 * k)))
        struck = tkfont.Font(root=win, font=tkfont.nametofont("SunValleyBodyFont"))
        struck.configure(overstrike=1)
        for r, (wrong, right) in enumerate(pairs[:12], 1):
            v = tk.BooleanVar(value=not is_joining(wrong, right))
            v.trace_add("write", lambda *a: count())
            ttk.Checkbutton(card, variable=v).grid(row=r, column=0, sticky="w", pady=int(3 * k))
            ttk.Label(card, text=wrong, style="Hint.TLabel", font=struck).grid(row=r, column=1, sticky="w",
                                                                                padx=(int(4 * k), int(40 * k)))
            ttk.Label(card, text="→", style="Hint.TLabel").grid(row=r, column=2, sticky="w", padx=(0, int(20 * k)))
            ttk.Label(card, text=right, font="SunValleyBodyStrongFont").grid(row=r, column=3, sticky="w")
            vars_.append(((wrong, right), v))
    again = tk.BooleanVar(value=False)
    if remake:
        rm = ttk.Frame(frame)
        rm.grid(row=5, column=0, sticky="ew", pady=(int(18 * k), 0))
        ttk.Checkbutton(rm, variable=again, text=t("editor.remake_check")).grid(row=0, column=0, sticky="w")
        ttk.Label(rm, text=t("editor.remake_hint"), style="Hint.TLabel", justify="left",
                  wraplength=wrap - int(30 * k)).grid(row=1, column=0, sticky="w", padx=(int(30 * k), 0))
    result: dict = {"words": [], "again": False}

    def done(take_words: bool):
        result["words"] = [p for p, v in vars_ if v.get()] if take_words else []
        result["again"] = again.get()
        win.destroy()

    def count():
        n = sum(1 for _, v in vars_ if v.get())
        n_var.set(plural_t("editor.words_add", n))
        b_add.state(["!disabled"] if n else ["disabled"])
    btns = ttk.Frame(frame)
    btns.grid(row=6, column=0, sticky="e", pady=(int(22 * k), 0))
    if pairs:
        ttk.Button(btns, text=t("editor.words_skip"), command=lambda: done(False)).pack(side="left")
        b_add = ttk.Button(btns, textvariable=n_var, style="Accent.TButton", command=lambda: done(True))
        b_add.pack(side="left", padx=(int(12 * k), 0))
        count()
    else:
        ttk.Button(btns, text=t("editor.ok"), style="Accent.TButton", command=lambda: done(False)).pack(side="left")
    skin.dialog(win)
    win.update_idletasks()
    win.geometry(f"{max(int(700 * k), win.winfo_reqwidth())}x{win.winfo_reqheight()}")
    try:
        place_over(win, parent.winfo_toplevel())
    except Exception:
        pass
    win.bind("<Escape>", lambda e: done(False))
    win.protocol("WM_DELETE_WINDOW", lambda: done(False))
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)
    return result["words"], result["again"]
