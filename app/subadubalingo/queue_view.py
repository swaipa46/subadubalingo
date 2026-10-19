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
from .common import LANG_BY_CODE, LANGUAGES, data_dir, log
from .i18n import t
from .pickers import Tooltip
from .theme import IMG, P

PHASES = ("listen", "claude", "make")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv"}
FINISHED = ("done", "error", "cancelled", "skipped")
AFTER = ("nothing", "shutdown", "sleep")   # Når køen er færdig: Gør ingenting / Sluk pc'en / Gå i dvale


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
    removed_ms: int = 0            # how much the cut took away
    length_ms: int | None = None   # the result's length, for the summary

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
        self.auto_cuts = False                          # take Claude's cut suggestions without the review
        self.after = "nothing"                          # when the queue is done: "nothing", "shutdown" or "sleep"
        self.started = 0.0                              # time.time() at the start, for "timer brugt"

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

    def later_round(self) -> bool:
        """Whether the round running now is not the current video's last (PHASES: listen, claude, make)."""
        return bool(self.current) and self.current[1] < len(PHASES) - 1

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
                                       "auto_cuts": self.auto_cuts, "after": self.after, "started": self.started,
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
        q.auto_cuts = bool(data.get("auto_cuts"))
        q.after = data.get("after") if data.get("after") in AFTER else "nothing"
        q.started = float(data.get("started") or time.time())
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

# CFUNCTYPE where there is no WINFUNCTYPE: lets tests import the GUI on Linux
_WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                                            ctypes.c_size_t, ctypes.c_ssize_t)
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



# --- Helpers for the views -------------------------------------------------------------------------------------------

def icon(name: str, fallback: str) -> str:
    """An icon of the T8 set (queue, grip ...), or an older one in a theme that does not have it yet."""
    return name if IMG.path(f"icons/{name}").exists() else fallback


