"""Kø: several videos in a queue (the setup view's list, the work view's queue card, the done view's summary).

The user adds several videos at once (several files in the file dialog, Tilføj videoer..., or dropping files on the
window), each with its own choices (spoken language, speech, subtitles, cut first, listen again), and starts the queue.
With one video the setup view is exactly as before: the queue only shows from the second video on.

A queue runs in three rounds, one video after another in each (phase-major order):

    1. listen  extract the audio and transcribe (no user needed: this is the part that can run overnight)
    2. claude  the Claude round, and the cut review and the cut when the video is cut first (the user's turns)
    3. make    dub or burn in the subtitles (no user needed)

so the user's turns come one after another in round 2 instead of being spread between long waits. Each round is an
ordinary run of the app (App._run_job) built by App._validate from the files next to the video, so a round that has
nothing to do (the subtitles are already there and current, nothing needs Claude, nothing to dub) is skipped, and a
queue resumed after a crash simply carries on from the round it was in. A video waiting for the user pauses the queue;
an error or a stop of one video does not stop the others (a stop asks whether to drop the rest).

The queue is kept in data_dir()/queue.json while it runs; after a crash or a restart the app offers to resume it.
"""
from __future__ import annotations

import ctypes
import json
import queue as _stdqueue
import time
import tkinter as tk
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from tkinter import filedialog, ttk

from . import skin, theme
from .common import LANG_BY_CODE, data_dir, log
from .i18n import t
from .pickers import Tooltip
from .theme import IMG, P

PHASES = ("listen", "claude", "make")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv"}
FINISHED = ("done", "error", "cancelled", "skipped")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def videos_in(paths) -> list[Path]:
    """The videos among dropped or picked paths, in order; a folder gives the videos directly inside it."""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted((x for x in p.iterdir() if x.is_file() and is_video(x)), key=lambda x: x.name.lower())
        elif is_video(p):
            out.append(p)
    return list(dict.fromkeys(out))


# --- The queue itself (no Tk) ----------------------------------------------------------------------------------------

