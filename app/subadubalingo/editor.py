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

import copy
import difflib
import queue
import re
import shutil
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk

from . import cutting, i18n, scrub, skin, textstate
from .common import (FFMPEG, LANGUAGES, Cue, Language, Runner, find_srt, guess_srt_language, log, read_srt,
                     srt_names, write_srt)
from .i18n import t
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
PREVIEW_W, PREVIEW_H = 384, 216   # the picture, in design px (16:9)
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


def similar(wrong: str, right: str) -> bool:
    """Whether a correction looks like a misheard word (similar spelling) rather than another word: such pairs are
    ticked from the start in the dialog."""
    return difflib.SequenceMatcher(None, wrong.lower(), right.lower()).ratio() >= 0.5


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

    def frame(self, ms: int, width: int):
        """Ask for the picture at ms; arrives as ("frame", ms, path or None). Only the newest request is served."""
        if self.video:
            self._frames.put((int(ms), width))

    def _frame_work(self, item):
        ms, width = item
        ms = int(round(ms / 40) * 40)   # one frame at 25 fps: dragging back and forth reuses the files
        out = self.tmp / f"frame-{width}-{ms}.png"
        ok = out.exists() or cutting.frame_png(self.frame_runner, self.video, ms, out, width)
        self.events.put(("frame", item[0], out if ok else None))

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


def open_in(app, srt: Path | None = None, video: Path | None = None, source: Language | None = None):
    """Open the editor in the main window for srt (or the subtitles of video), going back to the current view on
    close."""
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
    """Close the editor (asking about unsaved changes); False when the user chose to stay."""
    session = getattr(app, "_editor_session", None)
    if session is None:
        return True
    ed = getattr(app, "_editor", None)
    if ask and session.dirty and ed is not None:
        from .gui import boxes
        answer = boxes.askyesnocancel(t("editor.title"), t("editor.unsaved"), parent=app._owner())
        if answer is None:
            return False
        if answer and not ed.save():
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


def fmt_time(ms: int) -> str:
    """0:06,25 (the UI language's decimal sign; hours only when there are any)."""
    s = scrub.fmt_fine(ms)
    return s if i18n.current() == "en" else s.replace(".", ",")


def fmt_seconds(ms: int) -> str:
    s = f"{ms / 1000:.2f}"
    return s if i18n.current() == "en" else s.replace(".", ",")


def plural_t(key: str, n: int, **kw) -> str:
    return t(key + ("_one" if n == 1 else ""), n=n, **kw)

# --- The editor --------------------------------------------------------------------------------