def clock(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def length_text(ms: int) -> str:
    """"2 t 40 min" / "18 min" in the app's language."""
    m = max(1, round(ms / 60000))
    return t("queue.len_hm", h=m // 60, m=m % 60) if m >= 60 else t("queue.len_m", m=m)


def power_off(kind: str):
    """Når køen er færdig: shut the pc down ("shutdown") or hibernate it ("sleep"; sleep when hibernation is off)."""
    import subprocess
    from .common import NO_WINDOW
    log.info("queue done: %s", kind)
    if kind == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=NO_WINDOW)
    elif kind == "sleep":
        r = subprocess.run(["shutdown", "/h"], capture_output=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            ctypes.windll.powrprof.SetSuspendState(False, True, False)


def _power(kind: str):
    power_off(kind)   # looked up at call time, so a test can replace it


class AfterDialog(tk.Toplevel):
    """A minute's warning before the pc is shut down or hibernated after the queue; Annullér is the default."""
    SECONDS = 60

    def __init__(self, app, kind: str):
        from .gui import APP_NAME, place_over
        super().__init__(app.root)
        self.withdraw()
        self.app, self.kind, self.left = app, kind, self.SECONDS
        self.title(APP_NAME)
        self.transient(app.root)
        self.resizable(False, False)
        k = app.scale
        f = ttk.Frame(self, padding=(int(24 * k), int(20 * k)))
        f.pack(fill="both", expand=True)
        self.msg = ttk.Label(f, text="", wraplength=int(380 * k), justify="left", font="SunValleyBodyStrongFont")
        self.msg.pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=(int(18 * k), 0))
        skin.IconButton(row, "cancel", text=t("gui.cancel"), style="Accent.TButton",
                        command=self.cancel).pack(side="left")
        ttk.Button(row, text=t("queue.after_now"), command=self.go).pack(side="left", padx=(int(12 * k), 0))
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self._tick_id = None
        place_over(self, app.root)
        self._count()
        app._call_attention()

    def _count(self):
        self.msg.configure(text=t(f"queue.after_{self.kind}", s=self.left))
        if self.left <= 0:
            self.go()
            return
        self.left -= 1
        self._tick_id = self.after(1000, self._count)

    def go(self):
        self._stop()
        _power(self.kind)

    def cancel(self):
        log.info("queue done: %s cancelled", self.kind)
        self._stop()

    def _stop(self):
        if self._tick_id:
            self.after_cancel(self._tick_id)
            self._tick_id = None
        self.destroy()


# --- The controller in the app ---------------------------------------------------------------------------------------

class QueueController:
    """The queue's part of the App: the list in the setup view, the run of the rounds, the work view's queue card,
    the done view's summary, dropping files and resuming after a restart. The App calls in at a few points (the
    `self.vq` lines in gui.py); everything else stays in the App as for one video.

    Layout as in the design system's mockups queue-setup/-work/-done (assets/ui/README.md "Queue")."""

    def __init__(self, app):
        self.app = app
        self.q = VideoQueue()
        self._loading = False            # the form is being filled from an item (for a round's job)
        self._shown = False              # the setup view shows the queue
        self.summary_items: list[Item] = []
        self.summary_stopped = False
        self.summary_elapsed = 0.0
        self.drop: DropTarget | None = None
        self._job_meta: dict = {}        # the round's job: cut planned, the cut video's path
        self.box = self.work_card = self.summary = None
        self._summary_widgets: list = []
        self._rows: list[dict] = []
        self._work_rows: list[dict] = []
        self._durations: dict[str, int | None] = {}
        self._new_durations = False
        self._drag_from: int | None = None
        self._frame = 0
        self._ticking = False
        self.auto_cuts = tk.BooleanVar(master=app.root, value=bool(getattr(app.settings, "queue_auto_cuts", False)))

    @property
    def active(self) -> bool:
        """The setup view shows the queue (two videos or more)."""
        return len(self.q.items) >= 2

    @property
    def running(self) -> bool:
        return self.q.running

    # --- The setup view --------------------------------------------------------------------------------------------

    def build_setup(self, g1):
        """The list of videos in card 1 (in place of the one chosen video, from the second video on) and the queue's
        own choices in card 2. Called by App._build_setup, also after a theme or language switch."""
        app, k = self.app, self.app.scale
        style = ttk.Style(app.root)
        style.configure("Tool.TButton", padding=(int(9 * k), int(5 * k), int(12 * k), int(8 * k)))
        style.configure("Card.Tool.TButton", background=P.card)
        Tooltip(app.v_btn, lambda: t("queue.pick_tooltip"))
        self._shown = False
        self._rows = []
        box = self.box = ttk.Frame(g1)
        box.grid(row=0, column=0, columnspan=2, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.rows_frame = tk.Frame(box, background=P.card, borderwidth=0, highlightthickness=0)
        self.rows_frame.grid(row=0, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(box)
        bar.grid(row=1, column=0, sticky="ew", pady=(int(14 * k), 0))
        bar.columnconfigure(1, weight=1)
        skin.IconButton(bar, "plus", text=t("queue.add"), command=self.add_dialog).grid(row=0, column=0, sticky="w")
        hint = ttk.Label(bar, text=t("queue.add_hint"), style="Hint.TLabel", justify="left")
        hint.grid(row=0, column=1, sticky="ew", padx=(int(16 * k), 0))
        app._wrap(hint, bar, int(220 * k))
        box.grid_remove()
        # "Træk i ⠿ for at ændre rækkefølgen" at the card's top right, level with its title
        self.drag_hint = tk.Label(g1, text=t("queue.drag_hint"), background=P.card, foreground=P.hint,
                                  font=("Segoe UI", -int(12 * k)), borderwidth=0, padx=0, pady=0)
        # Card 2: what the single-video form shows per video moves into the rows; these are for all the videos
        g2 = app.g_output
        texts = (t("gui.setup.speech"), t("gui.setup.subtitles"))
        labels = [w for w in g2.winfo_children() if w.winfo_class() == "TLabel" and str(w.cget("text")) in texts]
        # Card 1's "Hvilket sprog tales på videoen?" row: each video has its own in the list
        lang_row = [w for w in g1.grid_slaves(row=1) if w is not box]
        # The speech combo sits in a frame with Hør stemmen's button (voice_preview): hide the frame, button and all
        speech = app.c_speech.master if app.c_speech.master is not g2 else app.c_speech
        self._single_only = lang_row + labels + [speech, app.c_subs, app.cb_redo, app.redo_hint, app.cb_recut,
                                                 app.recut_hint, app.plan_row, app.plan_steps]
        opts = self.opts = ttk.Frame(g2)
        opts.grid(row=40, column=0, columnspan=2, sticky="ew", pady=(int(4 * k), 0))
        opts.columnconfigure(0, weight=1)
        indent = int(30 * k)
        self.cb_auto = ttk.Checkbutton(opts, variable=self.auto_cuts, text=t("queue.auto_cuts"),
                                       command=self._auto_changed)
        self.cb_auto.grid(row=0, column=0, sticky="w")
        auto_hint = self.auto_hint = ttk.Label(opts, text=t("queue.auto_cuts_hint"), style="Hint.TLabel", justify="left")
        auto_hint.grid(row=1, column=0, sticky="ew", padx=(indent, 0))
        app._wrap(auto_hint, opts, 40 + indent)
        after = ttk.Frame(opts)
        after.grid(row=2, column=0, sticky="w", pady=(int(16 * k), 0))
        ttk.Label(after, text=t("queue.after_label")).pack(side="left")
        self.after_box = ttk.Combobox(after, state="readonly", width=18,
                                      values=[t(f"queue.after.{a}") for a in AFTER])
        self.after_box.current(AFTER.index(self.q.after) if self.q.after in AFTER else 0)
        self.after_box.pack(side="left", padx=(int(10 * k), 0))
        opts.grid_remove()

    def _auto_changed(self):
        self.app.settings.queue_auto_cuts = bool(self.auto_cuts.get())
        self.app.settings.save()

    def _columns(self, frame):
        """The same columns in the header and every row: grip, number, name (flexible), spoken language, arrow, new
        speech, subtitles, remove."""
        k = self.app.scale
        for col, width in ((0, 30), (1, 26), (3, 110), (4, 30), (5, 140), (6, 160), (7, 52)):
            frame.columnconfigure(col, minsize=int(width * k))
        frame.columnconfigure(2, weight=1)

    def _speech_items(self, item: Item) -> list[tuple[str | None, str]]:
        none = t("gui.setup.subs_none")
        return [(None, t("queue.speech_item", name=none))] + [
            (x.code, t("queue.speech_item", name=x.name)) for x in LANGUAGES if x.code != item.lang]

    @staticmethod
    def _subs_items() -> list[tuple[str | None, str]]:
        return [(None, t("queue.subs_item", name=t("gui.setup.subs_none")))] + [
            (x.code, t("queue.subs_item", name=x.word("choice"))) for x in LANGUAGES]

    def _draw_rows(self):
        """A header and one 52 px row per video, as in queue-setup.png: grip and number, name and length, the three
        language choices, remove; a dashed line under each."""
        k = self.app.scale
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._rows = []
        head = tk.Frame(self.rows_frame, background=P.card, borderwidth=0)
        head.grid(row=0, column=0, sticky="ew", pady=(0, int(4 * k)))
        self._columns(head)
        for col, key in ((2, "queue.col_video"), (3, "queue.col_lang"), (5, "queue.col_speech"),
                         (6, "queue.col_subs")):
            tk.Label(head, text=t(key), background=P.card, foreground=P.hint, font=("Segoe UI", -int(12 * k)),
                     borderwidth=0, padx=0, pady=0, anchor="w").grid(
                row=0, column=col, sticky="w", padx=(int(4 * k) if col == 2 else int(10 * k) if col == 6 else 0, 0))
        grip_img = IMG.get(f"icons/{icon('grip', 'minus')}")
        for i, item in enumerate(self.q.items):
            row = tk.Frame(self.rows_frame, background=P.card, borderwidth=0, highlightthickness=0,
                           height=int(52 * k))
            row.grid(row=2 * i + 1, column=0, sticky="ew")
            row.grid_propagate(False)
            row.rowconfigure(0, weight=1)
            self._columns(row)
            grip = tk.Label(row, image=grip_img, background=P.card, borderwidth=0, cursor="fleur", takefocus=1)
            grip.grid(row=0, column=0)
            num = tk.Label(row, text=str(i + 1), foreground=P.heading, background=P.card, borderwidth=0, pady=0,
                           font=(theme.PIXEL_FONT, -int(10 * k)))
            num.grid(row=0, column=1, sticky="w")
            names = tk.Frame(row, background=P.card, borderwidth=0)
            names.grid(row=0, column=2, sticky="ew", padx=(int(4 * k), int(12 * k)))
            name = tk.Label(names, text=Path(item.video).name, background=P.card, foreground=P.text, borderwidth=0,
                            padx=0, pady=0, font="SunValleyBodyStrongFont", anchor="w")
            name.pack(fill="x")
            dur = tk.Label(names, text="", background=P.card, foreground=P.hint, borderwidth=0, padx=0, pady=0,
                           font=("Segoe UI", -int(12 * k)), anchor="w")
            dur.pack(fill="x")
            c_lang = ttk.Combobox(row, state="readonly", width=1, values=[x.name for x in LANGUAGES])
            c_lang.grid(row=0, column=3, sticky="ew")
            c_lang.current(next((n for n, x in enumerate(LANGUAGES) if x.code == item.lang), 0))
            arrow = tk.Label(row, text="→", background=P.card, foreground=P.hint, borderwidth=0)
            arrow.grid(row=0, column=4)
            c_speech = ttk.Combobox(row, state="readonly", width=1)
            c_speech.grid(row=0, column=5, sticky="ew")
            c_subs = ttk.Combobox(row, state="readonly", width=1, values=[lb for _, lb in self._subs_items()])
            c_subs.grid(row=0, column=6, sticky="ew", padx=(int(10 * k), 0))
            c_subs.current(next((n for n, (c, _) in enumerate(self._subs_items()) if c == item.subs), 0))
            rm = ttk.Button(row, image=IMG.get("icons/cancel"), style="Card.Tool.TButton",
                            command=lambda n_=i: self.remove(n_))
            rm.grid(row=0, column=7, sticky="e", padx=(int(10 * k), int(2 * k)))
            Tooltip(rm, t("queue.remove"))
            line = tk.Canvas(self.rows_frame, height=max(2, int(2 * k)), highlightthickness=0, borderwidth=0,
                             background=P.card)
            line.grid(row=2 * i + 2, column=0, sticky="ew")
            line.bind("<Configure>", lambda e, c=line: (c.delete("all"), c.create_line(
                0, 1, e.width, 1, fill=P.step_pending_icon, width=max(2, int(2 * k)), dash=(4, 4))))
            r = {"frame": row, "plain": [row, grip, num, names, name, dur, arrow], "dur": dur, "item": item,
                 "lang": c_lang, "speech": c_speech, "subs": c_subs, "grip": grip}
            self._rows.append(r)
            self._fill_speech(r)
            c_lang.bind("<<ComboboxSelected>>", lambda e, r_=r: self._lang_picked(r_))
            c_speech.bind("<<ComboboxSelected>>", lambda e, r_=r: self._speech_picked(r_))
            c_subs.bind("<<ComboboxSelected>>", lambda e, r_=r: self._subs_picked(r_))
            for w in r["plain"]:
                w.bind("<Enter>", lambda e, r_=r: self._hover(r_, True), add="+")
                w.bind("<Leave>", lambda e, r_=r: self._hover(r_, False), add="+")
            grip.bind("<ButtonPress-1>", lambda e, n_=i: self._drag_start(n_))
            grip.bind("<B1-Motion>", self._drag_motion)
            grip.bind("<ButtonRelease-1>", self._drag_end)
            grip.bind("<Alt-Up>", lambda e, n_=i: self.move(n_, -1))
            grip.bind("<Alt-Down>", lambda e, n_=i: self.move(n_, 1))
            grip.bind("<Delete>", lambda e, n_=i: self.remove(n_))
        self._fill_durations()

    def _fill_speech(self, r: dict):
        items = self._speech_items(r["item"])
        r["speech"].configure(values=[lb for _, lb in items])
        r["speech"].current(next((n for n, (c, _) in enumerate(items) if c == r["item"].speech), 0))

    def _lang_picked(self, r: dict):
        item = r["item"]
        item.lang = LANGUAGES[r["lang"].current()].code
        if item.speech == item.lang:
            item.speech = None
        self._fill_speech(r)
        self.refresh_setup()

    def _speech_picked(self, r: dict):
        r["item"].speech = self._speech_items(r["item"])[r["speech"].current()][0]
        self.refresh_setup()

    def _subs_picked(self, r: dict):
        r["item"].subs = self._subs_items()[r["subs"].current()][0]
        self.refresh_setup()

    def _hover(self, r: dict, on: bool):
        if self._drag_from is not None:
            return
        if not on:   # Leave also fires when the pointer moves onto one of the row's own widgets
            try:
                w = r["frame"].winfo_containing(*r["frame"].winfo_pointerxy())
            except (tk.TclError, KeyError):
                w = None
            on = w is not None and str(w).startswith(str(r["frame"]))
        self._paint(r, P.picker_hover_bg if on else P.card)

    @staticmethod
    def _paint(r: dict, colour: str):
        for w in r["plain"]:
            try:
                w.configure(background=colour)
            except tk.TclError:
                pass

    # Reordering by dragging the grip (or Alt+Up/Down on it)
    def _drag_start(self, index: int):
        self._drag_from = index

    def _row_at(self, y_root: int) -> int | None:
        if not self._rows:
            return None
        for n, r in enumerate(self._rows):
            top = r["frame"].winfo_rooty()
            if top <= y_root < top + r["frame"].winfo_height() + 4:
                return n
        return 0 if y_root < self._rows[0]["frame"].winfo_rooty() else len(self._rows) - 1

    def _drag_motion(self, e):
        if self._drag_from is None:
            return
        target = self._row_at(e.y_root)
        for n, r in enumerate(self._rows):
            self._paint(r, P.picker_hover_bg if n in (target, self._drag_from) else P.card)

    def _drag_end(self, e):
        start, self._drag_from = self._drag_from, None
        target = self._row_at(e.y_root)
        if start is None or target is None or target == start:
            for r in self._rows:
                self._paint(r, P.card)
            return
        self.q.items.insert(target, self.q.items.pop(start))
        self._redraw()

    def move(self, index: int, delta: int):
        j = self.q.move(index, delta)
        self._redraw()
        if j < len(self._rows):
            self._rows[j]["grip"].focus_set()
        return "break"

    def remove(self, index: int):
        if self.running:
            return
        self.q.remove(index)
        if len(self.q.items) == 1:   # back to one video: the setup view as it always was
            only = self.q.items.pop()
            self._draw_rows()
            self.load(only)
            return
        self._redraw()

    def _redraw(self):
        self._draw_rows()
        skin.skin_cards(self.app.g_video)
        self.refresh_setup()

    # Video lengths, found in the background (ffprobe)
    def _fill_durations(self):
        import threading
        from .common import Runner
        from .engine import probe
        for r in self._rows:
            v = r["item"].video
            if not Path(v).is_file():
                r["dur"].configure(text=t("gui.setup.video_gone"), foreground=P.error)
                continue
            if v not in self._durations:
                self._durations[v] = None

                def find(v=v):
                    try:
                        self._durations[v] = probe(Runner(), Path(v)).duration_ms
                    except Exception:
                        log.info("no length for %s", v)
                    self._new_durations = True
                threading.Thread(target=find, daemon=True).start()
            ms = self._durations.get(v)
            r["dur"].configure(text=clock(ms) if ms else "", foreground=P.hint)

    def refresh_setup(self):
        """Called at the end of App._refresh: one video as before, or the queue."""
        app = self.app
        if self.box is None:
            return
        if not self.active:
            if self._shown:
                self._shown = False
                self.box.grid_remove()
                self.drag_hint.place_forget()
                self.opts.grid_remove()
                app.v_card.grid()
                for w in self._single_only:
                    if w not in (app.cb_redo, app.redo_hint, app.cb_recut, app.recut_hint, app.plan_row):
                        w.grid()   # the others are shown by App._refresh when they apply
                app.g_video.configure(text=t("gui.setup.group_video"))
                app.g_output.configure(text=t("gui.setup.group_output"))
                app.cb_cut.configure(text=t("gui.setup.cut_first"))
                app.start_btn.icon = "play"
                app.start_btn.configure(text=t("gui.setup.start"), style="Main.Accent.TButton")
                app.start_hint.configure(text=t("gui.setup.start_hint"))
                app._refresh()
            return
        k = app.scale
        if not self._shown:
            self._shown = True
            app.v_card.grid_remove()
            self.box.grid()
            self.opts.grid()
            _, top, right, _ = app.g_video._pad
            self.drag_hint.place(relx=1.0, x=-right, y=top + int(4 * k), anchor="ne", bordermode="outside")
            app.cb_cut.configure(text=t("queue.cut_all"))
            app.start_btn.icon = icon("queue", "play")
            app.start_btn.configure(text=t("queue.start"), style="Main.Accent.TButton")
        for w in self._single_only:
            w.grid_remove()
        app.g_video.configure(text=t("queue.group_video", n=len(self.q.items)))
        app.g_output.configure(text=t("queue.group_output"))
        if not app.cb_improve.instate(["disabled"]):   # Forbedr lyden: all the videos (design review round 4)
            app.cb_improve.hint.configure(text=t("audio.queue_hint"))
        if [r["item"] for r in self._rows] != self.q.items:
            self._draw_rows()
            skin.skin_cards(app.g_video)
        for w in (self.cb_auto, self.auto_hint):   # only with "Klip videoerne først" (a disabled box looks checked)
            w.grid() if app.cut.get() else w.grid_remove()
        self.cb_auto.state(["!disabled"] if app.cut.get() else ["disabled"])
        known = [self._durations.get(i.video) for i in self.q.items]
        n = len(self.q.items)
        count = (t("queue.start_count", n=n, length=length_text(sum(known))) if all(known)
                 else t("queue.start_count_n", n=n))
        app.start_hint.configure(text=count + " " + t("queue.start_hint"))
        ok = any(Path(i.video).is_file() for i in self.q.items)
        app.start_btn.configure(state="normal" if ok and not self.running else "disabled")

    def _form_choices(self) -> tuple:
        app = self.app
        sp, su = app._speech_lang(), app._subs_lang()
        return (app._lang().code, sp.code if sp else None, su.code if su else None, bool(app.cut.get()),
                bool(app.redo.get()), bool(app.recut.get()))

    def load(self, item: Item, speech="keep", subs="keep", cut=None, redo=None, recut=None):
        """Fill the setup form with a video's choices (or the given overrides for a round): App._validate reads it."""
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

    # Adding videos
    def picked(self, paths) -> bool:
        """The file dialog of Vælg video / Skift video: one file is handled by the App as always; several make a
        queue of them (with the current choices). Returns True when handled here."""
        videos = [Path(p) for p in paths]
        if len(videos) < 2:
            return False
        lang, speech, subs, cut, _, _ = self._form_choices()
        self.q = VideoQueue()
        self.q.add(videos, (lang, speech, subs, cut, False, False))
        self._redraw()
        return True

    def add_dialog(self):
        from .gui import video_types
        app = self.app
        ps = filedialog.askopenfilenames(parent=app.root, title=t("queue.add_title"), filetypes=video_types(),
                                         initialdir=app.settings.last_dir)
        if ps:
            app.settings.last_dir = str(Path(ps[0]).parent)
            app.settings.save()
            self.add(ps)

    def add(self, paths):
        """Add videos (picked or dropped): one video already chosen becomes the first in a new queue. The new ones get
        the current choices (the last video's in the queue), as new videos (not listened to or cut again)."""
        app = self.app
        videos = videos_in(paths)
        if not videos:
            return
        if self.running:   # they join the running queue
            lang, speech, subs, cut, _, _ = self.q.items[-1].choices()
            if self.q.add(videos, (lang, speech, subs, cut, False, False)):
                self.q.save()
                self.update_work()
            return
        if app._running or app._starting:
            return
        fresh = app.view == "done"   # after a run: the dropped videos start a new choice, without the old one
        if fresh:
            app._show("setup")
        if self.active:
            lang, speech, subs, cut, _, _ = self.q.items[-1].choices()
            self.q.add(videos, (lang, speech, subs, cut, False, False))
            self._redraw()
            return
        current = None if fresh else app._video()
        if not current or not current.is_file():
            current = None
            if len(videos) == 1:   # nothing chosen yet: as Vælg video
                app.redo.set(False)
                app.recut.set(False)
                app.video.set(str(videos[0]))
                return
        lang, speech, subs, cut, _, _ = self._form_choices()
        self.q = VideoQueue()
        self.q.add(([current] if current else []) + videos, (lang, speech, subs, cut, False, False))
        if len(self.q.items) < 2:
            self.q.items = []
            return
        self._redraw()

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
        """Called by App._poll: dropped files, and video lengths found meanwhile."""
        if self._new_durations:
            self._new_durations = False
            try:
                if self._shown:
                    self._fill_durations()
                    self.refresh_setup()
            except tk.TclError:
                pass
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
        cut = bool(app.cut.get())
        for item in self.q.items:
            item.cut, item.redo, item.recut = cut, False, False
        finals = {}
        for item in self.q.items:
            self.load(item)
            job = app._validate(auto=False)
            if not job:
                self.refresh_setup()
                return
            finals[item.id] = str(job["result"])
        for item in self.q.items:
            item.phase, item.state, item.result, item.files, item.words = 0, "waiting", None, [], []
            item.message, item.no_cut, item.final, item.removed_ms = "", False, finals.get(item.id), 0
        self.q.running, self.q.stopped = True, False
        self.q.auto_cuts = bool(cut and self.auto_cuts.get())
        self.q.after = AFTER[max(0, self.after_box.current())]
        self.q.started = time.time()
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
            job["auto_cuts"] = self.q.auto_cuts
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
        # The queue card replaces the step list (queue-work.png); the buttons say what they act on
        app.steps_box.grid_remove()
        app.w_result.grid_remove()
        app.pause_btn.configure(text=t("queue.pause"))
        app.cancel_btn.configure(text=t("queue.cancel_one"))
        self.update_work()
        if not self._ticking:
            self._ticking = True
            self._tick()

    def later_round(self) -> bool:
        """Whether the queue's current video has a round after the one running now (the video is made later)."""
        return self.running and self.q.later_round()

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
        cut_made = bool(self._job_meta.get("cut") and self._job_meta.get("edited") in app.created)
        no_cut = kind == "done" and PHASES[phase] == "claude" and self._job_meta.get("cut") and not cut_made
        self.q.end(item, phase, kind, info, files, app.learned_words, no_cut=bool(no_cut))
        if cut_made and app._review_result:
            try:
                from . import cutting
                item.removed_ms = cutting.removed_ms(cutting.active(app._review_result, 10 ** 9))
            except Exception:
                log.exception("cut stats")
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
        items = [Item.from_json(asdict(i)) for i in self.q.items]
        for i in items:   # the result's length for the summary: the original's, unless it was cut
            if i.state == "done" and i.result and Path(i.result).suffix.lower() != ".srt":
                if i.removed_ms:
                    try:
                        from .common import Runner
                        from .engine import probe
                        i.length_ms = probe(Runner(), Path(i.result)).duration_ms
                    except Exception:
                        log.info("no length for %s", i.result)
                else:
                    i.length_ms = self._durations.get(i.video)
        self.summary_items, self.summary_stopped = items, self.q.stopped
        self.summary_elapsed = time.time() - self.q.started if self.q.started else 0
        after = self.q.after if not self.q.stopped and any(i.state == "done" for i in items) else "nothing"
        self.q.running = False
        self.q.current = None
        self.q.forget()
        self.q = VideoQueue()
        self._ticking = False
        app.level = 1
        app.steps_box.grid()
        app.w_result.grid()
        app.cancel_btn.configure(text=t("gui.cancel"))
        self.update_work()
        if items:   # the form as the user left it for the last video, not as its last round needed it
            self.load(items[-1])
        app._show_result("queue", None)
        if app._theme_pending or app._language_pending:
            app.root.after(300, app._apply_pending)
        if after != "nothing":
            AfterDialog(app, after)

    def retry(self, only: Item | None = None):
        """Prøv igen on the summary: the videos that did not finish (or one of them), as a new queue, or one video
        as always."""
        app = self.app
        again = [only.fresh()] if only else [i.fresh() for i in self.summary_items if i.state != "done"]
        if not again:
            return
        app._show("setup")
        self.q = VideoQueue()
        if len(again) == 1:
            self._draw_rows()
            self.load(again[0])
            app._start()
            return
        self.q.items = again
        self._redraw()
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
        self._redraw()
        log.info("queue resumed: %d videos, %d left", len(q.items), left)
        self.next()

    # --- The work view: the queue card -----------------------------------------------------------------------------

    def build_work(self, parent, row: int = 0):
        """The "Køen" card at the top of the work view (queue-work.png); the view's own rows move down one."""
        app, k = self.app, self.app.scale
        for w in parent.grid_slaves():
            w.grid_configure(row=int(w.grid_info()["row"]) + 1)
        app.callout.grid()   # kept its place while hidden: move it down too
        app.callout.grid_configure(row=int(app.callout.grid_info()["row"]) + 1)
        app.callout.grid_remove()
        card = self.work_card = skin.Card(parent, text=t("queue.work_title"), padding=14)
        card.grid(row=row, column=0, sticky="ew", pady=(0, int(22 * k)))
        card.columnconfigure(0, weight=1)
        self.work_count = tk.Label(card, text="", background=P.card, foreground=P.heading, borderwidth=0,
                                   font=(theme.PIXEL_FONT, -int(12 * k)))
        _, top, right, _ = card._pad
        self.work_count.place(relx=1.0, x=-right, y=top + int(4 * k), anchor="ne", bordermode="outside")
        self.work_rows_frame = tk.Frame(card, background=P.card, borderwidth=0)
        self.work_rows_frame.grid(row=0, column=0, sticky="ew")
        self.work_rows_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(card)
        bar.grid(row=1, column=0, sticky="ew", pady=(int(12 * k), 0))
        bar.columnconfigure(1, weight=1)
        skin.IconButton(bar, "plus", text=t("queue.add"), command=self.add_dialog).grid(row=0, column=0, sticky="w")
        hint = ttk.Label(bar, text=t("queue.work_hint"), style="Hint.TLabel", justify="left")
        hint.grid(row=0, column=1, sticky="ew", padx=(int(16 * k), 0))
        app._wrap(hint, bar, int(220 * k))
        self._work_rows = []
        card.grid_remove()

    def _status(self, item: Item, now: bool) -> tuple[str, str, str]:
        """(text, colour, icon) for a row of the queue card."""
        app = self.app
        if now and item.state == "running":
            if app._review is not None:
                return t("queue.state.turn_review"), P.your_turn_title, f"step-your-turn-{self._frame // 2 % 2}"
            if app._tdialog is not None:
                return t("queue.state.turn_claude"), P.your_turn_title, f"step-your-turn-{self._frame // 2 % 2}"
            pct = str(app.w_pct.cget("text"))
            return t("queue.state.running", pct=pct), P.heading, f"step-active-{self._frame % 6}"
        colour = {"done": P.success, "error": P.error}.get(item.state, P.hint)
        icon_name = state_icon(item)
        if item.state == "waiting" and item.phase >= 1 and not self.q.stopped:
            # Listened to (and maybe checked), waiting for its next round: that part is done, so step-done with the
            # text in hint, unlike Venter (step-pending) and the running row (design review round 4, item 8)
            icon_name = "step-done"
        return state_text(item, self.q.stopped), colour, icon_name

    def update_work(self):
        """The queue card: each video with its state; the running one on picker_hover_bg."""
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
        cur = self.q.item(self.q.current[0]) if self.q.current else None
        done = sum(1 for i in items if i.state in FINISHED)
        self.work_count.configure(text=f"{items.index(cur) + 1 if cur in items else done}/{len(items)}")
        if [r["item"] for r in self._work_rows] != items:
            for child in self.work_rows_frame.winfo_children():
                child.destroy()
            self._work_rows = []
            for n, item in enumerate(items):
                row = tk.Frame(self.work_rows_frame, background=P.card, borderwidth=0, height=int(40 * k))
                row.grid(row=n, column=0, sticky="ew")
                row.grid_propagate(False)
                row.rowconfigure(0, weight=1)
                row.columnconfigure(1, weight=1)
                ic = tk.Label(row, background=P.card, borderwidth=0)
                ic.grid(row=0, column=0, padx=(int(8 * k), int(12 * k)))
                name = tk.Label(row, text=Path(item.video).name, background=P.card, foreground=P.text, anchor="w",
                                borderwidth=0, padx=0, pady=0)
                name.grid(row=0, column=1, sticky="ew")
                st = tk.Label(row, background=P.card, borderwidth=0, padx=0, pady=0,
                              font=("Segoe UI Semibold", -int(13 * k)))
                st.grid(row=0, column=2, sticky="e", padx=(int(12 * k), int(10 * k)))
                self._work_rows.append({"item": item, "frame": row, "icon": ic, "name": name, "status": st})
        self._paint_work()
        card.grid()

    def _paint_work(self):
        cur = self.q.item(self.q.current[0]) if self.q.current else None
        for r in self._work_rows:
            now = r["item"] is cur
            text, colour, ic = self._status(r["item"], now)
            bg = P.picker_hover_bg if now else P.card
            for w in (r["frame"], r["icon"], r["name"], r["status"]):
                w.configure(background=bg)
            r["icon"].configure(image=IMG.get(f"icons/{ic}"))
            r["name"].configure(font="SunValleyBodyStrongFont" if now else "SunValleyBodyFont",
                                foreground=P.heading if now else P.text)
            r["status"].configure(text=text, foreground=colour)

    def _tick(self):
        """Keeps the running row's percentage, your-turn state and animation current."""
        if not self.running:
            self._ticking = False
            return
        self._frame += 1
        try:
            self._paint_work()
        except tk.TclError:
            pass
        self.app.root.after(250, self._tick)

    # --- The done view: the summary --------------------------------------------------------------------------------

    def hide_summary(self):
        """Before the App's own done view: no queue summary, and Hvad nu? as it was."""
        app = self.app
        for w in [self.summary] + self._summary_widgets:
            if w is not None:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
        self.summary, self._summary_widgets = None, []
        try:
            app.d_next_hint.master.grid_configure(row=0, pady=0)
            app.d_next_hint.grid()
            app.d_new.icon = "choose-video"
            app.d_new.configure(text=t("gui.done.new_video"), style=str(app.d_new.cget("style")))
        except (AttributeError, tk.TclError):
            pass

    def show_summary(self, replay: bool = False):
        """The done view after a queue (queue-done.png): ALL CLEAR!, "Køen er færdig" with one row per video (its
        result file, how much was cut and the new length, or what went wrong, with Afspil / Vis i mappe or Prøv
        igen), and on the right "I alt" and "Hvad nu?"."""
        app, k = self.app, self.app.scale
        items = self.summary_items
        n, done = len(items), sum(1 for i in items if i.state == "done")
        failed = [i for i in items if i.state != "done"]
        app._last_result = ("queue", None)
        # ALL CLEAR! after a queue (STAGE CLEAR! stays for one video; design review round 4, item 1)
        banner = "queue-banner" if IMG.path("art/queue-banner").exists() else "done-banner"
        app.set_banner(banner if done else "continue-banner")
        self.hide_summary()
        for w in (app.d_icon, app.d_where, app.d_stats, app.d_msg, app.d_error, app.d_extra, app.d_acts,
                  app.d_again, app.d_same, app.d_steps, app.d_files, app.d_file, app.d_next_hint):
            w.grid_remove()
        for w in (app.d_log, app.d_folder, app.d_play):
            w.pack_forget()
        app.d_target = None
        title = t("queue.stopped_title" if self.summary_stopped else "queue.done_title")
        app.d_title.configure(text=title, foreground=P.heading)
        app.status.configure(text=title)
        head = app.d_title.master
        head.columnconfigure(1, weight=1)
        count = ttk.Label(head, text=t("queue.head_count", done=done, n=n), style="Card.Hint.TLabel")
        count.grid(row=0, column=2, sticky="e", padx=(int(16 * k), 0))
        self._summary_widgets.append(count)
        # The videos, in a list that scrolls when the window cannot show them all
        outer = self.summary = tk.Frame(app.d_main, background=P.card, borderwidth=0)
        outer.grid(row=8, column=0, sticky="ew", pady=(int(10 * k), 0))
        outer.columnconfigure(0, weight=1)
        # An explicit height in pixels: without one cget gives Tk's default, a distance string ("7c")
        canvas = tk.Canvas(outer, height=int(110 * k), background=P.card, highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="ew")
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        box = tk.Frame(canvas, background=P.card, borderwidth=0)
        inside = canvas.create_window(0, 0, window=box, anchor="nw")
        box.columnconfigure(0, weight=1)
        texts = []
        for r, item in enumerate(items):
            row = tk.Frame(box, background=P.card, borderwidth=0, height=int(52 * k))
            row.grid(row=2 * r, column=0, sticky="ew")
            row.grid_propagate(False)
            row.rowconfigure(0, weight=1)
            row.columnconfigure(1, weight=1)
            tk.Label(row, image=IMG.get(f"icons/{state_icon(item)}"), background=P.card,
                     borderwidth=0).grid(row=0, column=0, padx=(int(8 * k), int(14 * k)))
            lines = tk.Frame(row, background=P.card, borderwidth=0)
            lines.grid(row=0, column=1, sticky="ew")
            result = Path(item.result) if item.result else None
            shown = result.name if item.state == "done" and result else Path(item.video).name
            tk.Label(lines, text=shown, foreground=P.text, background=P.card, anchor="w",
                     font="SunValleyBodyStrongFont", borderwidth=0, padx=0, pady=0).pack(fill="x")
            if item.state == "done":
                parts = []
                if item.removed_ms:
                    parts.append(t("queue.removed", s=round(item.removed_ms / 1000)))
                if item.length_ms:
                    parts.append(clock(item.length_ms))
                line = " · ".join(parts) if result else t("queue.nothing_new")
                colour = P.hint
            else:
                line = state_text(item, self.summary_stopped) + (f": {item.message}" if item.message else "")
                colour = P.error if item.state == "error" else P.hint
            sub = tk.Label(lines, text=line, foreground=colour, background=P.card, anchor="w", justify="left",
                           font=("Segoe UI", -int(12 * k)), borderwidth=0, padx=0, pady=0)
            sub.pack(fill="x")
            texts.append(sub)
            tools = tk.Frame(row, background=P.card, borderwidth=0)
            tools.grid(row=0, column=2, sticky="e", padx=(int(12 * k), int(4 * k)))
            if item.state == "done" and result:
                video = result.suffix.lower() != ".srt"
                b1 = ttk.Button(tools, image=IMG.get("icons/play"), style="Card.Tool.TButton", width=4,
                                command=lambda p=result: app._open(p))
                b1.grid(row=0, column=0)
                Tooltip(b1, t("gui.done.play" if video else "gui.done.open"))
                b2 = ttk.Button(tools, image=IMG.get("icons/show-in-folder"), style="Card.Tool.TButton", width=4,
                                command=lambda p=result: app._show_in_folder(p))
                b2.grid(row=0, column=1, padx=(int(14 * k), 0))
                Tooltip(b2, t("gui.show_in_folder"))
            elif item.state != "done":
                skin.IconButton(tools, "retry", text=t("gui.done.try_again"), style="Card.Tool.TButton",
                                command=lambda i=item: self.retry(i)).grid(row=0, column=0)
            line_c = tk.Canvas(box, height=max(2, int(2 * k)), highlightthickness=0, borderwidth=0,
                               background=P.card)
            line_c.grid(row=2 * r + 1, column=0, sticky="ew")
            line_c.bind("<Configure>", lambda e, c=line_c: (c.delete("all"), c.create_line(
                0, 1, e.width, 1, fill=P.step_pending_icon, width=max(2, int(2 * k)), dash=(4, 4))))

        def fit(e=None):
            """The list's height: all of it, or what the window has room for (with a scroll bar)."""
            try:
                width = canvas.winfo_width()
                canvas.itemconfigure(inside, width=width)
                for x in texts:
                    x.configure(wraplength=max(80, width - int(220 * k)))
                need = box.winfo_reqheight()
                room = max(int(110 * k), app.root.winfo_height() - canvas.winfo_rooty() + app.root.winfo_rooty()
                           - int(60 * k))
                height = min(need, room)
                if canvas.winfo_pixels(canvas.cget("height")) != height:
                    canvas.configure(height=height)
                canvas.configure(scrollregion=(0, 0, width, need))
                bar.grid(row=0, column=1, sticky="ns") if need > height else bar.grid_remove()
            except tk.TclError:
                pass

        def wheel(e):
            if box.winfo_reqheight() > canvas.winfo_pixels(canvas.cget("height")):
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Configure>", fit, add="+")
        box.bind("<Configure>", fit, add="+")
        outer.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        outer.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        app.root.after(50, fit)
        # Learned words, from all the videos
        words = list(dict.fromkeys(f"{w} → {r}" for i in items for w, r in i.words))
        if words:
            app.d_msg.configure(text=t("gui.done.learned", words=", ".join(words)))
            app.d_msg.grid()
        # All files, for the files list's double-click (the list itself is left out: each row has its result)
        app.files.delete(0, "end")
        app.created = []
        for i in items:
            for f in i.files:
                if Path(f) not in app.created:
                    app.created.append(Path(f))
                    app.files.insert("end", Path(f).name)
        # Right: I alt, then Hvad nu?
        nxt = app.d_next_hint.master
        nxt.grid_configure(row=1, pady=(int(24 * k), 0))
        card_pad = (int(18 * k), int(14 * k), int(22 * k), int(18 * k))
        total = skin.Card(nxt.master, text=t("queue.total"), padding=card_pad)
        total.grid(row=0, column=0, sticky="ew")
        self._summary_widgets.append(total)
        stats = [(f"{done}/{n}", t("queue.stat_done"))]
        removed = sum(i.removed_ms for i in items)
        if removed:
            stats.append((f"-{round(removed / 1000)} s" if removed < 59500 else f"-{clock(removed)}",
                          t("gui.done.stat_cut")))
        mins = round(self.summary_elapsed / 60)
        if mins >= 60:
            stats.append((f"{mins // 60}:{mins % 60:02d}", t("queue.stat_hours")))
        else:
            stats.append((str(max(1, mins)), t("queue.stat_minutes")))
        for c, (value, label) in enumerate(stats):
            tk.Label(total, text=value.upper(), foreground=P.accent if value.startswith("-") else P.heading,
                     background=P.card, borderwidth=0, padx=0, pady=0, font=(theme.PIXEL_FONT, -int(22 * k))).grid(
                row=0, column=c, sticky="w", padx=(0, int(36 * k)))
            ttk.Label(total, text=label, style="Card.Hint.TLabel").grid(row=1, column=c, sticky="w",
                                                                         padx=(0, int(36 * k)), pady=(int(2 * k), 0))
        if failed:
            app.d_again.configure(text=t("queue.retry_named", name=Path(failed[0].video).stem) if len(failed) == 1
                                  else t("queue.retry", n=len(failed)))
            app.d_again.grid()
        app.d_new.icon = icon("queue", "choose-video")
        app.d_new.configure(text=t("queue.new_queue"), style=str(app.d_new.cget("style")))
        folder_of = next((Path(i.result) for i in items if i.result), Path(items[0].video) if items else None)
        open_all = skin.IconButton(nxt, "show-in-folder", text=t("queue.open_folder"), style="Wide.Tall.TButton",
                                   command=lambda: app._show_in_folder(folder_of))
        open_all.grid(row=4, column=0, sticky="ew", pady=(int(14 * k), 0))
        self._summary_widgets.append(open_all)
        app._show("done")
        skin.skin_cards(app.views["done"])
        if not replay:
            app._call_attention()