@dataclass
class Item:
    """One video in the queue: its choices (language codes) and how far it has come."""
    video: str
    lang: str = "da"
    speech: str | None = None      # None: keep the original speech
    subs: str | None = None        # None: no subtitles
    cut: bool = False              # Klip videoen først
    redo: bool = False             # Lyt videoen igennem forfra
    recut: bool = False            # Klip videoen forfra
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    phase: int = 0                 # the next round to run (index in PHASES; 3 = all rounds done)
    state: str = "waiting"         # waiting, running, done, error, cancelled, skipped
    result: str | None = None      # the newest file the rounds made (the dub, the burned video, the subtitles)
    final: str | None = None       # the name of the file the user gets, as planned when the queue started
    files: list[str] = field(default_factory=list)
    words: list[list[str]] = field(default_factory=list)   # words Claude corrected and the app learned
    message: str = ""
    no_cut: bool = False           # the user kept everything in the cut review: round 3 uses the original video

    def choices(self) -> tuple:
        return self.lang, self.speech, self.subs, self.cut, self.redo, self.recut

    def fresh(self) -> "Item":
        """The same video and choices, not started (Prøv igen)."""
        return Item(self.video, self.lang, self.speech, self.subs, self.cut, self.redo, self.recut)

    @classmethod
    def from_json(cls, d: dict) -> "Item":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class VideoQueue:
    """The videos and their progress; saved as JSON while the queue runs."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "queue.json"
        self.items: list[Item] = []
        self.running = False
        self.current: tuple[str, int] | None = None   # (item id, round) of the run going on
        self.stopped = False                            # the user dropped the rest

    # The list in the setup view
    def add(self, videos, choices: tuple) -> int:
        """Append videos with the given choices; a video already in the queue with the same choices is left out."""
        lang, speech, subs, cut, redo, recut = choices
        have = {(i.video.lower(), i.choices()) for i in self.items}
        n = 0
        for v in videos:
            item = Item(str(v), lang, speech, subs, cut, redo, recut)
            key = (item.video.lower(), item.choices())
            if key in have:
                continue
            have.add(key)
            self.items.append(item)
            n += 1
        return n

    def remove(self, index: int):
        if 0 <= index < len(self.items):
            del self.items[index]

    def move(self, index: int, delta: int) -> int:
        """Move an item up (-1) or down (+1); returns its new index."""
        j = index + delta
        if 0 <= index < len(self.items) and 0 <= j < len(self.items):
            self.items[index], self.items[j] = self.items[j], self.items[index]
            return j
        return index

    def item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    # Running
    def next_step(self) -> tuple[Item, int] | None:
        """The next (video, round): every video's first round before any second round, and so on."""
        if self.stopped:
            return None
        for phase in range(len(PHASES)):
            for item in self.items:
                if item.state in ("waiting", "running") and item.phase == phase:
                    return item, phase
        return None

    def begin(self, item: Item, phase: int):
        item.state = "running"
        self.current = (item.id, phase)

    def skip(self, item: Item, phase: int):
        """The round has nothing to do for this video."""
        item.phase = phase + 1
        item.state = "done" if item.phase >= len(PHASES) else "waiting"
        if self.current and self.current[0] == item.id:
            self.current = None

    def end(self, item: Item, phase: int, kind: str, info=None, files=(), words=(), no_cut: bool = False):
        """A round's run ended: kind is "done" (info: its result file), "error" (info: the message), "cancelled" or
        "skipped" (the Claude round was not finished)."""
        for f in files:
            if str(f) not in item.files:
                item.files.append(str(f))
        for w in words:
            if list(w) not in item.words:
                item.words.append(list(w))
        self.current = None
        if kind == "done":
            if info:
                item.result = str(info)
            if no_cut:
                item.no_cut = True
            self.skip(item, phase)
        elif kind == "error":
            item.state, item.message = "error", str(info or "")
        elif kind == "cancelled":
            item.state = "cancelled"
        else:
            item.state, item.message = "skipped", str(info or "")

    def stop(self):
        """Drop the rest: the videos not started stay "waiting" (shown as not started)."""
        self.stopped = True

    def pending(self) -> list[Item]:
        return [i for i in self.items if i.state not in FINISHED]

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in ("waiting", "running") + FINISHED}
        for i in self.items:
            out[i.state] = out.get(i.state, 0) + 1
        return out

    # Kept on disk while it runs
    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "running": self.running, "saved": time.time(),
                                       "current": list(self.current) if self.current else None,
                                       "items": [asdict(i) for i in self.items]}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.exception("could not save the queue")

    def forget(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            log.exception("could not remove the queue file")

    @classmethod
    def load_unfinished(cls, path: Path | None = None) -> "VideoQueue | None":
        """A queue that was running when the app stopped, with videos left to do; None otherwise."""
        q = cls(path)
        try:
            data = json.loads(q.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.exception("could not read the queue")
            return None
        if not data.get("running"):
            return None
        try:
            q.items = [Item.from_json(d) for d in data.get("items", [])]
        except TypeError:
            log.exception("bad queue file")
            return None
        for i in q.items:
            if i.state == "running":
                i.state = "waiting"   # the round it was in runs again
        return q if q.pending() else None


# --- Words for the list ----------------------------------------------------------------------------------------------

def choices_text(item: Item) -> str:
    """"Dansk · svensk tale · norske undertekster · klippes først" in the app's language."""
    lang = LANG_BY_CODE.get(item.lang) or LANG_BY_CODE["da"]
    parts = [lang.name]
    sp, su = LANG_BY_CODE.get(item.speech or ""), LANG_BY_CODE.get(item.subs or "")
    if sp:
        parts.append(t("gui.done.stat_speech", singular=sp.singular, name=sp.singular))
    if su:
        parts.append(t("gui.done.stat_subs", plural=su.plural))
    if not sp and not su:
        parts.append(t("queue.only_text"))
    if item.cut:
        parts.append(t("queue.cut"))
    return " · ".join(parts)


def state_text(item: Item, stopped: bool = False) -> str:
    if item.state == "running":
        return t(("queue.state.listen_now", "queue.state.claude_now", "queue.state.make_now")[min(item.phase, 2)])
    if item.state == "waiting":
        if stopped:
            return t("queue.state.not_started")
        return t(("queue.state.waiting", "queue.state.listened", "queue.state.checked")[min(item.phase, 2)])
    return t(f"queue.state.{item.state}")


def state_icon(item: Item) -> str:
    return {"done": "step-done", "error": "step-error", "running": "step-active-0"}.get(item.state, "step-pending")


def rotated(rel: str, quarter: int) -> tk.PhotoImage:
    """A pixel icon turned a quarter (1: clockwise, 3: anticlockwise), kept with the theme's images."""
    key = f"{rel}@rot{quarter}"
    if key not in IMG._cache:
        src = IMG.get(rel)
        w, h = src.width(), src.height()
        img = tk.PhotoImage(master=IMG.root, width=h, height=w)
        for y in range(h):
            for x in range(w):
                if src.transparency_get(x, y):
                    continue
                r, g, b = src.get(x, y)
                nx, ny = (h - 1 - y, x) if quarter == 1 else (y, w - 1 - x)
                img.put(f"#{r:02x}{g:02x}{b:02x}", (nx, ny))
        IMG._cache[key] = img
    return IMG._cache[key]


# --- Dropping files on the window ------------------------------------------------------------------------------------

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
_targets: list = []   # kept alive for the life of the process (Windows calls them)


class DropTarget:
    """Files dropped from Explorer on a window (WM_DROPFILES), without tkdnd: the window's procedure is subclassed,
    and the dropped paths go into a thread-safe queue that the app's poll loop reads. The window procedure runs
    inside Tk's message loop, so it must not call Tk (see gui._box_over)."""
    WM_DROPFILES = 0x0233

    def __init__(self, hwnd: int):
        self.paths: _stdqueue.SimpleQueue = _stdqueue.SimpleQueue()
        self.hwnd = hwnd
        u32, sh = ctypes.windll.user32, ctypes.windll.shell32
        u32.SetWindowLongPtrW.restype = ctypes.c_void_p
        u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        u32.CallWindowProcW.restype = ctypes.c_ssize_t
        u32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_ssize_t]
        sh.DragQueryFileW.restype = ctypes.c_uint
        sh.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        sh.DragFinish.argtypes = [ctypes.c_void_p]
        sh.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._proc = _WNDPROC(self._wndproc)
        self._old = u32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p))  # GWLP_WNDPROC
        sh.DragAcceptFiles(hwnd, True)
        # Explorer runs at medium integrity like us, but let the drop messages through in case we do not
        try:
            u32.ChangeWindowMessageFilterEx.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
            for msg in (self.WM_DROPFILES, 0x004A, 0x0049):   # WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA
                u32.ChangeWindowMessageFilterEx(hwnd, msg, 1, None)
        except Exception:
            pass
        _targets.append(self)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_DROPFILES:
            try:
                sh = ctypes.windll.shell32
                n = sh.DragQueryFileW(ctypes.c_void_p(wparam), 0xFFFFFFFF, None, 0)
                names = []
                for i in range(n):
                    size = sh.DragQueryFileW(ctypes.c_void_p(wparam), i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(size)
                    sh.DragQueryFileW(ctypes.c_void_p(wparam), i, buf, size)
                    names.append(buf.value)
                sh.DragFinish(ctypes.c_void_p(wparam))
                self.paths.put(names)
            except Exception:
                pass
            return 0
        return ctypes.windll.user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)

    def take(self) -> list[list[str]]:
        out = []
        while True:
            try:
                out.append(self.paths.get_nowait())
            except _stdqueue.Empty:
                return out