class SubtitleEditor(ttk.Frame):
    def __init__(self, master, app, session: Session, on_close):
        super().__init__(master)
        self.app, self.s, self.on_close = app, session, on_close
        self.k = k = app.scale
        self.kd = 2 if IMG.two else 1
        self.media = session.media
        self._overlay: tk.Text | None = None
        self._drag: dict | None = None
        self._drag_frame_t = self._drag_sound_t = 0.0
        self._frame_job = None
        self._img = None
        self._want_ms: int | None = None   # the picture asked for last
        self.v0, self.v1 = 0, VIEW_MIN_MS
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()
        self._fill_tree()
        skin.skin_cards(self)
        # skin_cards gives every plain tk widget in a card the card's colour; these two have their own
        self.pic.configure(background="#0b0320")
        self.text_box.configure(background=P.input_border)
        self._select(min(session.sel, len(self.s.doc.cues) - 1) if self.s.doc.cues else -1)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after(100, self._poll)

    # --- Layout ---------------------------------------------------------------------------

    def _build(self):
        k, kd = self.k, self.kd
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text=t("editor.title").upper(), style="Section.TLabel", foreground=P.heading,
                  font=(PIXEL_FONT, -16 * kd)).grid(row=0, column=0, sticky="w")
        langs = ttk.Frame(head)
        langs.grid(row=0, column=2, sticky="e")
        ttk.Label(langs, text=t("editor.language")).pack(side="left", padx=(0, int(8 * k)))
        self.lang_var = tk.StringVar()
        self.c_lang = ttk.Combobox(langs, textvariable=self.lang_var, state="readonly", width=16)
        self.c_lang.pack(side="left")
        self.c_lang.bind("<<ComboboxSelected>>", lambda e: self._switch_lang(self.c_lang.current()))
        self._lang_names()
        sub = ttk.Label(self, style="Hint.TLabel", justify="left", text=(
            (self.s.video.name if self.s.video else self.s.doc.path.name) + "  ·  " + t("editor.hint")
            + ("" if self.s.video else "  " + t("editor.no_video"))))
        sub.grid(row=1, column=0, sticky="ew", pady=(int(4 * k), int(10 * k)))
        self.app._wrap(sub, self, 10)

        mid = ttk.Frame(self)
        mid.grid(row=2, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        card_pad = (int(14 * k), int(14 * k), int(18 * k), int(18 * k))

        # The lines, in a card: #, start, end, text
        lc = skin.Card(mid, padding=card_pad)
        lc.grid(row=0, column=0, sticky="nsew")
        lc.columnconfigure(0, weight=1)
        lc.rowconfigure(0, weight=1)
        ttk.Style(self).configure("Editor.Treeview", rowheight=int(30 * k))
        cols = ("nr", "start", "end", "text")
        self.tree = ttk.Treeview(lc, columns=cols, show="headings", selectmode="browse", style="Editor.Treeview")
        for col, title, width, stretch in (("nr", "#", 46, False), ("start", t("editor.start"), 92, False),
                                           ("end", t("editor.end"), 92, False), ("text", t("editor.col_text"), 300, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * k), minwidth=int(width * k * 0.7), stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lc, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("edited", foreground=P.accent)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<Return>", lambda e: self._edit_inline())
        self.tree.bind("<F2>", lambda e: self._edit_inline())
        self.tree.bind("<Delete>", lambda e: self._delete())
        keys = ttk.Label(lc, text=t("editor.keys"), style="Hint.TLabel", justify="left")
        keys.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(int(10 * k), 0))
        self.app._wrap(keys, lc, int(40 * k))

        # Right column: the picture, then the chosen line (its text, times and actions)
        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew", padx=(int(20 * k), 0))
        right.columnconfigure(0, weight=1)
        pc = skin.Card(right, padding=card_pad)
        pc.grid(row=0, column=0, sticky="ew")
        pw, ph = int(PREVIEW_W * k), int(PREVIEW_H * k)
        self._blank = tk.PhotoImage(master=self, width=pw, height=ph)
        self.pic = tk.Label(pc, image=self._blank, text="...", compound="center", borderwidth=0, width=pw, height=ph,
                            background="#0b0320", foreground="#9c8cc9", font=(PIXEL_FONT, -10 * kd))
        self.pic.grid(row=0, column=0, columnspan=2, sticky="w")
        if not self.s.video:
            self.pic.configure(text="")
        cap = ttk.Frame(pc)
        cap.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(int(8 * k), 0))
        cap.columnconfigure(1, weight=1)
        self.pic_time = ttk.Label(cap, text="", font=(TERMINAL_FONT, -26 * kd), foreground=P.text)
        self.pic_time.grid(row=0, column=0, sticky="w")
        self.b_play = skin.IconButton(cap, "play", text=t("editor.play_line"), command=self._play_line)
        self.b_play.grid(row=0, column=2, sticky="e")
        if not self.s.video:
            self.b_play.state(["disabled"])

        ln = self.line_card = skin.Card(right, text=" ", padding=card_pad)
        ln.grid(row=1, column=0, sticky="nsew", pady=(int(16 * k), 0))
        ln.columnconfigure(0, weight=1)
        box = self.text_box = tk.Frame(ln, background=P.input_border, padx=max(1, kd), pady=max(1, kd))
        box.grid(row=0, column=0, sticky="ew")
        self.text = tk.Text(box, height=3, width=34, wrap="word", undo=False, font="SunValleyBodyFont",
                            background=P.input_bg, foreground=P.text, insertbackground=P.text,
                            selectbackground=P.selection_bg, selectforeground=P.selection_text, borderwidth=0,
                            highlightthickness=0, padx=int(8 * k), pady=int(6 * k))
        self.text.pack(fill="both", expand=True)
        self.text.bind("<FocusIn>", lambda e: box.configure(background=P.focus))
        self.text.bind("<FocusOut>", lambda e: (box.configure(background=P.input_border), self._commit_text()))
        self._text_keys(self.text, self._text_changed)

        times = ttk.Frame(ln)
        times.grid(row=1, column=0, sticky="w", pady=(int(12 * k), 0))
        self.t_labels = {}
        for col, side in enumerate(("start", "end")):
            f = ttk.Frame(times)
            f.grid(row=0, column=col, sticky="w", padx=(0, int(24 * k)))
            ttk.Label(f, text=t(f"editor.{side}"), style="Hint.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
            skin.IconButton(f, "minus", width=0, command=lambda s=side: self._nudge(s, -NUDGE_MS)).grid(row=1, column=0)
            lab = ttk.Label(f, text="", font=(TERMINAL_FONT, -24 * kd), width=9, anchor="center")
            lab.grid(row=1, column=1, padx=int(4 * k))
            skin.IconButton(f, "plus", width=0, command=lambda s=side: self._nudge(s, NUDGE_MS)).grid(row=1, column=2)
            self.t_labels[side] = lab
        self.t_len = ttk.Label(times, text="", style="Hint.TLabel")
        self.t_len.grid(row=0, column=2, sticky="sw", pady=(0, int(6 * k)))

        acts = ttk.Frame(ln)
        acts.grid(row=2, column=0, sticky="w", pady=(int(14 * k), 0))
        self.b_split = ttk.Button(acts, text=t("editor.split"), command=self._split)
        self.b_split.grid(row=0, column=0, sticky="w")
        self.b_merge = ttk.Button(acts, text=t("editor.merge"), command=self._merge)
        self.b_merge.grid(row=0, column=1, sticky="w", padx=(int(8 * k), 0))
        self.b_delete = ttk.Button(acts, text=t("editor.delete"), command=self._delete)
        self.b_delete.grid(row=1, column=0, sticky="w", pady=(int(8 * k), 0))
        self.b_undo = ttk.Button(acts, text=t("editor.undo"), command=self._undo)
        self.b_undo.grid(row=1, column=1, sticky="w", padx=(int(8 * k), 0), pady=(int(8 * k), 0))

        # The timeline around the chosen line, inside frames/timeline like the cut review's
        tl = ttk.Frame(self, style="Timeline.TFrame", padding=(6 * kd, 6 * kd, 10 * kd, 10 * kd))
        tl.grid(row=3, column=0, sticky="ew", pady=(int(16 * k), 0))
        tl.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(tl, height=int(104 * k), background=P.timeline_bg, highlightthickness=0,
                                borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="ew")
        c = self.canvas
        c.bind("<Configure>", lambda e: self._redraw())
        c.bind("<Motion>", self._on_motion)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<MouseWheel>", lambda e: self._pan(-e.delta / 120 * 0.2))

        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, sticky="ew", pady=(int(14 * k), 0))
        self.b_save = ttk.Button(bottom, text=t("editor.save"), style="Main.Accent.TButton", command=self.save)
        self.b_save.pack(side="left")
        ttk.Button(bottom, text=t("editor.close"), command=self.on_close).pack(side="left", padx=(int(12 * k), 0))
        ttk.Button(bottom, text=t("editor.open_external"),
                   command=lambda: self.app._open(self.s.doc.path)).pack(side="left", padx=(int(12 * k), 0))
        self.status = ttk.Label(bottom, text="", style="Hint.TLabel", justify="left")
        self.status.pack(side="left", padx=(int(16 * k), 0), fill="x", expand=True)
        self.bind_all("<Control-s>", self._key_save, add="+")
        self.bind_all("<F5>", self._key_play, add="+")

    def _text_keys(self, widget: tk.Text, on_change):
        """Enter: on to the next line; Shift+Enter: a new line in the text; Ctrl+Enter: split at the cursor;
        Ctrl+Z: undo the last change of a line."""
        widget.bind("<KeyRelease>", lambda e: on_change())
        widget.bind("<Return>", lambda e: (self._next_line(), "break")[1])
        widget.bind("<Shift-Return>", lambda e: None)   # the Text's own newline
        widget.bind("<Control-Return>", lambda e: (self._split(widget), "break")[1])
        widget.bind("<Control-z>", lambda e: (self._undo(), "break")[1])

    def _lang_names(self):
        names = []
        for d in self.s.docs:
            name = d.lang.name if d.lang else d.path.stem
            names.append(name + (" *" if d.dirty else ""))
        self.c_lang.configure(values=names)
        self.lang_var.set(names[self.s.cur])

    # --- The list -------------------------------------------------------------------------

    def _row(self, i: int, c: Cue):
        """A row's values; a line that differs from every saved line is marked (accent) until it is saved."""
        edited = (c.start, c.end, clean_text(c.text)) not in self._saved_keys
        return dict(values=(i + 1, fmt_time(c.start), fmt_time(c.end), flat(c.text) or "-"),
                    tags=("edited",) if edited else ())

    def _fill_tree(self):
        self._saved_keys = {(c.start, c.end, clean_text(c.text)) for c in self.s.doc.saved}
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.s.doc.cues):
            self.tree.insert("", "end", iid=str(i), **self._row(i, c))

    def _update_row(self, i: int):
        if 0 <= i < len(self.s.doc.cues) and self.tree.exists(str(i)):
            r = self._row(i, self.s.doc.cues[i])
            self.tree.item(str(i), values=r["values"], tags=r["tags"])

    def _on_tree_select(self, _=None):
        sel = self.tree.selection()
        if sel and int(sel[0]) != self.s.sel:
            self._commit_text()
            self._select(int(sel[0]), from_tree=True)

    def _on_tree_double(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell":
            self._edit_inline()
            return "break"

    def _select(self, i: int, from_tree: bool = False, show_frame: bool = True):
        cues = self.s.doc.cues
        self.s.sel = i
        if not (0 <= i < len(cues)):
            self.line_card.configure(text=t("editor.no_line"))
            self.text.delete("1.0", "end")
            for w in (self.b_split, self.b_merge, self.b_delete):
                w.state(["disabled"])
            self._redraw()
            return
        c = cues[i]
        if not from_tree:
            self.tree.selection_set(str(i))
        self.tree.see(str(i))
        self.line_card.configure(text=t("editor.line_title", n=i + 1, total=len(cues)))
        self._typing = False
        self._load_text(c.text)
        self._show_times()
        for w in (self.b_split, self.b_delete):
            w.state(["!disabled"])
        self.b_merge.state(["!disabled"] if i + 1 < len(cues) else ["disabled"])
        self._view_line(c)
        if show_frame:
            self._picture(c.start)

    def _load_text(self, text: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.edit_modified(False)

    def _show_times(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        c = cues[i]
        self.t_labels["start"].configure(text=fmt_time(c.start))
        self.t_labels["end"].configure(text=fmt_time(c.end))
        self.t_len.configure(text=t("editor.length", s=fmt_seconds(c.end - c.start)))

    # --- Editing --------------------------------------------------------------------------

    def _changed(self, i: int | None = None, rebuild: bool = False):
        """After any change: the row (or the whole list), the language's * and the timeline."""
        if rebuild:
            self._fill_tree()
        elif i is not None:
            self._update_row(i)
        self._lang_names()
        self.status.configure(text="")
        self._redraw()

    def _text_changed(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        text = self.text.get("1.0", "end-1c")
        if text != cues[i].text:
            if not getattr(self, "_typing", False):   # one undo step per line visit, not per key
                self.s.doc.push_undo(i)
                self._typing = True
            cues[i].text = text
            self._changed(i)

    def _commit_text(self):
        self._text_changed()
        self._typing = False

    def _next_line(self):
        self._close_overlay(commit=True)
        self._commit_text()
        if self.s.sel + 1 < len(self.s.doc.cues):
            self._select(self.s.sel + 1)
        self.text.focus_set()
        self.text.mark_set("insert", "end-1c")

    def _edit_inline(self):
        """The chosen row's text, edited on the row itself (a Text over the text cell)."""
        i = self.s.sel
        if not (0 <= i < len(self.s.doc.cues)):
            return
        self._close_overlay(commit=True)
        self.tree.see(str(i))
        self.tree.update_idletasks()
        box = self.tree.bbox(str(i), "text")
        if not box:
            return
        x, y, w, h = box
        lines = max(1, self.s.doc.cues[i].text.count("\n") + 1)
        ov = self._overlay = tk.Text(self.tree, wrap="word", font="SunValleyBodyFont", background=P.input_bg,
                                     foreground=P.text, insertbackground=P.text, selectbackground=P.selection_bg,
                                     selectforeground=P.selection_text, borderwidth=0, highlightthickness=max(1, self.kd),
                                     highlightcolor=P.focus, highlightbackground=P.focus, padx=int(4 * self.k), pady=0)
        ov.insert("1.0", self.s.doc.cues[i].text)
        ov.place(x=x, y=y, width=w, height=max(h, int(22 * self.k) * lines + int(8 * self.k)))
        ov.focus_set()
        ov.mark_set("insert", "end-1c")

        def changed():
            text = ov.get("1.0", "end-1c")
            if text != self.s.doc.cues[i].text:
                if not getattr(self, "_typing", False):
                    self.s.doc.push_undo(i)
                    self._typing = True
                self.s.doc.cues[i].text = text
                self._load_text(text)
                self._changed(i)
        self._text_keys(ov, changed)
        ov.bind("<Escape>", lambda e: (self._close_overlay(commit=False), "break")[1])
        ov.bind("<FocusOut>", lambda e: self._close_overlay(commit=True))
        ov._changed = changed

    def _close_overlay(self, commit: bool):
        ov, self._overlay = self._overlay, None
        if ov is None:
            return
        try:
            if commit:
                ov._changed()
            elif getattr(self, "_typing", False) and self.s.doc.undo:   # Esc: the line as before the overlay
                self._undo()
            ov.destroy()
        except tk.TclError:
            pass
        self._typing = False
        try:
            self.tree.focus_set()
        except tk.TclError:
            pass

    def _cursor(self, widget: tk.Text | None) -> int | None:
        """The cursor's character position in the line's text, from the Text being edited."""
        w = widget if widget is not None else (self._overlay or self.text)
        try:
            return len(w.get("1.0", "insert"))
        except tk.TclError:
            return None

    def _split(self, widget: tk.Text | None = None):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        pos = self._cursor(widget)   # a button press: the cursor of the row being edited, else of the line's box
        self._close_overlay(commit=True)
        self._commit_text()
        if pos is None or pos <= 0 or pos >= len(cues[i].text):
            self.status.configure(text=t("editor.split_where"))
            return
        parts = split_cue(cues[i], pos)
        if parts is None:
            self.status.configure(text=t("editor.split_short"))
            return
        self.s.doc.push_undo(i)
        cues[i:i + 1] = list(parts)
        self._changed(rebuild=True)
        self._select(i + 1)
        self.text.focus_set()
        self.text.mark_set("insert", "1.0")

    def _merge(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues) - 1):
            return
        self._close_overlay(commit=True)
        self._commit_text()
        self.s.doc.push_undo(i)
        cues[i:i + 2] = [merge_cues(cues[i], cues[i + 1])]
        self._changed(rebuild=True)
        self._select(i)

    def _delete(self):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)) or self._overlay is not None:
            return
        self._commit_text()
        self.s.doc.push_undo(i)
        del cues[i]
        self._changed(rebuild=True)
        self._select(min(i, len(cues) - 1))

    def _undo(self):
        ov, self._overlay = self._overlay, None
        if ov is not None:
            try:
                ov.destroy()
            except tk.TclError:
                pass
        self._typing = False
        doc = self.s.doc
        if not doc.undo:
            return
        doc.cues, sel = doc.undo.pop()
        self._changed(rebuild=True)
        self._select(min(sel, len(doc.cues) - 1))

    def _nudge(self, side: str, delta: int):
        cues, i = self.s.doc.cues, self.s.sel
        if not (0 <= i < len(cues)):
            return
        self._commit_text()
        old = getattr(cues[i], side)
        self.s.doc.push_undo(i)
        new = set_edge(cues, i, side, old + delta, self.media.duration)
        if new == old:
            self.s.doc.undo.pop()
            self.status.configure(text=t("editor.edge_blocked"))
            return
        self._show_times()
        self._changed(i)
        self._picture(new)

    def _switch_lang(self, n: int):
        if n < 0 or n == self.s.cur:
            return
        self._close_overlay(commit=True)
        self._commit_text()
        # The same moment in the other language: the line that covers the chosen line's start
        at = self.s.doc.cues[self.s.sel].start if 0 <= self.s.sel < len(self.s.doc.cues) else 0
        self.s.cur = n
        cues = self.s.doc.cues
        i = next((j for j, c in enumerate(cues) if c.end > at), len(cues) - 1)
        self._fill_tree()
        self._lang_names()
        self.s.sel = -2
        self._select(i)

    # --- Saving ---------------------------------------------------------------------------

    def _key_save(self, e=None):
        if self.winfo_exists() and self.winfo_ismapped():
            self.save()
            return "break"

    def save(self) -> bool:
        """Save every changed language; then offer the words corrected in the spoken language's text."""
        self._close_overlay(commit=True)
        self._commit_text()
        dirty = [d for d in self.s.docs if d.dirty]
        if not dirty:
            self.status.configure(text=t("editor.nothing_to_save"))
            return True
        src = self.s.source_doc()
        before = copy.deepcopy(src.saved) if src in dirty else None
        notes, backups = [], []
        for d in dirty:
            try:
                r = save_doc(d, self.media.duration)
            except OSError as e:
                log.exception("editor: save failed")
                from .gui import boxes
                boxes.showerror(t("editor.title"), t("editor.save_failed", file=d.path.name, error=e),
                                parent=self.app._owner())
                return False
            if r.backup:
                backups.append(r.backup.name)
            if r.moved:
                notes.append(plural_t("editor.moved", r.moved))
            if r.dropped:
                notes.append(plural_t("editor.dropped", r.dropped))
        self._fill_tree()
        self._lang_names()
        self._select(min(self.s.sel, len(self.s.doc.cues) - 1), show_frame=False)
        msg = t("editor.saved")
        if backups:
            msg += " " + t("editor.backup", backup=", ".join(backups))
        msg += " " + " ".join(notes) + (" " if notes else "") + t("editor.remake")
        if before is not None:
            from . import claude
            known = {w.lower() for w, _ in claude.word_rules()}
            pairs = word_changes(before, src.cues, known)
            if pairs:
                chosen = offer_words(self, pairs, src.lang)
                if chosen:
                    added = claude.add_words(chosen)
                    msg += " " + plural_t("editor.words_added", len(added))
        self.status.configure(text=msg)
        return True

    # --- The picture and the sound ----------------------------------------------------------

    def _picture(self, ms: int):
        if not self.s.video:
            return
        self._want_ms = ms
        self.pic_time.configure(text=fmt_time(ms))
        self.media.frame(ms, int(PREVIEW_W * self.k))
        self._redraw()

    def _key_play(self, e=None):
        if self.winfo_exists() and self.winfo_ismapped():
            self._play_line()
            return "break"

    def _play_line(self):
        cues, i = self.s.doc.cues, self.s.sel
        if self.media.playing:
            self.media.stop()
            self.b_play.configure(text=t("editor.play_line"))
            return
        if not (0 <= i < len(cues)) or self.media.wav is None:
            if self.s.video and self.media.wav is None:
                self.status.configure(text=t("editor.sound_wait"))
            return
        c = cues[i]
        self.media.play(c.start, min(MAX_PLAY_MS, c.end - c.start))
        self._picture(c.start)
        self.b_play.configure(text=t("editor.stop"))
        self.after(200, self._play_tick)

    def _play_tick(self):
        if not self.winfo_exists():
            return
        if self.media.playing:
            self.after(200, self._play_tick)
        else:
            self.b_play.configure(text=t("editor.play_line"))

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
                            self.pic.configure(image=self._img, text="")
                        except tk.TclError:
                            pass
                elif ev[0] in ("duration", "sound"):
                    self._redraw()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    # --- The timeline -----------------------------------------------------------------------

    def _m(self) -> int:
        return 10 * self.kd

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
        """Show the line with some of its neighbours: at least VIEW_MIN_MS, the line in the middle third."""
        if self._drag:
            return
        span = max(VIEW_MIN_MS, 3 * (c.end - c.start))
        mid = (c.start + c.end) / 2
        self._set_view(mid - span / 2, mid + span / 2)

    def _set_view(self, v0: float, v1: float):
        dur = self._dur()
        span = min(max(VIEW_MIN_MS / 4, v1 - v0), max(dur, VIEW_MIN_MS))
        v0 = min(max(0, v0), max(0, dur - span))
        self.v0, self.v1 = int(v0), int(v0 + span)
        self._redraw()

    def _pan(self, frac: float):
        span = self.v1 - self.v0
        self._set_view(self.v0 + span * frac, self.v0 + span * (1 + frac))

    def _redraw(self):
        c = self.canvas
        try:
            if not c.winfo_exists():
                return
        except tk.TclError:
            return
        c.delete("all")
        k, kd, m = self.k, self.kd, self._m()
        w, h = c.winfo_width(), c.winfo_height()
        ruler = 26 * kd
        top, bottom = ruler + 2 * kd, h - 4 * kd
        mid = (top + bottom) // 2
        env = self.media.env
        if env is not None and len(env):
            import numpy as np
            cols = max(2, int(self._width() / max(1, k)))
            idx = np.clip(np.linspace(self.v0 / cutting.ENVELOPE_MS, self.v1 / cutting.ENVELOPE_MS,
                                      cols + 1)[:-1].astype(int), 0, len(env) - 1)
            level = np.clip((np.maximum.reduceat(env, idx) + 60) / 60, 0.02, 1.0) * ((bottom - top) / 2 - 4 * kd)
            xs = m + np.arange(cols) * self._width() / cols
            pts = [v for x, l in zip(xs, level) for v in (x, mid - l)] + \
                  [v for x, l in zip(xs[::-1], level[::-1]) for v in (x, mid + l)]
            if len(pts) >= 8:
                c.create_polygon(pts, fill=P.timeline_waveform, outline="")
        # The lines: the neighbours outlined in the speech colour with their number, the chosen one in the manual cut
        # colours with drag handles on its edges
        font = ("Segoe UI Semibold", 10)
        y0, y1 = top + 8 * kd, bottom - 8 * kd
        sel = self.s.sel
        for i, q in enumerate(self.s.doc.cues):
            if q.end <= self.v0 or q.start >= self.v1:
                continue
            x0, x1 = self.x_of(q.start), self.x_of(q.end)
            if i == sel:
                continue
            c.create_rectangle(x0, y0, max(x1, x0 + 2), y1, outline=P.timeline_speech, width=2 * kd)
            if x1 - x0 > 30 * kd:
                c.create_text(x0 + 6 * kd, y0 + 5 * kd, text=str(i + 1), anchor="nw", fill=P.timeline_speech, font=font)
        if 0 <= sel < len(self.s.doc.cues):
            q = self.s.doc.cues[sel]
            x0, x1 = self.x_of(q.start), self.x_of(q.end)
            border, fill = getattr(P, "cut_manual_border", P.accent), getattr(P, "cut_manual_fill", P.picker_hover_bg)
            c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=border, width=3 * kd)
            words = flat(q.text)
            room = x1 - x0 - 16 * kd
            if room > 20 * kd:
                label = f"{sel + 1}  {words}"
                while label and self._measure(label, font) > room:
                    label = label[:-2]
                c.create_text(x0 + 8 * kd, mid, text=label, anchor="w", fill=P.text, font=font)
            dragging = self._drag["side"] if self._drag else None
            for side, x in (("start", x0), ("end", x1)):
                if dragging == side:
                    c.create_image(x, mid, image=IMG.get("art/timeline-handle-dragging"))
                else:
                    c.create_rectangle(x - 5 * kd, mid - 20 * kd, x + 5 * kd, mid + 20 * kd, fill=border, outline="")
        # The ruler, over anything that reaches up there
        c.create_rectangle(0, 0, w, top, fill=P.timeline_bg, outline="")
        c.create_rectangle(m, ruler, w - m, top, fill=P.card_border, outline="")
        px_per_s = self._width() / max(0.001, (self.v1 - self.v0) / 1000)
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * px_per_s >= 80 * k), 600)
        sec = (self.v0 // 1000 // step) * step
        while sec * 1000 <= self.v1:
            x = round(self.x_of(sec * 1000))
            if m - 1 <= x <= w - m - 2 * kd:
                c.create_rectangle(x, 6 * kd, x + 2 * kd, ruler, fill=P.timeline_ruler, outline="")
                c.create_text(x + 7 * kd, ruler - 3 * kd, text=cutting.fmt_clock(sec * 1000), anchor="sw",
                              fill=P.timeline_ruler, font=(TERMINAL_FONT, -18 * kd))
            sec += step
        # Where the picture is from
        if self._want_ms is not None and self.v0 <= self._want_ms <= self.v1:
            x = self.x_of(self._want_ms)
            c.create_line(x, top, x, bottom, fill=P.heading, width=kd)

    def _measure(self, text: str, font) -> int:
        if not hasattr(self, "_font"):
            from tkinter import font as tkfont
            self._font = tkfont.Font(root=self, font=font)
        return self._font.measure(text)

    def _hit(self, x: float):
        """('edge', side) on the chosen line's edge, ('line', i) on a line, or None."""
        cues, sel = self.s.doc.cues, self.s.sel
        if 0 <= sel < len(cues):
            q = cues[sel]
            for side, ms in (("start", q.start), ("end", q.end)):
                if abs(self.x_of(ms) - x) <= EDGE_PX * self.kd:
                    return ("edge", side)
        ms = self.t_of(x)
        for i, q in enumerate(cues):
            if q.start <= ms <= q.end:
                return ("line", i)
        return None

    def _on_motion(self, e):
        if self._drag:
            return
        hit = self._hit(e.x)
        self.canvas.configure(cursor="sb_h_double_arrow" if hit and hit[0] == "edge" else
                              "hand2" if hit else "crosshair")

    def _on_press(self, e):
        self.canvas.focus_set()
        self._commit_text()
        hit = self._hit(e.x)
        if hit and hit[0] == "edge":
            self.s.doc.push_undo(self.s.sel)
            self._drag = {"side": hit[1], "moved": False}
            self._redraw()
        elif hit:
            self._select(hit[1])
        else:
            self._picture(self.t_of(e.x))
            self._redraw()

    def _on_drag(self, e):
        d = self._drag
        if not d:
            return
        i = self.s.sel
        ms = int(round(self.t_of(e.x) / 10) * 10)
        new = set_edge(self.s.doc.cues, i, d["side"], ms, self.media.duration)
        d["moved"] = True
        self._show_times()
        self._update_row(i)
        self._redraw()
        now = time.monotonic()
        if now - self._drag_frame_t >= DRAG_FRAME_MS / 1000:
            self._drag_frame_t = now
            self._picture(new)
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
            self._changed(self.s.sel)
        self._redraw()

    def _on_destroy(self, e):
        if e.widget is self:
            for seq in ("<Control-s>", "<F5>"):
                try:
                    self.unbind_all(seq)
                except tk.TclError:
                    pass


def offer_words(parent, pairs: list[tuple[str, str]], lang: Language | None) -> list[tuple[str, str]]:
    """Ask which corrected words go into the word list (corrections.txt): a check box per word, the ones that look
    misheard ticked. Returns the chosen pairs (none when the dialog is closed)."""
    from .gui import place_over
    k = 2 if IMG.two else 1
    win = tk.Toplevel(parent)
    win.title(t("editor.words_title"))
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=(20 * k, 16 * k, 24 * k, 20 * k))
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=t("editor.words_title"), style="Section.TLabel").pack(anchor="w")
    ttk.Label(frame, text=t("editor.words_text", plural=lang.plural if lang else ""), justify="left",
              wraplength=440 * k).pack(anchor="w", pady=(6 * k, 12 * k))
    card = skin.Card(frame, padding=(14 * k, 12 * k, 18 * k, 16 * k))
    card.pack(fill="x")
    vars_ = []
    for wrong, right in pairs[:20]:
        v = tk.BooleanVar(value=similar(wrong, right))
        ttk.Checkbutton(card, variable=v, text=f"{wrong}  →  {right}").pack(anchor="w", pady=2 * k)
        vars_.append(((wrong, right), v))
    chosen: list[tuple[str, str]] = []

    def add():
        chosen.extend(p for p, v in vars_ if v.get())
        win.destroy()
    btns = ttk.Frame(frame)
    btns.pack(fill="x", pady=(16 * k, 0))
    ttk.Button(btns, text=t("editor.words_add"), style="Accent.TButton", command=add).pack(side="left")
    ttk.Button(btns, text=t("editor.words_skip"), command=win.destroy).pack(side="left", padx=(10 * k, 0))
    skin.dialog(win)
    try:
        place_over(win, parent.winfo_toplevel())
    except Exception:
        pass
    win.bind("<Escape>", lambda e: win.destroy())
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)
    return chosen