# --- The controller in the app ---------------------------------------------------------------------------------------

class QueueController:
    """The queue's part of the App: the list in the setup view, the run of the rounds, the work view's queue card,
    the done view's summary, dropping files and resuming after a restart. The App calls in at a few points (see the
    `self.vq` lines in gui.py); everything else stays in the App as for one video."""

    def __init__(self, app):
        self.app = app
        self.q = VideoQueue()
        self.sel = 0                    # the video whose choices the setup view shows
        self._loading = False           # the form is being filled from an item: do not copy it back
        self.summary_items: list[Item] = []
        self.summary_stopped = False
        self.drop: DropTarget | None = None
        self._job_meta: dict = {}       # the round's job: cut planned, the cut video's path
        self.box = self.work_card = self.summary = None

    @property
    def active(self) -> bool:
        """The setup view shows the queue (two videos or more)."""
        return len(self.q.items) >= 2

    @property
    def running(self) -> bool:
        return self.q.running

    # --- The setup view --------------------------------------------------------------------------------------------

    def build_setup(self, g1):
        """The list of videos in the video card, in the same place as the one chosen video (hidden until there are
        two). Called by App._build_setup, also after a theme or language switch."""
        app, k = self.app, self.app.scale
        style = ttk.Style(app.root)
        style.configure("Tool.TButton", padding=(int(7 * k), int(5 * k), int(10 * k), int(8 * k)))
        style.configure("Card.Tool.TButton", background=P.card)
        Tooltip(app.v_btn, lambda: t("queue.pick_tooltip"))
        box = self.box = ttk.Frame(g1)
        box.grid(row=0, column=0, columnspan=2, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.rows_frame = tk.Frame(box, background=P.card, borderwidth=0, highlightthickness=0)
        self.rows_frame.grid(row=0, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(box)
        bar.grid(row=1, column=0, sticky="ew", pady=(int(10 * k), 0))
        bar.columnconfigure(1, weight=1)
        skin.IconButton(bar, "plus", text=t("queue.add"), command=self.add_dialog).grid(row=0, column=0, sticky="w")
        self.sel_hint = ttk.Label(bar, text=t("queue.select_hint"), style="Hint.TLabel", justify="left")
        self.sel_hint.grid(row=0, column=1, sticky="ew", padx=(int(14 * k), 0))
        app._wrap(self.sel_hint, bar, int(220 * k))
        box.grid_remove()
        self._rows: list[dict] = []

    def _draw_rows(self):
        """One row per video: the selection mark, its number, name, choices and state, and move/remove buttons."""
        k, app = self.app.scale, self.app
        for r in self._rows:
            r["frame"].destroy()
        self._rows = []
        n = len(self.q.items)
        for i, item in enumerate(self.q.items):
            row = tk.Frame(self.rows_frame, background=P.card, borderwidth=0, highlightthickness=0,
                           takefocus=1)
            row.grid(row=i, column=0, sticky="ew", pady=(0, int(2 * k)))
            row.columnconfigure(3, weight=1)
            mark = tk.Label(row, borderwidth=0, background=P.card)
            mark.grid(row=0, column=0, rowspan=2, padx=(int(8 * k), int(10 * k)))
            num = tk.Label(row, text=str(i + 1), foreground=P.hint, background=P.card, borderwidth=0,
                           font=(theme.PIXEL_FONT, -int(12 * k)), width=2, anchor="w")
            num.grid(row=0, column=1, rowspan=2, sticky="w")
            v = Path(item.video)
            name = tk.Label(row, text=v.name, background=P.card, foreground=P.text, borderwidth=0, padx=0, pady=0,
                            font="SunValleyBodyStrongFont", anchor="w", justify="left")
            name.grid(row=0, column=3, sticky="ew", pady=(int(6 * k), 0))
            sub = tk.Label(row, text=choices_text(item) if v.is_file() else t("gui.setup.video_gone"),
                           background=P.card, foreground=P.hint if v.is_file() else P.error, borderwidth=0,
                           padx=0, pady=0, font=("Segoe UI", -int(13 * k)), anchor="w", justify="left")
            sub.grid(row=1, column=3, sticky="ew", pady=(0, int(6 * k)))
            tools = tk.Frame(row, background=P.card, borderwidth=0)
            tools.grid(row=0, column=4, rowspan=2, sticky="e", padx=(int(10 * k), int(4 * k)))
            for col, (img, tip, cmd, ok) in enumerate((
                    (rotated("icons/arrow-right", 3), t("queue.move_up"), lambda n_=i: self.move(n_, -1), i > 0),
                    (rotated("icons/arrow-right", 1), t("queue.move_down"), lambda n_=i: self.move(n_, 1), i < n - 1),
                    (IMG.get("icons/cancel"), t("queue.remove"), lambda n_=i: self.remove(n_), True))):
                b = ttk.Button(tools, image=img, style="Card.Tool.TButton", command=cmd)
                b.grid(row=0, column=col, padx=(int(6 * k) if col else 0, 0))
                if not ok:
                    b.state(["disabled"])
                Tooltip(b, tip)
            parts = [row, mark, num, name, sub, tools]
            for w in parts[:5]:
                w.bind("<Button-1>", lambda e, n_=i: self.select(n_))
            row.bind("<Up>", lambda e, n_=i: self.select(max(0, n_ - 1), focus=True))
            row.bind("<Down>", lambda e, n_=i: self.select(min(n - 1, n_ + 1), focus=True))
            row.bind("<Delete>", lambda e, n_=i: self.remove(n_))
            self._rows.append({"frame": row, "mark": mark, "num": num, "name": name, "sub": sub, "tools": tools})
        self._paint_rows()

    def _paint_rows(self):
        for i, r in enumerate(self._rows):
            on = i == self.sel
            bg = P.picker_hover_bg if on else P.card
            for key in ("frame", "mark", "num", "name", "sub", "tools"):
                r[key].configure(background=bg)
            r["mark"].configure(image=IMG.get("icons/radio-on" if on else "icons/radio-off"))
            r["num"].configure(foreground=P.heading if on else P.hint)

    def refresh_setup(self):
        """Called at the end of App._refresh: one video as before, or the queue (and the form edits the selected
        video's choices)."""
        app = self.app
        if self.box is None:
            return
        if not self.active:
            if self.box.winfo_manager():
                self.box.grid_remove()
                app.v_card.grid()
                app.g_video.configure(text=t("gui.setup.group_video"))
                app.g_output.configure(text=t("gui.setup.group_output"))
                app.start_btn.configure(text=t("gui.setup.start"))
                app.start_hint.configure(text=t("gui.setup.start_hint"))
            return
        if not self._loading and not self.running:
            self._store_form()
        if not self.box.winfo_manager():
            app.v_card.grid_remove()
            self.box.grid()
            app.start_btn.configure(text=t("queue.start"))
            app.start_hint.configure(text=t("queue.start_hint"))
        app.g_video.configure(text=t("queue.group_video", n=len(self.q.items)))
        sel = self.q.items[min(self.sel, len(self.q.items) - 1)]
        app.g_output.configure(text=t("queue.group_output", name=Path(sel.video).stem))
        if len(self._rows) != len(self.q.items):
            self._draw_rows()
            skin.skin_cards(app.g_video)
            self._paint_rows()
        else:
            for r, item in zip(self._rows, self.q.items):
                ok = Path(item.video).is_file()
                r["sub"].configure(text=choices_text(item) if ok else t("gui.setup.video_gone"),
                                   foreground=P.hint if ok else P.error)
        ok = any(Path(i.video).is_file() for i in self.q.items)
        app.start_btn.configure(state="normal" if ok and not self.running else "disabled")

    def _form_choices(self) -> tuple:
        app = self.app
        sp, su = app._speech_lang(), app._subs_lang()
        return (app._lang().code, sp.code if sp else None, su.code if su else None, bool(app.cut.get()),
                bool(app.redo.get()), bool(app.recut.get()))

    def _store_form(self):
        if not (0 <= self.sel < len(self.q.items)):
            return
        item = self.q.items[self.sel]
        if Path(item.video) != (self.app._video() or Path()):
            return   # the form shows another video (it is being filled)
        item.lang, item.speech, item.subs, item.cut, item.redo, item.recut = self._form_choices()

    def load(self, item: Item, speech="keep", subs="keep", cut=None, redo=None, recut=None):
        """Fill the setup form with a video's choices (or the given overrides for a round)."""
        app = self.app
        self._loading = True
        try:
            app._recut_forced = False
            app.redo.set(item.redo if redo is None else redo)
            app.cut.set(item.cut if cut is None else cut)
            app.recut.set(item.recut if recut is None else recut)
            app.video.set(item.video)
            app.lang.set((LANG_BY_CODE.get(item.lang) or LANG_BY_CODE["da"]).name)
            app._refresh(lang_changed=True)
            sp = LANG_BY_CODE.get((item.speech if speech == "keep" else speech) or "")
            su = LANG_BY_CODE.get((item.subs if subs == "keep" else subs) or "")
            app.speech.set(sp.name if sp else app._speech_items(app._lang())[0][1])
            app.subs.set(su.word("choice") if su else t("gui.setup.subs_none"))
            app._refresh()
        finally:
            self._loading = False

    def select(self, index: int, focus: bool = False):
        if self.running or not (0 <= index < len(self.q.items)):
            return
        self._store_form()
        self.sel = index
        self.load(self.q.items[index])
        self._paint_rows()
        self.refresh_setup()
        if focus and index < len(self._rows):
            self._rows[index]["frame"].focus_set()

    def move(self, index: int, delta: int):
        self._store_form()
        j = self.q.move(index, delta)
        if self.sel == index:
            self.sel = j
        elif self.sel == j:
            self.sel = index
        self._draw_rows()
        skin.skin_cards(self.app.g_video)
        self._paint_rows()
        self.refresh_setup()

    def remove(self, index: int):
        if self.running:
            return
        self._store_form()
        self.q.remove(index)
        if self.sel >= index and self.sel > 0:
            self.sel -= 1
        self.sel = min(self.sel, len(self.q.items) - 1)
        if len(self.q.items) == 1:   # back to one video: the setup view as it always was
            only = self.q.items.pop()
            self._draw_rows()
            self.load(only)
            return
        self._draw_rows()
        skin.skin_cards(self.app.g_video)
        self._paint_rows()
        self.load(self.q.items[self.sel])

    # Adding videos
    def picked(self, paths) -> bool:
        """The file dialog of Vælg video / Skift video: one file is handled by the App as always; several make a
        queue of them (with the current choices). Returns True when handled here."""
        videos = [Path(p) for p in paths]
        if len(videos) < 2:
            return False
        app = self.app
        app.redo.set(False)
        app.recut.set(False)
        choices = self._form_choices()
        self.q = VideoQueue()
        self.q.add(videos, choices)
        self._after_add(0)
        return True

    def add_dialog(self):
        app = self.app
        from .gui import video_types
        ps = filedialog.askopenfilenames(parent=app.root, title=t("queue.add_title"), filetypes=video_types(),
                                         initialdir=app.settings.last_dir)
        if ps:
            app.settings.last_dir = str(Path(ps[0]).parent)
            app.settings.save()
            self.add(ps)

    def add(self, paths):
        """Add videos (picked or dropped) to what the setup view shows: one video already chosen becomes the first in
        a new queue. The new ones get the choices shown now, as new videos (not listened to again or cut again)."""
        app = self.app
        videos = videos_in(paths)
        if not videos:
            return
        if self.running:
            # The queue is running: the new videos join it with the last video's choices
            last = self.q.items[-1]
            lang, speech, subs, cut, _, _ = last.choices()
            n = self.q.add(videos, (lang, speech, subs, cut, False, False))
            if n:
                self.q.save()
                self.update_work()
            return
        if app._running or app._starting:
            return
        if app.view == "done":
            app._show("setup")
        self._store_form()
        lang, speech, subs, cut, _, _ = self._form_choices()
        choices = (lang, speech, subs, cut, False, False)
        if not self.active:
            current = app._video()
            if not current or not current.is_file():
                if len(videos) == 1:   # nothing chosen yet: as Vælg video
                    app.redo.set(False)
                    app.recut.set(False)
                    app.video.set(str(videos[0]))
                    return
                current = None
            self.q = VideoQueue()
            if current:
                self.q.add([current], self._form_choices())
            first_new = len(self.q.items)
            self.q.add(videos, choices)
            if len(self.q.items) < 2:
                self.q.items = []
                return
            self._after_add(first_new if current else 0)
            return
        first_new = len(self.q.items)
        if self.q.add(videos, choices):
            self._after_add(first_new)

    def _after_add(self, select: int):
        self.sel = min(select, len(self.q.items) - 1)
        self._draw_rows()
        skin.skin_cards(self.app.g_video)
        self.load(self.q.items[self.sel])
        self._paint_rows()
        self.refresh_setup()

    def enable_drop(self):
        if self.drop is not None:
            return
        try:
            from .gui import frame_hwnd
            hwnd = frame_hwnd(self.app.root)
            if hwnd:
                self.drop = DropTarget(hwnd)
        except Exception:
            log.exception("could not accept dropped files")

    def poll_drops(self):
        if self.drop is None:
            return
        for names in self.drop.take():
            log.info("dropped: %s", names)
            try:
                self.add(names)
            except Exception:
                log.exception("dropped files")

    # --- Running ---------------------------------------------------------------------------------------------------

    def start(self):
        """Start the queue: every video is checked first with the user present (the questions about files that
        will be made again, missing models and so on), then the rounds run without questions."""
        app = self.app
        if app._running or app._starting or self.running:
            return
        self._store_form()
        keep = self.sel
        finals = {}
        for i, item in enumerate(self.q.items):
            self.sel = i
            self._paint_rows()
            self.load(item)
            job = app._validate(auto=False)
            if not job:
                self.refresh_setup()
                return
            finals[item.id] = str(job["result"])
        for item in self.q.items:
            item.phase, item.state, item.result, item.files, item.words = 0, "waiting", None, [], []
            item.message, item.no_cut, item.final = "", False, finals.get(item.id)
        self.sel = keep
        self.q.running, self.q.stopped = True, False
        self.q.save()
        app.mcp_job = None
        self.next()

    def _build_job(self, item: Item, phase: int):
        """The App job for one round of a video, or None when the round has nothing to do. Raises ValueError when
        the video cannot be done at all."""
        app = self.app
        if not Path(item.video).is_file():
            raise ValueError(t("gui.setup.video_gone"))
        name = PHASES[phase]
        if name == "listen":
            self.load(item, speech=None, subs=None, cut=False, redo=item.redo, recut=False)
            if item.cut and not item.redo and not item.recut and app._cut_mode(Path(item.video))[0] == "reuse":
                return None   # the cut video made earlier is used, with its own subtitles
            job = app._validate(auto=True)
            if not job or not job["transcribe"]:
                return None
            job["check"], job["translate"] = False, []
            return job
        if name == "claude":
            self.load(item, redo=False, recut=item.recut or (item.redo and item.cut))
            job = app._validate(auto=True)
            if not job:
                if item.speech or item.subs:
                    raise ValueError(t("queue.cannot_start"))
                return None
            if not (job["check"] or job["translate"] or job["cut"]):
                return None
            job["speech"] = job["subs"] = None
            job["result"] = job["edited"] if job["cut"] else app._result_name(job["video"], job["lang"], None, None)
            return job
        # make
        if not item.speech and not item.subs:
            return None
        self.load(item, redo=False, recut=False, cut=item.cut and not item.no_cut)
        job = app._validate(auto=True)
        if not job:
            raise ValueError(t("queue.cannot_start"))
        return job

    def next(self):
        """Run the next round, or end the queue."""
        app = self.app
        if not self.running or app._running:
            return
        while True:
            step = self.q.next_step()
            if step is None:
                self.finish()
                return
            item, phase = step
            try:
                job = self._build_job(item, phase)
            except Exception as e:
                log.exception("queue: %s round %s", item.video, PHASES[phase])
                self.q.end(item, phase, "error", str(e) if isinstance(e, ValueError) else t("gui.error.unexpected",
                                                                                              error=e))
                self.q.save()
                continue
            if job is None:
                self.q.skip(item, phase)
                self.q.save()
                continue
            break
        self.q.begin(item, phase)
        self.q.save()
        self._job_meta = {"cut": bool(job.get("cut")), "edited": job.get("edited")}
        log.info("queue: %s round %s", item.video, PHASES[phase])
        app.level = self.q.items.index(item) + 1
        app._run_job(job)
        if item.final:
            app.w_result.configure(text=t("gui.work.you_get", name=Path(item.final).name))
        self.update_work()

    def job_ended(self, kind: str, info) -> bool:
        """A run ended (App._poll). Returns True when the queue takes it from here (the next round, or the summary)
        instead of the App's done view."""
        if not self.running or not self.q.current:
            return False
        app = self.app
        item_id, phase = self.q.current
        item = self.q.item(item_id)
        if item is None:
            return False
        files = [str(p) for p in app.created]
        no_cut = (kind == "done" and PHASES[phase] == "claude" and self._job_meta.get("cut")
                  and self._job_meta.get("edited") not in app.created)
        self.q.end(item, phase, kind, info, files, app.learned_words, no_cut=bool(no_cut))
        self.q.save()
        self.update_work()
        if kind == "cancelled" and self.q.next_step() is not None:
            from .gui import APP_NAME, boxes
            if boxes.askyesno(APP_NAME, t("queue.drop_rest"), parent=app._owner()):
                self.q.stop()
        app.root.after(400, self.next)
        return True

    def finish(self):
        app = self.app
        self.summary_items = [Item.from_json(asdict(i)) for i in self.q.items]
        self.summary_stopped = self.q.stopped
        self.q.running = False
        self.q.current = None
        self.q.forget()
        self.q = VideoQueue()
        self.sel = 0
        app.level = 1
        app._show_result("queue", None)
        if app._theme_pending or app._language_pending:
            app.root.after(300, app._apply_pending)

    def retry(self):
        """Prøv igen on the summary: the videos that did not finish, as a new queue (or one video as always)."""
        app = self.app
        again = [i.fresh() for i in self.summary_items if i.state != "done"]
        if not again:
            return
        if len(again) == 1:
            self.q = VideoQueue()
            self._draw_rows()
            self.load(again[0])
            app._show("setup")
            app._start()
            return
        self.q = VideoQueue()
        self.q.items = again
        app._show("setup")
        self._after_add(0)
        self.start()

    def offer_resume(self):
        """After a crash or a restart during a queue: ask whether to carry on."""
        q = VideoQueue.load_unfinished()
        if q is None:
            return
        app = self.app
        if app._running or app._starting:
            return
        from .gui import APP_NAME, boxes
        left = len(q.pending())
        if not boxes.askyesno(APP_NAME, t("queue.resume_ask", n=len(q.items), left=left), parent=app._owner()):
            q.forget()
            return
        self.q = q
        self.q.running, self.q.stopped = True, False
        self.sel = 0
        self._draw_rows()
        log.info("queue resumed: %d videos, %d left", len(q.items), left)
        self.next()

    # --- The work view: the queue card -----------------------------------------------------------------------------

    def build_work(self, parent, row: int):
        k = self.app.scale
        card = self.work_card = skin.Card(parent, text=t("queue.work_title", i=1, n=1), padding=14)
        card.grid(row=row, column=0, sticky="ew", pady=(int(24 * k), 0))
        card.columnconfigure(2, weight=1)
        self.work_rows = tk.Frame(card, background=P.card, borderwidth=0)
        self.work_rows.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.work_rows.columnconfigure(2, weight=1)
        hint = ttk.Label(card, text=t("queue.work_hint"), style="Hint.TLabel", justify="left")
        hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(int(10 * k), 0))
        self.app._wrap(hint, card, int(40 * k))
        card.grid_remove()

    def update_work(self):
        """The queue card under the run: each video with its state; the one running in the step colours."""
        card = self.work_card
        if card is None:
            return
        try:
            if not self.running:
                card.grid_remove()
                return
        except tk.TclError:
            return
        k, items = self.app.scale, self.q.items
        for child in self.work_rows.winfo_children():
            child.destroy()
        cur = self.q.item(self.q.current[0]) if self.q.current else None
        card.configure(text=t("queue.work_title", i=items.index(cur) + 1 if cur in items else
                              sum(1 for i in items if i.state in FINISHED), n=len(items)))
        for r, item in enumerate(items):
            self.work_rows.rowconfigure(r, minsize=int(32 * k))
            now = item is cur
            tk.Label(self.work_rows, image=IMG.get(f"icons/{state_icon(item)}"), background=P.card,
                     borderwidth=0).grid(row=r, column=0, sticky="w")
            tk.Label(self.work_rows, text=str(r + 1), foreground=P.hint, background=P.card, borderwidth=0, pady=0,
                     font=(theme.PIXEL_FONT, -int(10 * k))).grid(row=r, column=1, sticky="w", padx=(int(10 * k), 0))
            colour = {"done": P.step_done, "error": P.error}.get(item.state, P.step_active if now else P.text)
            tk.Label(self.work_rows, text=Path(item.video).name, foreground=colour, background=P.card,
                     font="SunValleyBodyStrongFont" if now else "SunValleyBodyFont", anchor="w", borderwidth=0,
                     padx=0, pady=0).grid(row=r, column=2, sticky="ew", padx=(int(10 * k), 0))
            tk.Label(self.work_rows, text=state_text(item, self.q.stopped), foreground=P.hint, background=P.card,
                     font=("Segoe UI", -int(13 * k)), borderwidth=0, padx=0, pady=0,
                     anchor="e").grid(row=r, column=3, sticky="e", padx=(int(12 * k), 0))
        card.grid()

    # --- The done view: the summary --------------------------------------------------------------------------------

    def hide_summary(self):
        """Before the App's own done view: no queue summary, and the files card has its own title again."""
        try:
            self.app.d_files.configure(text=t("gui.done.group_files"))
        except (AttributeError, tk.TclError):
            pass
        if self.summary is not None:
            try:
                self.summary.destroy()
            except tk.TclError:
                pass
            self.summary = None

    def show_summary(self, replay: bool = False):
        """The done view after a queue: STAGE CLEAR (or CONTINUE? when not all finished), how many are done, each
        video with its result file or what went wrong, all files made, and Prøv igen for the rest."""
        app, k = self.app, self.app.scale
        items = self.summary_items
        n, done = len(items), sum(1 for i in items if i.state == "done")
        failed = n - done
        app._last_result = ("queue", None)
        app.d_banner.configure(image=IMG.get("art/done-banner" if not failed else "art/continue-banner"))
        for w in (app.d_icon, app.d_where, app.d_stats, app.d_msg, app.d_error, app.d_extra, app.d_acts,
                  app.d_again, app.d_same, app.d_steps):
            w.grid_remove()
        for w in (app.d_log, app.d_folder, app.d_play):
            w.pack_forget()
        app.d_target = None
        title = ("queue.done_title" if not failed else "queue.stopped_title" if self.summary_stopped
                 else "queue.problems_title")
        app.d_title.configure(text=t(title), foreground=P.heading if not failed else P.warning)
        app.d_file.configure(text=t("queue.count", done=done, n=n))
        app.d_file.grid()
        app.status.configure(text=t(title))
        # The videos
        self.hide_summary()
        box = self.summary = tk.Frame(app.d_main, background=P.card, borderwidth=0)
        box.grid(row=8, column=0, sticky="ew", pady=(int(14 * k), 0))
        box.columnconfigure(2, weight=1)
        texts = []
        for r, item in enumerate(items):
            box.rowconfigure(2 * r, minsize=int(26 * k))
            tk.Label(box, image=IMG.get(f"icons/{state_icon(item)}"), background=P.card,
                     borderwidth=0).grid(row=2 * r, column=0, rowspan=2, sticky="nw", pady=(int(4 * k), 0))
            tk.Label(box, text=str(r + 1), foreground=P.hint, background=P.card, borderwidth=0, pady=0,
                     font=(theme.PIXEL_FONT, -int(10 * k))).grid(row=2 * r, column=1, sticky="w",
                                                                padx=(int(10 * k), 0))
            tk.Label(box, text=Path(item.video).name, foreground=P.text, background=P.card, anchor="w",
                     font="SunValleyBodyStrongFont", borderwidth=0, padx=0, pady=0).grid(
                row=2 * r, column=2, sticky="ew", padx=(int(10 * k), 0))
            result = Path(item.result) if item.result else None
            if item.state == "done":
                line = result.name if result else t("queue.nothing_new")
                colour = P.hint
            else:
                line = state_text(item, self.summary_stopped) + (f": {item.message}" if item.message else "")
                colour = P.error if item.state == "error" else P.hint
            sub = tk.Label(box, text=line, foreground=colour, background=P.card, anchor="w", justify="left",
                           font=("Segoe UI", -int(13 * k)), borderwidth=0, padx=0, pady=0)
            sub.grid(row=2 * r + 1, column=2, sticky="ew", padx=(int(10 * k), 0), pady=(0, int(10 * k)))
            texts.append(sub)
            if item.state == "done" and result:
                tools = tk.Frame(box, background=P.card, borderwidth=0)
                tools.grid(row=2 * r, column=3, rowspan=2, sticky="ne", padx=(int(10 * k), 0))
                video = result.suffix.lower() != ".srt"
                b1 = ttk.Button(tools, image=IMG.get("icons/play"), style="Card.Tool.TButton",
                                command=lambda p=result: app._open(p))
                b1.grid(row=0, column=0)
                Tooltip(b1, t("gui.done.play" if video else "gui.done.open"))
                b2 = ttk.Button(tools, image=IMG.get("icons/show-in-folder"), style="Card.Tool.TButton",
                                command=lambda p=result: app._show_in_folder(p))
                b2.grid(row=0, column=1, padx=(int(6 * k), 0))
                Tooltip(b2, t("gui.show_in_folder"))

        def wrap(e=None):
            width = box.winfo_width() - int(160 * k)
            if width > 80:
                for x in texts:
                    x.configure(wraplength=width)
        box.bind("<Configure>", wrap, add="+")
        # Learned words, from all the videos
        words = list(dict.fromkeys(f"{w} → {r}" for i in items for w, r in i.words))
        if words:
            app.d_msg.configure(text=t("gui.done.learned", words=", ".join(words)))
            app.d_msg.grid()
        # All files from the queue
        app.files.delete(0, "end")
        app.created = []
        for i in items:
            for f in i.files:
                p = Path(f)
                if p not in app.created:
                    app.created.append(p)
                    app.files.insert("end", p.name)
        app.d_files.configure(text=t("queue.group_files"))
        app.d_files.grid() if app.created else app.d_files.grid_remove()
        # Hvad nu?
        if failed:
            app.d_again.configure(text=t("queue.retry_one") if failed == 1 else t("queue.retry", n=failed))
            app.d_again.grid()
            app.d_next_hint.configure(text=t("queue.next_hint_retry"))
        else:
            app.d_next_hint.configure(text=t("queue.next_hint"))
        app._show("done")
        skin.skin_cards(app.views["done"])
        if not replay:
            if app._skill_warning:
                reason, have = app._skill_warning
                app._skill_warning = None
                app.root.after(1200, lambda: app.show_skill_guide(reason, have))
            app._call_attention()
