"""Danish tkinter GUI. All work runs in a worker thread; the GUI polls a queue."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import sys
import threading
import time
import traceback
import uuid
import webbrowser
import shutil
import ctypes
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import claude, cutting, engine, jobs, textstate
from .common import (APP_NAME, APP_TAGLINE, APP_VERSION, FFMPEG, LANG_BY_NAME, LANGUAGES, NO_WINDOW, PIPER, WHISPER, Cancelled,
                     Cue, Language, Runner, ToolError, burned_name, copy_file_to_clipboard, cut_name, data_dir, downloads_dir,
                     dub_name,
                     free_memory_gb, output_path,
                     log, log_file, model_dirs, read_srt, srt_name, write_srt)

VIDEO_TYPES = [("Video", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.wmv"), ("Alle filer", "*.*")]
PAD = {"padx": 8, "pady": 4}


class Settings:
    path = data_dir() / "settings.json"

    def __init__(self):
        self.whisper_model = "large"
        self.use_gpu = True
        self.keep_original_audio = False   # under a dub you would still hear the original speaker
        self.cut_first = False             # "Klip videoen først", remembered from the last run
        self.gender_voices = True          # (v1.9.6) kept for old settings files; voice_mode replaces it
        self.voice_mode = "person"         # dub: "person" (a voice per person), "gender" (deep/light) or "one"
        self.skill_installed = 0           # the Claude skill version the user has put into Claude (0 = unknown)
        self.last_dir = str(Path.home() / "Videos")
        try:
            self.__dict__.update(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            pass
        self.__dict__.pop("model", None)  # v1.0.0 key; the default changed from turbo to large-v3
        self.__dict__.pop("keep_original", None)  # before v1.3.2 on by default; now off
        if self.whisper_model not in engine.WHISPER_MODELS:
            self.whisper_model = "large"

    def save(self):
        try:
            self.path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
        except Exception:
            log.exception("could not save settings")


def apply_style(root: tk.Tk):
    """Windows 11 look (Sun Valley theme) with point-size fonts, so text scales with the display."""
    try:
        import sv_ttk
        sv_ttk.set_theme("light", root)
    except Exception:
        log.exception("sv_ttk not available; using the default theme")
    sizes = {"SunValleyCaptionFont": 9, "SunValleyBodyFont": 11, "SunValleyBodyStrongFont": 11,
             "SunValleyBodyLargeFont": 13, "SunValleySubtitleFont": 15, "SunValleyTitleFont": 20,
             "TkDefaultFont": 11, "TkTextFont": 11, "TkHeadingFont": 11, "TkMenuFont": 11}
    for name, size in sizes.items():
        try:
            tkfont.nametofont(name).configure(size=size)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    style.configure("Title.TLabel", font="SunValleyTitleFont", foreground=ACCENT_DARK)
    style.configure("Section.TLabel", font="SunValleySubtitleFont", foreground=ACCENT_DARK)
    style.configure("Hint.TLabel", foreground="#5f5f5f")
    style.configure("Turn.TLabel", font="SunValleyBodyStrongFont", foreground=ACCENT)
    style.configure("TLabelframe.Label", font="SunValleyBodyStrongFont", foreground=ACCENT_DARK)
    style.configure("Pct.TLabel", font=("Segoe UI Variable Display Semibold", 30), foreground=ACCENT)
    style.configure("Big.TLabel", font=("Segoe UI Variable Display Semibold", 24), foreground=ACCENT)
    style.configure("Tint.TLabel", background=TINT)
    # Sun Valley's row height is in pixels and does not follow the font size (rows were clipped)
    style.configure("Treeview", rowheight=int(tkfont.nametofont("TkDefaultFont").metrics("linespace") * 1.6))
    # Sun Valley sets label colours through a state map, which wins over configure(); map ours too
    for name, colour in (("Title.TLabel", ACCENT_DARK), ("Section.TLabel", ACCENT_DARK), ("Pct.TLabel", ACCENT),
                         ("Big.TLabel", ACCENT), ("Turn.TLabel", ACCENT)):
        style.map(name, foreground=[("!disabled", colour)])
    style.configure("TintHint.TLabel", background=TINT, foreground=ACCENT_DARK)
    scale = root.winfo_fpixels("1i") / 96
    root._card_images = []
    # Card frames like Sun Valley's, drawn in blue: the steps card, and two states the Claude card pulses between
    for name, fill, border, width in (("Blue", TINT, ACCENT_LIGHT, 1), ("TurnA", CARD_BG, ACCENT, 2),
                                      ("TurnB", CARD_BG, ACCENT_PULSE, 2)):
        img = card_image(root, fill, border, width, scale)
        root._card_images.append(img)
        pad = int(6 * scale) + width
        style.element_create(f"{name}.Labelframe.border", "image", img, border=pad, padding=4, sticky="nsew")
        # No Labelframe.label element inside: the widget draws its label itself, and the element would put a
        # second copy of the text inside the card (hidden behind the contents, but its edge peeked out).
        style.layout(f"{name}.TLabelframe", [(f"{name}.Labelframe.border", {"sticky": "nsew", "children": [
            ("Labelframe.padding", {"expand": "1"})]})])
    style.configure("Blue.TLabelframe.Label", foreground=ACCENT_DARK, font="SunValleyBodyStrongFont")
    for name in ("TurnA", "TurnB"):
        style.configure(f"{name}.TLabelframe.Label", foreground=ACCENT, font="SunValleyBodyStrongFont")


def card_image(root, fill: str, border: str, width: int, scale: float) -> tk.PhotoImage:
    """A rounded card frame (anti-aliased), drawn on the window background, for a ttk image element."""
    k = max(1.0, scale)
    radius, bw = 6 * k, width * k
    size = int(radius * 2 + bw * 2 + 12)

    def rgb(c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))

    def mix(a, b, t):
        return tuple(round(x + (y - x) * t) for x, y in zip(a, b))

    def coverage(x, y, inset, r):
        cx = min(max(x, inset + r), size - inset - r)
        cy = min(max(y, inset + r), size - inset - r)
        d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 - r
        return max(0.0, min(1.0, 0.5 - d))

    bg, bc, fc = rgb(CARD_BG), rgb(border), rgb(fill)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            outer = coverage(px, py, 0.0, radius)
            inner = coverage(px, py, bw, max(0.5, radius - bw))
            c = mix(mix(bg, bc, outer), fc, inner)
            row.append("#%02x%02x%02x" % c)
        rows.append("{" + " ".join(row) + "}")
    img = tk.PhotoImage(master=root, width=size, height=size)
    img.put(" ".join(rows))
    return img


# Blues (Windows 11 accent family): dark for headings, accent, a lighter pulse, light borders, tinted fills
ACCENT_DARK = "#0b4a7d"
ACCENT = "#0f6cbd"
ACCENT_PULSE = "#62abf5"
ACCENT_LIGHT = "#c7e0f4"
TINT = "#eff6fc"
CARD_BG = "#fafafa"
GREEN = "#0f7b0f"
AMBER = "#9d5d00"
RED = "#c42b1c"
GREY = "#6e6e6e"   # dark enough for text on the light background (4.5:1)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def frame_hwnd(win) -> int:
    """The Windows handle of a Tk toplevel's outer frame (winfo_id is the client area inside it)."""
    u32 = ctypes.windll.user32
    u32.GetParent.restype = ctypes.c_void_p
    u32.GetParent.argtypes = [ctypes.c_void_p]
    return u32.GetParent(win.winfo_id()) or 0


def work_area(hwnd: int | None = None) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the work area (the screen without the taskbar) of the monitor that holds
    `hwnd`, or of the monitor under the mouse when no window is given. None when Windows cannot tell."""
    try:
        u32 = ctypes.windll.user32
        if hwnd:
            u32.MonitorFromWindow.restype = ctypes.c_void_p
            u32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            mon = u32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        else:
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            u32.GetCursorPos(ctypes.byref(pt))
            u32.MonitorFromPoint.restype = ctypes.c_void_p
            u32.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
            mon = u32.MonitorFromPoint(pt, 2)
        if not mon:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not u32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return None
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def place_over(win, parent):
    """Show a (withdrawn) dialog centred over the app window, a little above the middle, inside the work area
    of the app's monitor. Without this Windows puts new windows in its own default spot (top-left)."""
    try:
        if parent.state() == "iconic":
            parent.deiconify()
        parent.update_idletasks()
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        area = work_area(frame_hwnd(parent))
        if area is None:
            area = (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        left, top, right, bottom = area
        title = int(40 * win.winfo_fpixels("1i") / 96)   # the dialog's title bar and frame
        x = max(left, min(x, right - w))
        y = max(top, min(y, bottom - h - title))
        win.geometry(f"+{x}+{y}")
    except Exception:
        log.exception("could not place dialog")
    win.deiconify()
    win.lift()
    win.focus_set()


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


def _box_over(fn, *args, parent=None, **kw):
    """Run a tkinter message box over `parent` instead of in the middle of the screen. parent= alone makes the
    box belong to the app, but Windows still centres MessageBox on the monitor. A one-shot CBT hook on this
    thread moves the box when it is activated, centred over the parent and inside the work area.

    The hook runs inside Tk's call with the GIL handed back to Python by ctypes, so it must not call Tk: a
    nested Tk call there clears _tkinter's saved thread state, and the next Tk event in the box's modal loop
    (the app's poll timer) then aborts Python with "PyEval_RestoreThread ... thread state is NULL"."""
    if parent is None:
        return fn(*args, **kw)
    u32 = ctypes.windll.user32
    hook = None
    try:
        phwnd = frame_hwnd(parent) or parent.winfo_id()   # Tk calls happen here, before the box exists
    except Exception:
        return fn(*args, parent=parent, **kw)

    def proc(code, wparam, lparam):
        try:
            if code == 5 and wparam:   # HCBT_ACTIVATE: wparam is the window being activated
                cls = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(ctypes.c_void_p(wparam), cls, 64)
                if cls.value == "#32770":
                    _move_box(wparam, phwnd)
                    u32.UnhookWindowsHookEx(ctypes.c_void_p(hook))
        except Exception:
            log.exception("could not place message box")
        return u32.CallNextHookEx(None, code, ctypes.c_size_t(wparam), ctypes.c_ssize_t(lparam))

    cb = _HOOKPROC(proc)
    try:
        u32.SetWindowsHookExW.restype = ctypes.c_void_p
        u32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, ctypes.c_void_p, ctypes.c_ulong]
        u32.CallNextHookEx.restype = ctypes.c_ssize_t
        u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        hook = u32.SetWindowsHookExW(5, cb, None, ctypes.windll.kernel32.GetCurrentThreadId())  # WH_CBT
    except Exception:
        log.exception("could not hook message box")
    try:
        return fn(*args, parent=parent, **kw)
    finally:
        if hook:
            u32.UnhookWindowsHookEx(ctypes.c_void_p(hook))   # harmless if the hook already removed itself


def _move_box(hwnd: int, phwnd: int):
    """Centre the box `hwnd` over the window `phwnd`. Win32 calls only (it runs inside the CBT hook)."""
    u32 = ctypes.windll.user32
    u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    box, own = _RECT(), _RECT()
    if not u32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(box)):
        return
    if not u32.GetWindowRect(ctypes.c_void_p(phwnd), ctypes.byref(own)):
        return
    w, h = box.right - box.left, box.bottom - box.top
    x = own.left + (own.right - own.left - w) // 2
    y = own.top + max(0, (own.bottom - own.top - h) // 3)
    area = work_area(phwnd)
    if area:
        left, top, right, bottom = area
        x = max(left, min(x, right - w))
        y = max(top, min(y, bottom - h))
    u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_uint]
    u32.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE|NOZORDER|NOACTIVATE


class _Boxes:
    """messagebox.showinfo/showwarning/showerror/askyesno, placed over their parent window (see _box_over)."""

    def __getattr__(self, name):
        fn = getattr(messagebox, name)
        return lambda *a, **kw: _box_over(fn, *a, **kw)


boxes = _Boxes()


def initial_geometry(sw: int, sh: int, scale: float, area: tuple[int, int, int, int] | None, frame: int) -> dict:
    """The main window's first size and place: about 60 % x 82 % of the screen within limits, but never larger
    than the work area (the screen without the taskbar) minus the title bar `frame`, so the bottom buttons are
    never hidden under the taskbar. `w`/`h` are the inside of the window; `x`/`y` its outer top-left corner."""
    left, top, right, bottom = area if area else (0, 0, sw, sh)
    wa_w, wa_h = right - left, bottom - top
    w = min(int(min(max(sw * 0.6, 1280 * scale), 1760 * scale)), int(sw * 0.92))
    h = min(int(min(max(sh * 0.82, 840 * scale), 1080 * scale)), int(sh * 0.88))
    w = min(w, wa_w - int(16 * scale))
    h = min(h, wa_h - frame - int(8 * scale))
    return {"w": w, "h": h, "x": left + max(0, (wa_w - w) // 2), "y": top + max(0, (wa_h - frame - h) // 3),
            "min_w": min(w, int(1000 * scale)), "min_h": min(h, int(600 * scale)), "zoom": wa_h / scale < 760}


class App:
    """Three views on the left: setup (choose video and languages), work (steps and progress) and done
    (result and what to do next). The Claude panel on the right lights up when it is the user's turn."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings()
        self.events: queue.Queue = queue.Queue()
        self.runner: Runner | None = None
        self.created: list[Path] = []
        self.learned_words: list[tuple[str, str]] = []
        self.job: dict | None = None
        self._running = False
        self._starting = False   # The user's own start is waiting in a dialog (a question, the model download)

        root.title(APP_NAME)
        apply_style(root)
        # Sizes in logical pixels, scaled to the display (the process is DPI aware), within the screen.
        scale = self.scale = root.winfo_fpixels("1i") / 96
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            frame = gsm(4) + gsm(33) + gsm(92)   # caption + sizing frame + padded border
        except Exception:
            frame = 0
        g = initial_geometry(sw, sh, scale, work_area(), frame or int(32 * scale))
        root.geometry(f"{g['w']}x{g['h']}+{g['x']}+{g['y']}")
        root.minsize(g["min_w"], g["min_h"])
        if g["zoom"]:
            root.state("zoomed")   # a low screen (e.g. 1200 px at 150 %): use all of it
        self.bg = ttk.Style().lookup("TFrame", "background") or "#fafafa"

        self.video = tk.StringVar()
        self.lang = tk.StringVar(value="Dansk")
        self.speech = tk.StringVar()       # "Original (dansk)" or a language name
        self.subs = tk.StringVar()         # "Ingen" or "Danske", "Svenske", ...
        self.redo = tk.BooleanVar(value=False)
        self.cut = tk.BooleanVar(value=bool(self.settings.cut_first))
        self.recut = tk.BooleanVar(value=False)
        self.cut_lines: list[str] = []       # Claude's cut suggestions from the last Claude round
        self._review = None
        self._review_done = threading.Event()
        self._review_result: list | None = None
        self._tdialog = None
        self._translation_done = threading.Event()
        self._translation_ok = False
        self._pulsing = False
        self._spin = 0
        self.step_rows: list[dict] = []
        self.active_step = -1
        self.claude_at = self.review_at = -1
        self.manual_steps: list[int] = []
        self.mcp_job: str | None = None      # the job Claude started through the MCP server, if any
        self._inbox_tick = 0
        self._status_written = 0.0
        try:
            jobs.cleanup()
        except Exception:
            log.exception("job cleanup failed")

        self._build()
        self._refresh()
        self.video.trace_add("write", lambda *_: self._refresh())
        self._show("setup")
        root.after(100, self._poll)
        root.after(150, self._animate)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._self_check, daemon=True).start()
        self._skill_guide = None
        self._skill_warning: tuple | None = None
        if "--from-claude" not in sys.argv and self.settings.skill_installed < claude.SKILL_VERSION:
            # This version ships a newer skill than the one the user has put into Claude
            # 0: unknown, most likely never put in (the "first" wording), else this version ships a newer one
            first = self.settings.skill_installed == 0
            root.after(900, lambda: self.show_skill_guide("first" if first else "update"))

    # --- Layout -------------------------------------------------------------------------

    def _wrap(self, label: ttk.Label, parent, pad: int = 20):
        parent.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - pad)), add="+")

    def _build(self):
        f = self.main = ttk.Frame(self.root, padding=(20, 16))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=3, uniform="cols")
        self._set_claude_width(False)
        f.rowconfigure(1, weight=1)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(top, text=APP_NAME, style="Title.TLabel", foreground=ACCENT_DARK).pack(side="left")
        ttk.Label(top, text=APP_TAGLINE, style="Hint.TLabel").pack(side="left", padx=(14, 0), pady=(10, 0))
        ttk.Button(top, text="Indstillinger", command=self._settings_dialog).pack(side="right")

        self.left = ttk.Frame(f)
        self.left.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        self.left.columnconfigure(0, weight=1)
        self.left.rowconfigure(0, weight=1)
        self.views = {"setup": self._build_setup(self.left), "work": self._build_work(self.left),
                      "done": self._build_done(self.left)}

        # Claude, on the right, in a frame whose border can pulse when it is the user's turn
        self.claude_wrap = ttk.Frame(f)
        self.claude_wrap.grid(row=1, column=1, sticky="nsew")
        self.claude_wrap.columnconfigure(0, weight=1)
        self.claude_wrap.rowconfigure(0, weight=1)
        self.claude_box = ttk.LabelFrame(self.claude_wrap, text=" Claude ", padding=12)
        self.claude_box.grid(row=0, column=0, sticky="nsew")
        self.claude_box.columnconfigure(0, weight=1)
        self.claude_box.rowconfigure(0, weight=1)
        self.claude_idle = ttk.Frame(self.claude_box)
        self.claude_idle.grid(row=0, column=0, sticky="nsew")
        idle = ttk.Label(self.claude_idle, justify="left", style="Hint.TLabel",
                         text="Claude hjælper, når det bliver din tur.")
        idle.pack(anchor="nw", fill="x")
        self._wrap(idle, self.claude_idle, 10)

    def _build_setup(self, parent) -> ttk.Frame:
        v = ttk.Frame(parent)
        v.columnconfigure(0, weight=1)

        g1 = ttk.LabelFrame(v, text=" 1. Video ", padding=12)
        g1.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        g1.columnconfigure(1, weight=1)
        # The chosen file as a card (name, folder, change button) instead of an editable path
        card = ttk.Frame(g1)
        card.grid(row=0, column=0, columnspan=2, sticky="ew")
        card.columnconfigure(0, weight=1)
        self.v_name = ttk.Label(card, text="", font="SunValleyBodyLargeFont")
        self.v_name.grid(row=0, column=0, sticky="ew")
        self.v_dir = ttk.Label(card, text="", style="Hint.TLabel")
        self.v_dir.grid(row=1, column=0, sticky="ew")
        self._wrap(self.v_name, card, 200)
        self._wrap(self.v_dir, card, 200)
        self.v_btn = ttk.Button(card, text="Vælg video...", width=14, command=self._pick_video)
        self.v_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        ttk.Label(g1, text="Hvilket sprog tales på videoen?").grid(row=1, column=0, sticky="w", pady=(14, 0))
        c_lang = ttk.Combobox(g1, textvariable=self.lang, values=[l.name for l in LANGUAGES], state="readonly", width=14)
        c_lang.grid(row=1, column=1, sticky="w", padx=10, pady=(14, 0))
        c_lang.bind("<<ComboboxSelected>>", lambda e: self._refresh(lang_changed=True))

        g2 = ttk.LabelFrame(v, text=" 2. Hvad skal den nye video have? ", padding=12)
        g2.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        g2.columnconfigure(1, weight=1)
        self.cb_cut = ttk.Checkbutton(g2, variable=self.cut, command=self._cut_changed,
                                      text="Klip videoen først: fjern ventetid, pauser og det, der ikke skal med")
        self.cb_cut.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(g2, text="Tale:").grid(row=1, column=0, sticky="w")
        self.c_speech = ttk.Combobox(g2, textvariable=self.speech, state="readonly", width=22)
        self.c_speech.grid(row=1, column=1, sticky="w", padx=8, pady=3)
        self.c_speech.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        ttk.Label(g2, text="Undertekster:").grid(row=2, column=0, sticky="w")
        self.c_subs = ttk.Combobox(g2, textvariable=self.subs, state="readonly", width=22,
                                   values=["Ingen"] + [l.plural.capitalize() for l in LANGUAGES])
        self.c_subs.grid(row=2, column=1, sticky="w", padx=8, pady=3)
        self.c_subs.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        # Short checkbox labels; what happens otherwise is on a wrapped hint line under each (lined up with the text)
        indent = int(20 * self.scale)
        self.cb_redo = ttk.Checkbutton(g2, variable=self.redo, command=self._refresh, text="Lyt videoen igennem forfra")
        self.cb_redo.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.redo_hint = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.redo_hint.grid(row=4, column=0, columnspan=2, sticky="ew", padx=(indent, 0))
        self._wrap(self.redo_hint, g2, 40 + indent)
        self.cb_recut = ttk.Checkbutton(g2, variable=self.recut, command=self._refresh, text="Klip videoen forfra")
        self.cb_recut.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.recut_hint = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.recut_hint.grid(row=6, column=0, columnspan=2, sticky="ew", padx=(indent, 0))
        self._wrap(self.recut_hint, g2, 40 + indent)
        self._recut_forced = False   # ticked by the program because the video is listened to again
        self.plan_result = ttk.Label(g2, text="", font="SunValleyBodyStrongFont", justify="left")
        self.plan_result.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._wrap(self.plan_result, g2, 40)
        self.plan_steps = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.plan_steps.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._wrap(self.plan_steps, g2, 40)

        g3 = ttk.Frame(v, padding=(0, 6))
        g3.grid(row=2, column=0, sticky="ew")
        g3.columnconfigure(0, weight=1)
        self.start_btn = ttk.Button(g3, text="Start", style="Accent.TButton", width=16, command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="w", ipady=4)
        hint = ttk.Label(g3, text="Programmet laver resten, og du kan arbejde videre imens. "
                                  "Det siger til, når det er din tur.", style="Hint.TLabel", justify="left")
        hint.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._wrap(hint, g3, 10)
        return v

    def _build_work(self, parent) -> ttk.Frame:
        v = ttk.Frame(parent)
        v.columnconfigure(0, weight=1)
        ttk.Label(v, text="Arbejder på", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        self.w_video = ttk.Label(v, text="", style="Section.TLabel", foreground=ACCENT_DARK)
        self.w_video.grid(row=1, column=0, sticky="ew")
        self._wrap(self.w_video, v)
        self.w_result = ttk.Label(v, text="", style="Hint.TLabel")
        self.w_result.grid(row=2, column=0, sticky="ew", pady=(2, 12))
        self._wrap(self.w_result, v)

        box = ttk.LabelFrame(v, text=" Trin ", padding=14, style="Blue.TLabelframe")
        box.grid(row=3, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.steps_box = box

        prog = ttk.Frame(v, padding=(0, 16, 0, 0))
        prog.grid(row=4, column=0, sticky="ew")
        prog.columnconfigure(1, weight=1)
        self.w_pct = ttk.Label(prog, text="0 %", style="Pct.TLabel", foreground=ACCENT, width=5)
        self.w_pct.grid(row=0, column=0, rowspan=2, sticky="w")
        self.bar = ttk.Progressbar(prog, maximum=1000)
        self.bar.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.status = ttk.Label(prog, text="")
        self.status.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))

        # One quiet line on a tinted strip: the big "Din tur" is in the Claude card, where the steps are
        k = self.scale
        self.callout = tk.Frame(v, background=TINT, padx=int(12 * k), pady=int(8 * k))
        self.callout.grid(row=5, column=0, sticky="ew", pady=(20, 0))
        self.callout.columnconfigure(0, weight=1)
        hint = tk.Label(self.callout, text="Din tur: følg trinene i Claude-kortet til højre", background=TINT,
                        foreground=ACCENT_DARK, font="SunValleyBodyStrongFont", justify="left", anchor="w",
                        borderwidth=0, padx=0, pady=0)
        hint.grid(row=0, column=0, sticky="w")
        self.callout.bind("<Configure>", lambda e: hint.configure(wraplength=max(150, e.width - int(90 * k))))
        self.arrow = tk.Canvas(self.callout, width=int(44 * k), height=int(20 * k), highlightthickness=0,
                               background=TINT)
        self.arrow.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.callout.grid_remove()

        self.detail = ttk.Label(v, text="", style="Hint.TLabel", justify="left")
        self.detail.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        self._wrap(self.detail, v)

        btns = ttk.Frame(v)
        btns.grid(row=7, column=0, sticky="w", pady=(18, 0))
        self.pause_btn = ttk.Button(btns, text="Pause", width=10, command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        self.cancel_btn = ttk.Button(btns, text="Annullér", width=10, command=self._cancel)
        self.cancel_btn.pack(side="left", padx=10)
        self.pause_hint = ttk.Label(btns, text="Pause frigiver pc'en med det samme. Arbejdet fortsætter, hvor det slap.",
                                    style="Hint.TLabel")
        self.pause_hint.pack(side="left", padx=6)
        return v

    def _build_done(self, parent) -> ttk.Frame:
        v = ttk.Frame(parent)
        v.columnconfigure(1, weight=1)
        self.d_icon = tk.Label(v, text="✓", font=("Segoe UI Variable Display", 44, "bold"), foreground=GREEN,
                               background=self.bg)
        self.d_icon.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 16))
        self.d_title = ttk.Label(v, text="", style="Title.TLabel", foreground=ACCENT_DARK)
        self.d_title.grid(row=0, column=1, sticky="sw")
        self.d_msg = ttk.Label(v, text="", justify="left")
        self.d_msg.grid(row=1, column=1, sticky="new", pady=(4, 0))
        self._wrap(self.d_msg, v, 120)

        self.d_main = ttk.LabelFrame(v, text=" Din nye video ", padding=12)
        self.d_main.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        self.d_main.columnconfigure(0, weight=1)
        self.d_file = ttk.Label(self.d_main, text="", font="SunValleyBodyStrongFont")
        self.d_file.grid(row=0, column=0, columnspan=3, sticky="ew")
        self._wrap(self.d_file, self.d_main)
        acts = ttk.Frame(self.d_main)
        acts.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.d_play = ttk.Button(acts, text="▶  Afspil", style="Accent.TButton", width=14,
                                 command=lambda: self._open(self.d_target))
        self.d_play.pack(side="left")
        ttk.Button(acts, text="Vis i mappe", width=14,
                   command=lambda: self._show_in_folder(self.d_target)).pack(side="left", padx=10)

        g = ttk.LabelFrame(v, text=" Alle filer fra denne kørsel ", padding=12)
        g.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        g.columnconfigure(0, weight=1)
        self.files = tk.Listbox(g, height=4, activestyle="none", borderwidth=0, highlightthickness=0,
                                font="SunValleyBodyFont", background=self.bg)
        self.files.grid(row=0, column=0, sticky="ew")
        self.files.bind("<Double-Button-1>", lambda e: self._open_selected())
        ttk.Label(g, text="Dobbeltklik for at åbne. Tekstfilerne (.srt) bruges igen, hvis du laver flere udgaver.",
                  style="Hint.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))

        nxt = ttk.LabelFrame(v, text=" Hvad nu? ", padding=12)
        nxt.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.d_again = ttk.Button(nxt, text="Prøv igen", style="Accent.TButton", command=self._start)
        self.d_same = ttk.Button(nxt, text="Lav en anden udgave af samme video", command=self._same_video)
        self.d_new = ttk.Button(nxt, text="Vælg en ny video", command=self._new_video)
        self.d_log = ttk.Button(nxt, text="Åbn logfil", command=lambda: self._open(log_file()))
        self.d_next_hint = ttk.Label(nxt, text="", style="Hint.TLabel", justify="left")
        self._wrap(self.d_next_hint, nxt)
        return v

    def _set_claude_width(self, active: bool):
        """The Claude card takes 2/5 of the width during the user's turn, and 1/4 while it only waits."""
        self.main.columnconfigure(1, weight=2 if active else 1, uniform="cols")

    def _show(self, name: str):
        # The done view has nothing for the Claude card: the result gets the whole width (as in the cut review)
        if name == "done":
            self.claude_wrap.grid_remove()
            self.left.grid_configure(columnspan=2, padx=0)
        elif self._review is None:
            self.left.grid_configure(columnspan=1, padx=(0, 16))
            self.claude_wrap.grid()
        for key, frame in self.views.items():
            if key == name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        self.view = name

    # --- Setup state --------------------------------------------------------------------

    def _video(self) -> Path | None:
        v = self.video.get().strip().strip('"')
        return Path(v) if v else None

    def _lang(self) -> Language:
        return LANG_BY_NAME[self.lang.get()]

    def _speech_lang(self) -> Language | None:
        """None = keep the original speech."""
        return LANG_BY_NAME.get(self.speech.get())

    def _subs_lang(self) -> Language | None:
        return next((l for l in LANGUAGES if l.plural.capitalize() == self.subs.get()), None)

    def _refresh(self, lang_changed: bool = False):
        l = self._lang()
        original = f"Original ({l.singular})"
        self.c_speech.configure(values=[original] + [x.name for x in LANGUAGES if x != l])
        if lang_changed or not self.speech.get() or self.speech.get() == l.name or self.speech.get().startswith("Original"):
            self.speech.set(original)
        if not self.subs.get() or (lang_changed and self.subs.get() != "Ingen"):
            self.subs.set(l.plural.capitalize())
        v = self._video()
        mode, _ = self._cut_mode(v)
        show_recut = bool(v and v.is_file() and self.cut.get() and textstate.cut_status(cut_name(v), v) == "ok")
        if mode == "reuse":
            v = cut_name(v)
        has_srt = bool(v and v.is_file() and textstate.status(srt_name(v, l), v) == "ok")
        self.redo_hint.configure(text=f"Ellers bruges de {l.plural} undertekster, der allerede er lavet.")
        for w in (self.cb_redo, self.redo_hint):
            w.grid() if has_srt else w.grid_remove()
        # Listening again always cuts again (see _cut_mode): show that instead of an option that does nothing
        if show_recut and self.redo.get():
            if not self.recut.get():
                self.recut.set(True)
                self._recut_forced = True
            self.cb_recut.state(["disabled"])
            self.recut_hint.configure(text="Sker altid, når videoen lyttes igennem forfra.")
        else:
            if self._recut_forced:
                self.recut.set(False)
                self._recut_forced = False
            self.cb_recut.state(["!disabled"])
            self.recut_hint.configure(text="Ellers bruges den klippede video fra sidst.")
        for w in (self.cb_recut, self.recut_hint):
            w.grid() if show_recut else w.grid_remove()
        plan = self._plan_text()
        first, _, rest = plan.partition("\n")
        if first.startswith("Du får"):
            self.plan_result.configure(text=first.replace("Du får:  ", "Du får: "))
            self.plan_result.grid()
            self.plan_steps.configure(text=rest)
        else:
            self.plan_result.grid_remove()
            self.plan_steps.configure(text=plan)
        v = self._video()
        self.start_btn.configure(state="normal" if v and v.is_file() else "disabled")
        if not v:
            self.v_name.configure(text="Ingen video valgt", foreground=GREY)
            self.v_dir.configure(text="Vælg den video, der skal have undertekster eller dubbes.", foreground="")
            self.v_btn.configure(text="Vælg video...", style="Accent.TButton")
        else:
            self.v_name.configure(text=v.name, foreground="")
            self.v_dir.configure(text=str(v.parent) if v.is_file() else "Filen findes ikke længere. Vælg den igen.",
                                 foreground="" if v.is_file() else RED)
            self.v_btn.configure(text="Skift video...", style="TButton")

    def _claude_plan(self, v: Path, lang: Language, transcribe: bool, targets) -> tuple[bool, list[Language]]:
        """(check the spoken-language text?, languages to translate to) for the Claude round.

        Claude checks the text once (after that it is reused, also if the user edited it by hand), and a
        translation is reused when it matches the spoken-language subtitles' time codes."""
        orig = srt_name(v, lang)
        check = transcribe or not textstate.is_checked(orig)
        orig_cues = None
        if not transcribe and orig.exists():
            try:
                orig_cues = read_srt(orig)
            except Exception:
                orig_cues = None
        needed = []
        for target in dict.fromkeys(x for x in targets if x and x != lang):
            existing = srt_name(v, target)
            ok = False
            if orig_cues is not None and existing.exists():
                try:
                    ok = engine.compare(orig_cues, read_srt(existing)) is None
                except Exception:
                    ok = False
            if not ok:
                needed.append(target)
        return check, needed

    def _result_name(self, v: Path, l: Language, sp: Language | None, su: Language | None) -> Path:
        if sp:
            return dub_name(v, sp, su)
        if su:
            return burned_name(v, su)
        return srt_name(v, l)

    def _cut_mode(self, v: Path | None) -> tuple[str | None, Path | None]:
        """(None | "new" | "reuse", the cut video's path). "reuse": the cut video made earlier from this video
        is used again (for another language), unless the user asks to cut again or to listen again."""
        if not v or not v.is_file() or not self.cut.get():
            return None, None
        edited = cut_name(v)
        if not self.recut.get() and not self.redo.get() and textstate.cut_status(edited, v) == "ok":
            return "reuse", edited
        return "new", edited

    def _cut_changed(self):
        self.settings.cut_first = bool(self.cut.get())
        self.settings.save()
        self._refresh()

    def _plan_text(self) -> str:
        v = self._video()
        if not v:
            return "Vælg den video, der skal have undertekster eller dubbes."
        if not v.is_file():
            return "Videoen findes ikke. Vælg den igen."
        l, sp, su = self._lang(), self._speech_lang(), self._subs_lang()
        mode, edited = self._cut_mode(v)
        if mode == "reuse":
            if not sp and not su:
                return (f"Den klippede video findes allerede: {edited.name}\n"
                        "Vælg tale eller undertekster, eller sæt flueben i \"Klip videoen forfra\".")
            v = edited
        base = edited if mode else v
        if mode == "new" and not sp and not su:
            lines = [f"Du får:  {edited.name}  (og de {l.plural} undertekster til den)"]
        else:
            lines = [f"Du får:  {self._result_name(base, l, sp, su).name}" + ("" if sp or su else "  (kun tekstfilen)")]
        if mode == "reuse":
            lines.append(f"Den klippede video ({edited.name}) genbruges.")
        state = textstate.status(srt_name(v, l), v)
        transcribe = self.redo.get() or state != "ok"
        if state == "changed" and not self.redo.get():
            lines.append(f"Videoen er ændret, siden teksten blev lavet, så de {l.plural} undertekster laves forfra.")
        elif state == "old" and not self.redo.get():
            lines.append(f"De {l.plural} undertekster er lavet af en ældre version af programmet, så de laves forfra.")
        elif transcribe:
            lines.append(f"Programmet lytter videoen igennem og skriver de {l.plural} undertekster.")
        check, need = self._claude_plan(v, l, transcribe, [sp, su])
        if mode == "new" and not check and not need:
            check = True
        todo = ([f"retter de {l.plural} undertekster"] if check else []) + \
               ([f"oversætter til {' og '.join(x.name.lower() for x in need)}"] if need else []) + \
               (["foreslår, hvad der skal klippes væk"] if mode == "new" else [])
        if todo:
            lines.append(f"Claude {', '.join(todo[:-1]) + ' og ' if len(todo) > 1 else ''}{todo[-1]} "
                         "(i Claude-kortet til højre, når det er din tur).")
        if mode == "new":
            lines.append("Programmet finder selv lange pauser. Du ser alle klip på en tidslinje og retter dem, "
                         "før videoen bliver klippet.")
        return "\n".join(lines)

    def _pick_video(self):
        p = filedialog.askopenfilename(parent=self.root, title="Vælg video", filetypes=VIDEO_TYPES,
                                       initialdir=self.settings.last_dir)
        if p:
            self.redo.set(False)
            self.recut.set(False)
            self.video.set(str(Path(p)))
            self.settings.last_dir = str(Path(p).parent)
            self.settings.save()

    def _same_video(self):
        self.redo.set(False)
        self.recut.set(False)
        if self.job and self.job.get("original"):
            self.video.set(str(self.job["original"]))
        self._refresh()
        self._show("setup")

    def _new_video(self):
        self._show("setup")
        self._pick_video()

    # --- Start / cancel -----------------------------------------------------------------

    def _validate(self, auto: bool = False):
        """Returns a job dict or None (after telling the user what is wrong). With `auto` (a job from Claude) it does
        not ask questions it can answer itself; the reason for a None is in self._auto_reason.

        The user never handles .srt files: subtitles in the spoken language are reused when they exist next to
        the video (or made when they do not), and translations are reused when they match those subtitles'
        time codes, otherwise asked for in the Claude panel during the run."""
        v = self._video()
        self._auto_reason = ""
        if not v or not v.is_file():
            self._auto_reason = "Videoen findes ikke."
            if not auto:
                boxes.showwarning(APP_NAME, "Vælg først en video.", parent=self._owner())
            return None
        lang = self._lang()
        speech, subs = self._speech_lang(), self._subs_lang()
        mode, edited = self._cut_mode(v)
        original = v
        if mode == "reuse":
            if not speech and not subs:
                self._auto_reason = f"Den klippede video findes allerede ({edited.name}). Vælg tale eller undertekster."
                if not auto:
                    boxes.showinfo(APP_NAME, f"Den klippede video findes allerede:\n{edited.name}\n\n"
                                        "Vælg tale eller undertekster, eller sæt flueben i \"Klip videoen forfra\".",
                                        parent=self._owner())
                return None
            v = edited
        orig_srt = srt_name(v, lang)
        job = {"video": v, "original": original, "lang": lang, "speech": speech, "subs": subs, "outputs": [],
               "translate": [], "cut": mode == "new", "edited": edited, "cut_reused": mode == "reuse"}
        job["transcribe"] = self.redo.get() or textstate.status(orig_srt, v) != "ok"
        if not speech and not subs and not job["transcribe"] and not job["cut"]:
            self._auto_reason = f"De {lang.plural} undertekster findes allerede ({orig_srt.name}). Vælg tale eller undertekster."
            if auto:
                return None
            boxes.showinfo(APP_NAME, f"De {lang.plural} undertekster findes allerede:\n{orig_srt.name}\n\n"
                                "Vælg et andet sprog til tale eller undertekster, eller sæt flueben i \"Lyt videoen igennem forfra\".",
                                parent=self._owner())
            return None
        if job["transcribe"]:
            job["outputs"].append(orig_srt)
        job["check"], job["translate"] = self._claude_plan(v, lang, job["transcribe"], [speech, subs])
        if job["cut"] and not job["check"] and not job["translate"]:
            job["check"] = True   # the Claude round is needed for the cut suggestions anyway
        if job["cut"]:
            job["outputs"].append(edited)
            job["result"] = self._result_name(edited, lang, speech, subs) if speech or subs else edited
        else:
            job["result"] = self._result_name(v, lang, speech, subs)
        if speech or subs:
            job["outputs"].append(job["result"])
        exists = [o for o in job["outputs"] if o.exists()]
        if exists and not auto and not boxes.askyesno(APP_NAME, "Disse filer findes allerede og bliver lavet forfra:\n\n"
                                              + "\n".join(o.name for o in exists) + "\n\nFortsæt?",
                                              parent=self._owner()):
            return None
        # Models
        need = []
        if job["transcribe"]:
            need.append(engine.whisper_model_file(self.settings.whisper_model))
        mode = self.settings.voice_mode if self.settings.voice_mode in ("person", "gender", "one") else "person"
        gender = engine.GENDER_VOICES.get(speech.code) if speech and mode == "gender" else None
        persons = engine.PERSON_VOICES.get(speech.code) if speech and mode == "person" else None
        if speech:
            names = {speech.voice} | set((gender or {}).values()) | {v for vs in (persons or {}).values() for v in vs}
            for v in sorted(names):
                need += engine.voice_files(v)
            if persons:
                need.append(engine.speaker_model_file())
        missing = [m for m in need if not engine.find_model(m.filename)]
        if missing:
            if auto:
                self._call_attention()
            if not ModelDialog(self.root, missing, scale=self.scale).result:
                self._auto_reason = "Programmet mangler filer (modeller), og de blev ikke hentet. Bed brugeren åbne appen."
                return None
        if job["transcribe"]:
            job["model"] = engine.find_model(engine.whisper_model_file(self.settings.whisper_model).filename)
            mem = free_memory_gb()
            if mem is not None and mem < 1.5 and auto:
                log.warning("only %.1f GB free memory; starting anyway (job from Claude)", mem)
            elif mem is not None and mem < 1.5:
                if not boxes.askyesno(APP_NAME, f"Der er kun {mem:.1f} GB ledig hukommelse. Transskriptionen bruger "
                                           "ca. 1,5 GB og bliver meget langsom, hvis der er for lidt.\n\n"
                                           "Luk gerne andre programmer (fx Teams og browserfaner) først.\n\nFortsæt alligevel?",
                                           parent=self._owner()):
                    return None
        if speech:
            job["voice"] = engine.find_model(speech.voice + ".onnx")
            job["voices"] = {k: engine.find_model(v + ".onnx") for k, v in gender.items()} if gender else None
            job["palette"] = ({g: [engine.find_model(v + ".onnx") for v in vs] for g, vs in persons.items()}
                              if persons else None)
            job["speaker_model"] = engine.find_model(engine.speaker_model_file().filename) if persons else None
        return job

    def _step_names(self, job: dict) -> tuple[list[str], int]:
        """Names shown in the step list, and where the Claude step sits (-1 if none). Steps without progress of
        their own (Claude, the cut review) are in self.manual_steps."""
        names = []
        if job["transcribe"] or job["cut"]:
            names.append("Trækker lyden ud")
        if job["transcribe"]:
            names.append("Lytter og skriver tekst")
        claude_at = -1
        self.review_at = -1
        if job["check"] or job["translate"]:
            claude_at = len(names)
            what = (["retter teksten"] if job["check"] else []) + \
                   ([f"oversætter til {' og '.join(t.name.lower() for t in job['translate'])}"] if job["translate"] else []) + \
                   (["foreslår klip"] if job["cut"] else [])
            what = (", ".join(what[:-1]) + " og " if len(what) > 1 else "") + what[-1]
            names.append(f"Claude {what}" + (" - i chatten" if job.get("mcp") else " - din tur"))
        if job["cut"]:
            self.review_at = len(names)
            names += ["Du gennemser klippene - din tur", "Klipper videoen"]
        self.manual_steps = [i for i in (claude_at, self.review_at) if i >= 0]
        if job["speech"]:
            names += [f"Indtaler på {job['speech'].name.lower()}", "Laver den dubbede video"]
        elif job["subs"]:
            names += ["Brænder undertekster ind"]
        return names, claude_at

    def _start(self, auto: bool = False):
        if self._running or self._starting:
            return
        if not auto:
            self.mcp_job = None
        self._starting = True
        try:
            job = self._validate(auto)
        finally:
            self._starting = False
        if not job:
            if auto and self.mcp_job:
                jobs.set_status(self.mcp_job, state="error", message=self._auto_reason or "Appen kunne ikke starte jobbet.")
                self.mcp_job = None
            return
        job["mcp"] = self.mcp_job
        self.job = job
        self.runner = Runner()
        self.created = []
        self.learned_words = []
        self.files.delete(0, "end")
        names, self.claude_at = self._step_names(job)
        for child in self.steps_box.winfo_children():
            child.destroy()
        self.step_rows = []
        size = int(24 * self.scale)
        for i, name in enumerate(names):
            icon = tk.Canvas(self.steps_box, width=size, height=size, highlightthickness=0, background=TINT)
            icon.grid(row=i, column=0, sticky="w", pady=4)
            text = tk.Label(self.steps_box, text=name, foreground=GREY, background=TINT, font="SunValleyBodyFont",
                            anchor="w", padx=0, pady=0, borderwidth=0)
            text.grid(row=i, column=1, sticky="w", padx=(10, 0))
            row = {"icon": icon, "text": text, "state": "todo"}
            self.step_rows.append(row)
            self._draw_icon(row)
        self.active_step = -1
        self.w_video.configure(text=job["video"].name)
        self.w_result.configure(text=f"Du får:  {job['result'].name}")
        self.bar.configure(value=0)
        self.w_pct.configure(text="0 %")
        self.status.configure(text="Starter...")
        self.detail.configure(text="")
        self.callout.grid_remove()
        self.pause_btn.configure(state="normal", text="Pause")
        self.cancel_btn.configure(state="normal")
        self._running = True
        self._user_turn(False)   # the hint may be hidden from a run stopped during the user's turn
        self._show("work")
        self._jstatus(state="running", percent=0, step="Starter", eta="", video=str(job["video"]),
                      result=str(job["result"]), steps=names, message="")
        threading.Thread(target=self._work, args=(job, self.runner), daemon=True).start()

    # --- Jobs from Claude (MCP) -----------------------------------------------------------

    def _jstatus(self, **fields):
        if self.mcp_job:
            try:
                jobs.set_status(self.mcp_job, **fields)
            except Exception:
                log.exception("could not write job status")

    def _check_cancel(self):
        """Claude's 'annuller': the user asked in the chat, so no question here."""
        if not self.mcp_job:
            return
        f = jobs.root() / self.mcp_job / "cancel.json"
        if not f.exists():
            return
        f.unlink(missing_ok=True)
        if self.runner and self._running and not self.runner.cancelled.is_set():
            log.info("job %s cancelled from Claude", self.mcp_job)
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")
            self.status.configure(text="Claude stopper kørslen...")
            self.runner.cancel()
        else:
            self._jstatus(state="cancelled", message="Stoppet fra Claude.")

    def _check_inbox(self):
        """Take a job Claude has sent, if the app is free; otherwise tell Claude it is busy."""
        if self._starting or self._grab_path():
            return   # a dialog is open (maybe the user's own start): the request waits in the inbox for the next tick
        try:
            reqs = jobs.take_inbox()
        except Exception:
            log.exception("inbox")
            return
        for req in reqs:
            if self._running:
                jobs.reject(req, "busy", "Appen er i gang med en anden video. Prøv igen, når den er færdig.")
                continue
            jobs.accept(req)
            log.info("job from Claude: %s", req)
            self.mcp_job = req["id"]
            self.redo.set(bool(req.get("redo")))
            self.cut.set(bool(req.get("cut")))
            self.recut.set(bool(req.get("cut")) and bool(req.get("redo")))
            self.video.set(req["video"])
            self.lang.set(req.get("lang") or "Dansk")
            self._refresh(lang_changed=True)
            speech = req.get("speech")
            self.speech.set(speech if speech in LANG_BY_NAME else f"Original ({self._lang().singular})")
            subs = req.get("subs")
            self.subs.set(LANG_BY_NAME[subs].plural.capitalize() if subs in LANG_BY_NAME else "Ingen")
            self._refresh()
            self._show("setup")
            # after_idle: _poll reschedules itself before a dialog in _validate (models) can block it
            self.root.after_idle(lambda: self._start(auto=True))
            break

    def _set_step(self, index: int):
        """Mark steps before `index` done and `index` active."""
        if index == self.active_step:
            return
        self.active_step = index
        for i, row in enumerate(self.step_rows):
            state = "done" if i < index else "active" if i == index else "todo"
            if state == "active" and i in (self.claude_at, self.review_at):
                state = "turn"
            row["state"] = state
            colour = {"done": GREEN, "active": ACCENT, "turn": ACCENT}.get(state, GREY)
            strong = state in ("active", "turn")
            row["text"].configure(foreground=colour, font="SunValleyBodyStrongFont" if strong else "SunValleyBodyFont")
            self._draw_icon(row)

    def _draw_icon(self, row: dict, angle: float = 0.0):
        """Step icons drawn like Windows 11: thin ring (to do), spinning arc (working), filled check (done),
        filled arrow (the user's turn)."""
        c: tk.Canvas = row["icon"]
        c.delete("all")
        s = int(c.cget("width"))
        k = self.scale
        p = 3 * k
        state = row["state"]
        if state == "todo":
            c.create_oval(p, p, s - p, s - p, outline="#a9c7e3", width=1.5 * k)
        elif state == "done":
            c.create_oval(p, p, s - p, s - p, outline=GREEN, fill=GREEN)
            c.create_line(s * 0.30, s * 0.52, s * 0.45, s * 0.66, s * 0.71, s * 0.37,
                          fill="white", width=2 * k, capstyle="round", joinstyle="round")
        elif state == "turn":
            c.create_oval(p, p, s - p, s - p, outline=ACCENT, fill=ACCENT)
            c.create_line(s * 0.30, s * 0.5, s * 0.70, s * 0.5, fill="white", width=2 * k, capstyle="round")
            c.create_line(s * 0.52, s * 0.32, s * 0.70, s * 0.5, s * 0.52, s * 0.68,
                          fill="white", width=2 * k, capstyle="round", joinstyle="round")
        else:  # working: a light track with a rotating accent arc
            c.create_oval(p, p, s - p, s - p, outline=ACCENT_LIGHT, width=2.5 * k)
            c.create_arc(p, p, s - p, s - p, start=-angle, extent=100, style="arc", outline=ACCENT, width=2.5 * k)

    def _animate(self):
        """Smooth spinner on the active step, and the pulsing border on the Claude panel."""
        self._spin += 1
        if self._running and 0 <= self.active_step < len(self.step_rows):
            row = self.step_rows[self.active_step]
            if row["state"] == "active" and not (self.runner and self.runner.paused):
                self._draw_icon(row, angle=(self._spin * 12) % 360)
        if self._pulsing:
            if self._spin % 11 == 0:
                self.claude_box.configure(style="TurnA.TLabelframe" if (self._spin // 11) % 2 == 0 else "TurnB.TLabelframe")
            self._draw_arrow()
        self.root.after(40, self._animate)

    def _draw_arrow(self):
        import math
        c, k = self.arrow, self.scale
        c.delete("all")
        w, h = int(c.cget("width")), int(c.cget("height"))
        dx = (math.sin(self._spin * 0.25) + 1) * 3 * k
        y = h / 2
        c.create_line(3 * k + dx, y, w - 10 * k + dx, y, fill=ACCENT, width=2.5 * k, capstyle="round")
        c.create_line(w - 17 * k + dx, y - 6 * k, w - 9 * k + dx, y, w - 17 * k + dx, y + 6 * k,
                      fill=ACCENT, width=2.5 * k, capstyle="round", joinstyle="round")

    def _busy(self) -> bool:
        return self._running

    def _on_close(self):
        if self._busy():
            if not boxes.askyesno(APP_NAME, "Programmet arbejder stadig. Vil du stoppe og lukke?\n\n"
                                       "Det, der er nået, går tabt. Vil du bare frigive pc'en, så brug Pause i stedet.",
                                       parent=self._owner()):
                return
            if self.runner:
                self.runner.cancel()
        self.root.destroy()

    def _toggle_pause(self):
        r = self.runner
        if not r:
            return
        if r.paused:
            r.resume()
            self.pause_btn.configure(text="Pause")
            self.status.configure(text=self._status_before_pause)
        else:
            r.pause()
            self.pause_btn.configure(text="Fortsæt", style="Accent.TButton")
            self._status_before_pause = self.status.cget("text")
            self.status.configure(text="Sat på pause. Tryk \"Fortsæt\", når pc'en er ledig igen.")
        if not r.paused:
            self.pause_btn.configure(style="TButton")
        self._jstatus(state="paused" if r.paused else "running")

    def _user_turn(self, active: bool):
        """While the run waits for the user (the Claude round, the cut review) the percentage is grey, and there is
        no Pause: nothing runs then, and a paused run would not notice her answer. Pause comes back afterwards,
        unless the run has stopped meanwhile."""
        r = self.runner
        self.w_pct.configure(foreground=GREY if active else ACCENT)
        if active:
            if r and r.paused:
                self._toggle_pause()
            self.pause_btn.configure(state="disabled")
            self.pause_hint.pack_forget()
        elif self._running and r and not r.cancelled.is_set():
            self.pause_btn.configure(state="normal")
            if not self.pause_hint.winfo_manager():
                self.pause_hint.pack(side="left", padx=6)

    def _cancel(self):
        if self.runner:
            if not boxes.askyesno(APP_NAME, "Vil du stoppe kørslen?\n\nDet, programmet er i gang med, går tabt. "
                                       "Vil du bare frigive pc'en, så brug Pause.", parent=self._owner()):
                return
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")
            self.status.configure(text="Annullerer...")
            self.runner.cancel()

    # --- Worker -------------------------------------------------------------------------

    def _work(self, job: dict, runner: Runner):
        post = self.events.put
        v: Path = job["video"]
        lang: Language = job["lang"]
        speech, subs = job["speech"], job["subs"]
        cut = job["cut"]
        steps = []
        if job["transcribe"] or cut:
            steps.append(("Trækker lyden ud", 1))
        if job["transcribe"]:
            steps.append(("Lytter og skriver tekst", 20))
        if cut:
            steps.append(("Klipper videoen", 6))
        if speech:
            steps += [("Indtaler", 4), ("Laver den dubbede video", 6 if subs else 1)]
        elif subs:
            steps += [("Brænder undertekster ind", 6)]
        prog = engine.Progress(lambda o, s, d: post(("progress", o, s, d, prog.step)), steps, runner)
        work = Path(tempfile.mkdtemp(prefix="sdl-"))
        log.info("JOB video=%s lang=%s speech=%s subs=%s transcribe=%s check=%s translate=%s cut=%s reused_cut=%s", v,
                 lang.code, speech and speech.code, subs and subs.code, job["transcribe"], job["check"],
                 [t.code for t in job["translate"]], cut, job["cut_reused"])
        orig_srt = srt_name(v, lang)

        def srt_of(l: Language) -> Path:
            return srt_name(v, l)

        try:
            info = engine.probe(runner, v)
            log.info("duration %.1f min, audio=%s", info.duration_ms / 60000, info.has_audio)
            wav = work / "audio.wav"
            if job["transcribe"] or cut:
                if not info.has_audio:
                    raise ToolError("Videoen har ingen lyd.")
                prog.start("Trækker lyden ud")
                engine.extract_audio(runner, v, wav)
            if job["transcribe"]:
                prog.start("Lytter og skriver tekst")
                cues = engine.transcribe(runner, wav, lang, job["model"], info.duration_ms,
                                         self.settings.use_gpu, prog, work)
                if not cues:
                    raise ToolError("Der blev ikke fundet nogen tale i videoen.")
                write_srt(orig_srt, cues)
                textstate.record(orig_srt, v)
                post(("file", orig_srt))
            if job["check"] or job["translate"]:
                self._translation_done.clear()
                self.cut_lines = []
                post(("claude", v, lang, orig_srt, job["check"], job["translate"], cut, info.duration_ms))
                while not self._translation_done.wait(0.3):
                    runner.check()
                runner.check()
                if not self._translation_ok:
                    post(("skipped", f"Claude-trinnet blev afbrudt, så videoen er ikke lavet endnu. "
                                     f"De {lang.plural} undertekster er gemt, så næste gang går det hurtigere."))
                    return
            if cut:
                v, info = self._cut_step(job, v, info, wav, prog, runner, work)
                if v is None:   # nothing cut and nothing else to make
                    post(("done", orig_srt))
                    return
            if speech:
                out = dub_name(v, speech, subs)
                engine.dub(runner, v, read_srt(srt_of(speech)), job["voice"], out, srt_of(subs) if subs else None,
                           self.settings.keep_original_audio, info, prog, work, "Indtaler", "Laver den dubbede video",
                           voices=job.get("voices"), palette=job.get("palette"),
                           speaker_model=job.get("speaker_model"))
                post(("file", out))
            elif subs:
                prog.start("Brænder undertekster ind")
                out = burned_name(v, subs)
                engine.burn(runner, v, srt_of(subs), out, info, prog, work)
                post(("file", out))
            post(("done", job["result"]))
        except Cancelled:
            log.info("cancelled")
            post(("cancelled", None))
        except ToolError as e:
            log.error("ToolError: %s", e)
            post(("error", str(e)))
        except Exception as e:
            log.error("crash: %s", traceback.format_exc())
            post(("error", f"Der skete en uventet fejl: {e}\n\nSend gerne logfilen til Kasper."))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _cut_step(self, job: dict, v: Path, info, wav: Path, prog, runner: Runner, work: Path):
        """Suggest cuts (silent pauses + Claude's), let the user review them, cut, and move the subtitles over.
        Returns (the video to carry on with, its info), or (None, None) when nothing was cut and the job only
        asked for cutting."""
        post = self.events.put
        lang = job["lang"]
        cues = read_srt(srt_name(v, lang))
        try:
            env = cutting.envelope(wav)
        except Exception:
            log.exception("loudness envelope failed")
            env = None
        claude_cuts, bad = cutting.parse_cut_lines(self.cut_lines, cues, info.duration_ms)
        if bad:
            log.warning("could not read cut lines: %s", bad)
        pauses = cutting.find_pauses(cues, info.duration_ms, env)
        suggestions = cutting.combine_suggestions(pauses, claude_cuts)
        log.info("cut suggestions: %d from Claude, %d pauses -> %d", len(claude_cuts), len(pauses), len(suggestions))
        self._review_done.clear()
        self._review_result = None
        post(("review", v, info.duration_ms, cues, suggestions, env))
        while not self._review_done.wait(0.3):
            runner.check()
        runner.check()
        ranges = cutting.active(self._review_result or [], info.duration_ms)
        if not ranges:
            log.info("no cuts chosen")
            if not job["speech"] and not job["subs"]:
                return None, None
            job["result"] = self._result_name(v, lang, job["speech"], job["subs"])
            post(("result", job["result"]))
            return v, info
        edited: Path = job["edited"]
        prog.start("Klipper videoen")
        cutting.cut_video(runner, v, ranges, edited, info.duration_ms, info.has_audio, prog, work)
        textstate.record_cut(edited, v, ranges)
        post(("file", edited))
        # The subtitles that belong to the original (and match its blocks) are moved to the cut video.
        checked = textstate.is_checked(srt_name(v, lang))
        for l in LANGUAGES:
            src = srt_name(v, l)
            if not src.exists():
                continue
            try:
                these = read_srt(src)
            except Exception:
                continue
            if l != lang and engine.compare(cues, these) is not None:
                continue
            out = srt_name(edited, l)
            write_srt(out, cutting.remap_cues(these, ranges))
            textstate.record(out, edited)
            if l == lang and checked:
                textstate.set_checked(out, True)
            if l in (lang, job["speech"], job["subs"]):
                post(("file", out))
        return edited, engine.probe(runner, edited)

    def _worker_step_to_display(self, i: int) -> int:
        """The worker's progress steps leave out the steps without progress (Claude, the cut review)."""
        shown = [k for k in range(len(self.step_rows)) if k not in self.manual_steps]
        return shown[i] if 0 <= i < len(shown) else i

    def _poll(self):
        self._inbox_tick += 1
        if self._inbox_tick % 6 == 0:
            try:
                self._check_cancel()
                self._check_inbox()
            except Exception:   # never let the inbox stop the poll loop (it reschedules itself below)
                log.exception("inbox check")
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "progress":
                    if self.runner and self.runner.paused:
                        continue
                    _, overall, status, detail, step = ev
                    if step is not None and step >= 0:
                        self._set_step(self._worker_step_to_display(step))
                    if overall is not None:
                        self.bar.configure(value=overall * 1000)
                        self.w_pct.configure(text=f"{overall * 100:.0f} %")
                    self.status.configure(text=status.split(": ", 1)[-1] if status.startswith("Trin ") else status)
                    if detail:
                        self.detail.configure(text=f"Lige nu:  “{detail.strip()}”")
                    if self.mcp_job and overall is not None and time.time() - self._status_written > 1.5:
                        self._status_written = time.time()
                        step_txt = status.split(": ", 1)[-1] if status.startswith("Trin ") else status
                        name, _, eta = step_txt.partition(" - ")
                        self._jstatus(state="running", percent=int(overall * 100), step=name, eta=eta)
                elif kind == "file":
                    self.add_file(ev[1])
                elif kind == "result":
                    self.job["result"] = ev[1]
                    self.w_result.configure(text=f"Du får:  {ev[1].name}")
                    self._jstatus(result=str(ev[1]))
                elif kind == "review":
                    self._start_review(*ev[1:])
                elif kind == "claude":
                    _, video, lang, src_srt, check, langs, cut, duration = ev
                    self._set_step(self.claude_at)
                    self.detail.configure(text="")
                    self.claude_idle.grid_remove()
                    self._set_claude_width(True)
                    self._tdialog = ClaudePanel(self.claude_box, self, video, lang, src_srt, check, langs,
                                                on_finish=self._translation_finished, mcp_job=self.mcp_job,
                                                cut=cut, duration=duration)
                    self._tdialog.grid(row=0, column=0, sticky="nsew")
                    self._user_turn(True)
                    if self.mcp_job:
                        self.status.configure(text="Claude retter og oversætter i chatten")
                        self._jstatus(state="claude_turn", step="Claude retter og oversætter", eta="",
                                      percent=int(float(self.bar.cget("value")) / 10), message="Teksten er klar til dig.")
                    else:
                        self.status.configure(text="Venter på dig")
                        self.callout.grid()
                        self._pulsing = True
                        self._call_attention()
                elif kind in ("done", "cancelled", "error", "skipped"):
                    self._close_claude_panel()
                    self._close_review()
                    self._running = False
                    if kind == "done":
                        learned = "; ".join(f"{w} => {r}" for w, r in self.learned_words)
                        self._jstatus(state="done", percent=100, result=str(ev[1]),
                                      files=[str(f) for f in self.created],
                                      message=f"Programmet har lært disse rettelser: {learned}" if learned else "")
                    elif kind == "error":
                        self._jstatus(state="error", message=ev[1])
                    else:
                        self._jstatus(state="cancelled", message=ev[1] or "Stoppet i appen.")
                    self.mcp_job = None
                    self._show_result(kind, ev[1])
                elif kind == "selfcheck":
                    self._show_selfcheck(ev[1])
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _show_result(self, kind: str, info):
        for w in (self.d_again, self.d_same, self.d_new, self.d_log, self.d_next_hint):
            w.pack_forget()
        self.d_main.grid_remove()
        if kind == "done":
            self.status.configure(text="Færdig.")
            target: Path = info
            self.d_target = target
            self.d_icon.configure(text="✓", foreground=GREEN)
            self.d_title.configure(text="Færdig!")
            if target.suffix.lower() == ".srt":
                self.d_msg.configure(text="Teksten er klar.")
                self.d_main.configure(text=" Din tekstfil ")
                self.d_play.configure(text="Åbn")
            else:
                learned = ", ".join(f"{w} → {r}" for w, r in self.learned_words)
                self.d_msg.configure(text="Din nye video er klar. Se den igennem, før du deler den."
                                     + (f"\n\nClaude rettede disse ord, og programmet husker dem fremover: {learned}"
                                        if learned else ""))
                self.d_main.configure(text=" Din nye video ")
                self.d_play.configure(text="▶  Afspil")
            self.d_file.configure(text=target.name)
            self.d_main.grid()
            if self._skill_warning:
                reason, have = self._skill_warning
                self._skill_warning = None
                self.root.after(1200, lambda: self.show_skill_guide(reason, have))
            self.d_next_hint.configure(text="Skal den samme video også have andre sprog? Så genbruges teksten, "
                                            "og det går meget hurtigere.")
            self.d_next_hint.pack(anchor="w", pady=(0, 8))
            self.d_same.configure(style="Accent.TButton")
            self.d_same.pack(side="left")
            self.d_new.pack(side="left", padx=10)
            self._call_attention()
        else:
            if kind == "cancelled":
                self.d_icon.configure(text="■", foreground=GREY)
                self.d_title.configure(text="Annulleret")
                self.d_msg.configure(text=self._cancelled_text())
            elif kind == "skipped":
                self.d_icon.configure(text="!", foreground=AMBER)
                self.d_title.configure(text="Ikke færdig")
                self.d_msg.configure(text=info)
            else:
                self.d_icon.configure(text="!", foreground=RED)
                self.d_title.configure(text="Noget gik galt")
                self.d_msg.configure(text=info)
                self._call_attention()
            self.status.configure(text=self.d_title.cget("text"))
            self.d_same.configure(style="TButton")
            self.d_again.configure(text="Fortsæt" if kind == "skipped" else "Prøv igen")
            self.d_again.pack(side="left")
            self.d_same.pack(side="left", padx=10)
            self.d_new.pack(side="left")
            if kind == "error":
                self.d_log.pack(side="left", padx=10)
        self.files.configure(height=max(1, len(self.created)))
        self._show("done")

    def _cancelled_text(self) -> str:
        """What a stopped run kept: the subtitles in the spoken language, if this run wrote them or reused them
        (they were current next to the video), are used again next time. A run that was listening again and
        was stopped before it finished has saved nothing new."""
        job = self.job or {}
        lang = job.get("lang")
        kept = any(p.suffix.lower() == ".srt" for p in self.created)
        if not kept and lang and job.get("video") and not job.get("transcribe"):
            try:
                kept = textstate.status(srt_name(job["video"], lang), job["video"]) == "ok"
            except Exception:
                kept = False
        if kept and lang:
            return f"Kørslen blev stoppet. De {lang.plural} undertekster er gemt, så næste gang går det hurtigere."
        return "Kørslen blev stoppet. Intet blev gemt."

    # --- Results ------------------------------------------------------------------------

    def add_file(self, path: Path):
        if path not in self.created:
            self.created.append(path)
            self.files.insert("end", path.name)

    def _open(self, path: Path | None):
        if path and path.exists():
            os.startfile(path)

    def _show_in_folder(self, path: Path | None):
        if path and path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif path:
            os.startfile(path.parent)

    def _open_folder(self):
        self._show_in_folder(self.created[-1] if self.created else self._video())

    def _open_selected(self):
        sel = self.files.curselection()
        if sel:
            os.startfile(self.created[sel[0]])

    # --- Self check (also tells us whether App Control blocks the bundled tools) ----------

    def _self_check(self):
        problems = []
        for name, exe, args in (("ffmpeg", FFMPEG(), ["-version"]), ("Whisper", WHISPER(), ["--help"]),
                                ("Piper", PIPER(), ["--help"])):
            try:
                if not exe.exists():
                    problems.append(f"{name}: filen mangler ({exe})")
                    continue
                subprocess.run([str(exe)] + args, capture_output=True, timeout=60, creationflags=NO_WINDOW,
                               cwd=str(exe.parent))
            except OSError as e:
                problems.append(f"{name}: kan ikke starte ({e})")
            except subprocess.TimeoutExpired:
                pass
        log.info("self check: %s", problems or "ok")
        if problems:
            self.events.put(("selfcheck", problems))

    def _show_selfcheck(self, problems):
        boxes.showerror(APP_NAME, "Programmet kan ikke starte alle sine værktøjer på denne pc:\n\n"
                             + "\n".join(problems)
                             + "\n\nHvis der står \"blokeret\" eller \"group policy\", stopper EG's sikkerhedspolitik "
                             "programmet. Send denne besked og logfilen til Kasper.\n\nLogfil: " + str(log_file()),
                             parent=self._owner())

    # --- Dialogs and the Claude panel ---------------------------------------------------

    def _settings_dialog(self):
        SettingsDialog(self.root, self.settings)

    # --- The Claude skill's version ---------------------------------------------------------

    def note_skill(self, version: int | None):
        """What Claude reported about its skill in a Claude round: the current version (remember it), an old one or
        none (show the update guide when the run is done)."""
        if version is not None and version >= claude.SKILL_VERSION:
            if self.settings.skill_installed < version:
                self.settings.skill_installed = version
                self.settings.save()
            self._skill_warning = None
            return
        log.info("Claude used skill version %s (expected %s)", version, claude.SKILL_VERSION)
        self._skill_warning = ("missing", None) if version is None else ("old", version)

    def show_skill_guide(self, reason: str, have: int | None = None):
        if self._skill_guide is not None and self._skill_guide.winfo_exists():
            self._skill_guide.lift()
            return
        self._skill_guide = SkillGuide(self.root, self, reason, have)

    def _translation_finished(self, ok: bool):
        self._translation_ok = ok
        self._user_turn(False)
        if ok:
            self._jstatus(state="running", message="")
        self._close_claude_panel()
        if ok and 0 <= self.claude_at < len(self.step_rows):
            self._set_step(self.claude_at + 1)
            self.status.configure(text="Tak! Programmet fortsætter selv.")
        self._translation_done.set()

    # --- The cut review ---------------------------------------------------------------------

    def _start_review(self, video: Path, duration: int, cues, suggestions, env):
        from .review import CutReview
        self._set_step(self.review_at)
        self._review = CutReview(self.left, self, video, duration, cues, suggestions, env,
                                 on_done=self._review_finished, mcp_job=self.mcp_job)
        self._review.grid(row=0, column=0, sticky="nsew")
        for frame in self.views.values():
            frame.grid_remove()
        self.view = "review"
        self._user_turn(True)
        self.status.configure(text="Venter på dig: gennemse klippene")
        # Nothing to do in the Claude card meanwhile: the review gets the whole width
        self.claude_wrap.grid_remove()
        self.left.grid_configure(columnspan=2, padx=0)
        n = len(suggestions)
        if self.mcp_job:   # the list must be there before Claude hears that the user is reviewing it
            jobs.write_json(jobs.job_dir(self.mcp_job) / "cuts.json", self._review.snapshot())
        self._jstatus(state="review_cuts", step="Brugeren gennemser klippene", eta="",
                      message=f"{n} klipforslag vises for brugeren på en tidslinje.")
        self._call_attention()

    def _review_finished(self, chosen, _all):
        self._review_result = list(chosen)
        self._close_review()
        self._show("work")
        self._user_turn(False)
        if 0 <= self.review_at < len(self.step_rows):
            self._set_step(self.review_at + 1)
        self.status.configure(text="Tak! Programmet klipper videoen." if chosen else "Ingen klip. Programmet fortsætter.")
        self._jstatus(state="running", message=f"{len(chosen)} klip godkendt." if chosen else "Ingen klip valgt.")
        self._review_done.set()

    def _close_review(self):
        if self._review is not None:
            try:
                self._review.destroy()
            except tk.TclError:
                pass
            self._review = None
            self.left.grid_configure(columnspan=1, padx=(0, 16))
            self.claude_wrap.grid()

    def _close_claude_panel(self):
        self._pulsing = False
        self.claude_box.configure(style="TLabelframe")
        self.callout.grid_remove()
        if self._tdialog is not None:
            try:
                self._tdialog.destroy()
            except tk.TclError:
                pass
            self._tdialog = None
        self.claude_idle.grid()
        self._set_claude_width(False)

    def _grab_path(self) -> str:
        """The Tk path of the window holding the grab ('' when none). Not root.grab_current(): that raises
        KeyError while a combobox list is open, because its '.!combobox.popdown' has no Python widget."""
        try:
            return self.root.tk.eval("grab current").strip()
        except tk.TclError:
            return ""

    def _grab_window(self):
        """The modal dialog holding the grab, or None (also for a combobox list, which is not a dialog)."""
        path = self._grab_path()
        if not path:
            return None
        try:
            return self.root.nametowidget(path)
        except (KeyError, tk.TclError):
            return None

    def _owner(self):
        """The window a message box belongs to: an open modal dialog, else the main window."""
        return self._grab_window() or self.root

    def _call_attention(self):
        """Ask for attention without moving the window or taking the keyboard from what the user is typing in:
        show it again (without activating it) only if it is minimised, then flash the taskbar button of the
        window that needs her until she looks."""
        try:
            u32 = ctypes.windll.user32
            if self.root.state() == "iconic":
                u32.ShowWindow(ctypes.c_void_p(frame_hwnd(self.root)), 4)  # SW_SHOWNOACTIVATE
            self.root.bell()
            target = self._grab_window()
            guide = getattr(self, "_skill_guide", None)
            if target is None and guide is not None and guide.winfo_exists():
                target = guide
            hwnd = frame_hwnd(target or self.root)

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                            ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3 | 12, 0, 0)  # FLASHW_ALL | FLASHW_TIMERNOFG
            u32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass


class SkillGuide(tk.Toplevel):
    """How to put the new Claude skill into Claude: after an update (this version ships a newer skill), or when
    Claude worked without the skill or with an old one."""

    def __init__(self, parent, app: App, reason: str, have: int | None = None):
        super().__init__(parent)
        self.withdraw()   # shown by place_over, so it never flashes in Windows' default spot
        self.app = app
        heading = "Læg Claude-skill'en ind" if reason == "first" else "Opdatér Claude-skill'en"
        self.title(heading)
        self.transient(parent)
        self.resizable(False, False)
        n = claude.SKILL_VERSION
        intro = {
            "first": "Claude skal have Videosprog-skill'en, så den kender reglerne og EG's fagord. Det gør du én gang.",
            "update": f"Denne version af {APP_NAME} kommer med en ny Claude-skill. Den skal lægges ind i "
                      "Claude én gang, så Claude kender de nye regler.",
            "missing": "Claude arbejdede uden Videosprog-skill'en i den sidste runde. Det gik, men med skill'en kender "
                       "Claude reglerne og EG's fagord bedre.",
            "old": f"Claude brugte en ældre udgave af skill'en (version {have}) i den sidste runde. Den nyeste er "
                   f"version {n}.",
        }[reason]
        f = ttk.Frame(self, padding=18)
        f.pack(fill="both", expand=True)
        wrap = int(520 * app.scale)
        ttk.Label(f, text=heading, style="Section.TLabel", foreground=ACCENT_DARK).pack(anchor="w")
        ttk.Label(f, text=intro, wraplength=wrap, justify="left").pack(anchor="w", pady=(6, 12))
        steps = ["Klik \"Gem skill-filen\". Den lægges i din Overførsler-mappe.",
                 "Åbn Claude, og gå til Customize -> Skills.",
                 "Slet den gamle \"videosprog\", hvis den er der.",
                 "Klik \"Upload skill\", og vælg \"videosprog-skill.zip\"."]
        # The delete step stays on "first" too: skill_installed 0 means unknown, not absent (installed through
        # Settings, "Senere", or before tracking), and uploading over an existing "videosprog" is what it prevents.
        for i, step in enumerate(steps, 1):
            ttk.Label(f, text=f"{i}.  {step}", wraplength=wrap, justify="left").pack(anchor="w", pady=2)
        self.status = ttk.Label(f, text="", style="Hint.TLabel", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", pady=(10, 0))
        b = ttk.Frame(f)
        b.pack(anchor="w", pady=(14, 0))
        save_btn = ttk.Button(b, text="Gem skill-filen", style="Accent.TButton", command=self._save, default="active")
        save_btn.pack(side="left")
        ttk.Button(b, text="Jeg har gjort det", command=self._done).pack(side="left", padx=8)
        ttk.Button(b, text="Senere", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._save())
        place_over(self, parent.winfo_toplevel())
        save_btn.focus_set()

    def _save(self):
        dest = downloads_dir() / f"{claude.SKILL_NAME}-skill.zip"
        try:
            claude.skill_zip(dest)
        except OSError as e:
            self.status.configure(text=f"Kunne ikke gemme filen: {e}", foreground=RED)
            return
        self.status.configure(text=f"Gemt: {dest}", foreground=GREEN)
        subprocess.Popen(["explorer", "/select,", str(dest)])

    def _done(self):
        self.app.settings.skill_installed = claude.SKILL_VERSION
        self.app.settings.save()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.withdraw()
        self.title("Indstillinger")
        self.transient(parent)
        self.resizable(False, False)
        self.s = settings
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both")
        ttk.Label(f, text="Model til at skrive tekst:").grid(row=0, column=0, sticky="w")
        self.model = tk.StringVar(value=settings.whisper_model)
        for i, (key, (label, _)) in enumerate(engine.WHISPER_MODELS.items(), 1):
            ttk.Radiobutton(f, text=label, value=key, variable=self.model).grid(row=i, column=0, sticky="w", padx=12)
        self.gpu = tk.BooleanVar(value=settings.use_gpu)
        ttk.Checkbutton(f, text="Brug grafikkortet, hvis det kan (meget hurtigere på nogle pc'er)", variable=self.gpu).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.keep = tk.BooleanVar(value=settings.keep_original_audio)
        self.voice_mode = tk.StringVar(value=settings.voice_mode)
        dubopts = ttk.Frame(f)
        dubopts.grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(dubopts, text="Behold den originale lyd svagt under dubben", variable=self.keep).pack(anchor="w")
        ttk.Label(dubopts, text="Stemmer i dubben:").pack(anchor="w", pady=(8, 0))
        for value, label in (("person", "Én stemme pr. person (programmet hører selv, hvem der taler)"),
                             ("gender", "Dyb stemme til mænd og lys stemme til kvinder (svensk og engelsk)"),
                             ("one", "Samme stemme til alle")):
            ttk.Radiobutton(dubopts, text=label, value=value, variable=self.voice_mode).pack(anchor="w", padx=12)
        links = ttk.Frame(f)
        links.grid(row=6, column=0, sticky="w", pady=(12, 0))
        ttk.Button(links, text="Åbn modelmappe", command=lambda: self._open(model_dirs()[0])).pack(side="left")
        ttk.Button(links, text="Rediger rettelser", command=lambda: self._open(engine.corrections_file())).pack(side="left", padx=6)
        ttk.Button(links, text="Gem Claude-skill...", command=self._save_skill).pack(side="left", padx=(0, 6))
        ttk.Button(links, text="Forbind med Claude", command=self._connect_claude).pack(side="left", padx=(0, 6))
        ttk.Button(links, text="Åbn logfil", command=lambda: self._open(log_file())).pack(side="left")
        ttk.Label(f, text=f"{APP_NAME} {APP_VERSION}", style="Hint.TLabel").grid(row=7, column=0, sticky="w", pady=(12, 0))
        b = ttk.Frame(f)
        b.grid(row=8, column=0, sticky="e", pady=(12, 0))
        save_btn = ttk.Button(b, text="Gem", style="Accent.TButton", command=self._save, default="active")
        save_btn.pack(side="left")
        ttk.Button(b, text="Annullér", command=self.destroy).pack(side="left", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._save())
        place_over(self, parent.winfo_toplevel())
        save_btn.focus_set()
        self.wait_visibility()
        self.grab_set()

    def _open(self, p: Path):
        if p.suffix:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)

    def _connect_claude(self):
        try:
            path = connect_claude_desktop()
        except Exception as e:
            log.exception("connect to Claude failed")
            boxes.showerror("Forbind med Claude", f"Det lykkedes ikke: {e}", parent=self)
            return
        boxes.showinfo("Forbind med Claude", "Klar. Luk Claude helt (højreklik på Claude-ikonet ved uret -> Quit), "
                            "og åbn Claude igen.\n\nSå kan du skrive fx: \"Lav denne video på svensk med norske "
                            "undertekster: C:\\...\\video.mp4\"\n\n" + str(path), parent=self)

    def _save_skill(self):
        dest = filedialog.asksaveasfilename(parent=self, title="Gem Claude-skill", defaultextension=".zip",
                                            initialdir=str(Path.home() / "Downloads"),
                                            initialfile=f"{claude.SKILL_NAME}.zip", filetypes=[("Zip", "*.zip")])
        if not dest:
            return
        claude.skill_zip(Path(dest))
        boxes.showinfo("Claude-skill", "Gemt. Sådan lægger du den ind i Claude:\n\n"
                            "1. Åbn Claude (Chat eller Cowork), og gå til Customize -> Skills.\n"
                            "2. Klik \"Upload skill\", og vælg zip-filen.\n\n"
                            "Har du en ældre udgave, så slet den først. Den nye har alle de ord, programmet har lært.",
                            parent=self)

    def _save(self):
        self.s.whisper_model, self.s.use_gpu, self.s.keep_original_audio = self.model.get(), self.gpu.get(), self.keep.get()
        self.s.voice_mode = self.voice_mode.get()
        self.s.save()
        self.destroy()


class ModelDialog(tk.Toplevel):
    """Shown when model files are missing: download them, or fetch them by hand."""

    def __init__(self, parent, missing: list[engine.ModelFile], scale: float = 1.0):
        super().__init__(parent)
        self.withdraw()
        self.title("Programmet skal hente nogle filer")
        wrap = int(560 * scale)
        self.minsize(wrap, 0)
        self.transient(parent)
        self.missing = missing
        self.result = False
        self.runner = Runner()
        self.q: queue.Queue = queue.Queue()
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        mb = sum(m.approx_mb for m in missing)
        ttk.Label(f, text=f"Programmet skal bruge disse filer (ca. {mb} MB). Det er kun første gang.",
                  wraplength=wrap).pack(anchor="w")
        for m in missing:
            row = ttk.Frame(f)
            row.pack(anchor="w", fill="x", pady=1)
            ttk.Label(row, text="•  " + m.filename).pack(side="left")
            lnk = ttk.Label(row, text="link", foreground="#0645ad", cursor="hand2")
            lnk.pack(side="left", padx=8)
            lnk.bind("<Button-1>", lambda e, u=m.url: webbrowser.open(u))
        ttk.Label(f, wraplength=wrap, style="Hint.TLabel",
                  text="Virker \"Hent automatisk\" ikke (fx hvis netværket blokerer), så klik på linkene, "
                       "og læg de hentede filer i modelmappen.").pack(anchor="w", pady=(8, 4))
        self.bar = ttk.Progressbar(f, maximum=1000)
        self.bar.pack(fill="x", pady=4)
        self.status = ttk.Label(f, text="")
        self.status.pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(10, 0))
        self.get_btn = ttk.Button(b, text="Hent automatisk", style="Accent.TButton", command=self._download)
        self.get_btn.pack(side="left")
        ttk.Button(b, text="Åbn modelmappe", command=self._open_dir).pack(side="left", padx=6)
        ttk.Button(b, text="Jeg har lagt filerne der", command=self._check).pack(side="left")
        ttk.Button(b, text="Annullér", command=self._close).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.bind("<Return>", lambda e: self._download() if self.get_btn.instate(["!disabled"]) else self._check())
        place_over(self, parent.winfo_toplevel())
        self.get_btn.focus_set()
        self.wait_visibility()
        self.grab_set()
        self.after(150, self._poll)
        self.wait_window()

    def _open_dir(self):
        d = model_dirs()[0]
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(d)

    def _check(self):
        still = [m.filename for m in self.missing if not engine.find_model(m.filename)]
        if still:
            boxes.showwarning("Der mangler filer", "Disse filer findes stadig ikke i modelmappen:\n\n" + "\n".join(still), parent=self)
        else:
            self.result = True
            self.destroy()

    def _close(self):
        self.runner.cancel()
        self.destroy()

    def _download(self):
        self.get_btn.configure(state="disabled")

        def work():
            try:
                todo = [m for m in self.missing if not engine.find_model(m.filename)]
                for n, m in enumerate(todo):
                    engine.download(m, lambda fr, txt: self.q.put(("p", (n + fr) / len(todo), txt)), self.runner)
                self.q.put(("ok",))
            except Cancelled:
                pass
            except Exception as e:
                log.error("download failed: %s", e)
                self.q.put(("err", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "p":
                    self.bar.configure(value=ev[1] * 1000)
                    self.status.configure(text=ev[2])
                elif ev[0] == "ok":
                    self.result = True
                    self.destroy()
                    return
                else:
                    self.get_btn.configure(state="normal")
                    boxes.showerror("Kunne ikke hente", f"Hentningen fejlede:\n{ev[1]}\n\nBrug linkene i stedet.", parent=self)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.after(150, self._poll)


class ClaudePanel(ttk.Frame):
    """The Claude round, in the right-hand card.

    File mode (normal): the program writes "<video> - til Claude.txt"; the user copies the file into a Claude chat,
    the skill writes "<video> - fra Claude.txt", the user downloads it, and the program picks it up from the Downloads
    folder by the job id in its first line, checks it and carries on by itself.

    Parts mode (fallback): copy a prompt per part and paste Claude's answer back, as before.

    One answer holds the corrected text in the spoken language (when `check`), the translations to `targets`,
    and the words Claude corrected. `on_finish(True/False)` tells the waiting run the outcome."""

    def __init__(self, master, app: App, video: Path, src: Language, src_srt: Path, check: bool,
                 targets: list[Language], on_finish=None, mcp_job: str | None = None, cut: bool = False,
                 duration: int = 0):
        super().__init__(master)
        self.mcp_job = mcp_job
        self.cut, self.duration = cut, duration
        self.cut_parts: dict[int, list[str]] = {}
        self.app, self.video, self.src, self.src_srt = app, video, src, src_srt
        self.check, self.targets, self.on_finish = check, list(targets), on_finish
        self.codes = ([src.code] if check else []) + [t.code for t in self.targets]
        self.cues = read_srt(src_srt)
        self.words: list[tuple[str, str]] = []
        self.job_id = uuid.uuid4().hex[:10]
        self.work_file = output_path(video, "til Claude.txt")
        self.answer_name = output_path(video, "fra Claude.txt").name
        self.started = time.time()
        self.mode = "file"
        self.watching: int | None = None
        self._prompt_text = ""
        self._seen_clip = ""
        self._seen_files: dict[str, float] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        todo = ([f"rette de {src.plural} undertekster"] if check else []) + \
               ([f"oversætte dem til {' og '.join(t.name.lower() for t in self.targets)}"] if self.targets else []) + \
               (["foreslå, hvad der skal klippes væk"] if cut else [])
        todo = [", ".join(todo[:-1]), todo[-1]] if len(todo) > 2 else todo
        self.head = ttk.Label(self, text="Din tur", style="Big.TLabel", foreground=ACCENT)
        self.head.grid(row=0, column=0, sticky="w")
        intro = ttk.Label(self, justify="left", text=f"Claude skal {' og '.join(todo)}.")
        intro.grid(row=1, column=0, sticky="ew", pady=(2, 12))
        app._wrap(intro, self, 10)
        self.body = ttk.Frame(self)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.footer = ttk.Frame(self)
        self.footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.footer.columnconfigure(0, weight=1)
        self.switch_btn = ttk.Button(self.footer, text="Brug kopiér/indsæt i dele i stedet", command=self._to_parts)
        self.switch_btn.grid(row=0, column=0, sticky="w")
        ttk.Button(self.footer, text="Afbryd", command=self._close).grid(row=0, column=1, padx=(0, 8))
        self.save_btn = ttk.Button(self.footer, text="Gem og fortsæt", command=self._save_parts)
        # Whatever is on the clipboard now is old (maybe an answer from an earlier round): never accept it.
        try:
            self._seen_clip = self.clipboard_get()
        except tk.TclError:
            self._seen_clip = ""
        if mcp_job:
            self._build_mcp_mode()
        else:
            self._build_file_mode()
        self.after(700, self._tick)

    # --- Claude does it in the chat (job from the MCP server) ----------------------------------

    def _build_mcp_mode(self):
        self.mode = "mcp"
        self.head.configure(text="Claude arbejder")
        d = jobs.job_dir(self.mcp_job)
        jobs.write_json(d / "source.json", jobs.cues_to_json(self.cues))
        jobs.write_json(d / "task.json", {
            "src": self.src.code, "check": self.check, "targets": [t.code for t in self.targets],
            "codes": self.codes, "blocks": len(self.cues), "words": claude.known_words(),
            "cut": self.cut, "duration": self.duration,
            "rules": [list(r) for r in claude.word_rules()[:80]]})
        b = self.body
        msg = ttk.Label(b, justify="left", text=(
            "Claude henter teksten, retter og oversætter den og sender den tilbage hertil. Du skal ikke gøre noget her.\n\n"
            "Følg med i chatten. Er Claude stoppet undervejs, så skriv \"fortsæt\" til Claude."))
        msg.grid(row=0, column=0, sticky="ew")
        self.app._wrap(msg, b, 10)
        names = {l.code: l.name for l in LANGUAGES}
        self.mcp_bars = {}
        for i, code in enumerate(self.codes, 1):
            row = ttk.Frame(b, padding=(0, 10, 0, 0))
            row.grid(row=i, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)
            ttk.Label(row, text=names[code] + (" (rettet)" if code == self.src.code else ""), width=16).grid(row=0, column=0, sticky="w")
            bar = ttk.Progressbar(row, maximum=len(self.cues))
            bar.grid(row=0, column=1, sticky="ew", padx=8)
            lab = ttk.Label(row, text=f"0 af {len(self.cues)}", style="Hint.TLabel", width=12)
            lab.grid(row=0, column=2, sticky="e")
            self.mcp_bars[code] = (bar, lab)
        self.mcp_cut_lbl = None
        if self.cut:
            self.mcp_cut_lbl = ttk.Label(b, text="Klipforslag: ikke modtaget endnu", style="Hint.TLabel")
            self.mcp_cut_lbl.grid(row=len(self.codes) + 1, column=0, sticky="w", pady=(10, 0))
        self.switch_btn.configure(text="Gør det selv med en fil i stedet", command=self._mcp_to_file)

    def _mcp_to_file(self):
        self.mode = "file"
        self.head.configure(text="Din tur")
        for w in self.body.winfo_children():
            w.destroy()
        self.switch_btn.configure(text="Brug kopiér/indsæt i dele i stedet", command=self._to_parts)
        self.app.callout.grid()
        self.app._pulsing = True
        self._build_file_mode()

    def _mcp_tick(self):
        d = jobs.root() / self.mcp_job
        for code, (bar, lab) in self.mcp_bars.items():
            n = len(jobs.read_json(d / "parts" / f"{code}.json", {}) or {})
            bar.configure(value=n)
            lab.configure(text=f"{n} af {len(self.cues)}")
        if self.mcp_cut_lbl is not None:
            k = jobs.read_json(d / "parts" / "klip.json", {}) or {}
            n = len(k.get("lines") or [])
            self.mcp_cut_lbl.configure(text=f"Klipforslag: {n} modtaget" + (" (færdig)" if k.get("done") else ""))
        done = jobs.read_json(d / "complete.json")
        if done:
            self.words = [tuple(w) for w in done.get("words", [])]
            self.app.cut_lines = list(done.get("cuts") or [])
            skill = jobs.read_json(d / "skill.json")
            if skill is not None:
                self.app.note_skill(skill.get("skill"))
            by_code = {code: jobs.cues_from_json(rows) for code, rows in done["codes"].items()}
            self.on_finish and self._finish(by_code)

    # --- File mode ---------------------------------------------------------------------

    def _build_file_mode(self):
        try:
            self.work_file.write_text(claude.work_file_text(self.src, self.check, self.targets, self.cues,
                                                            self.job_id, self.answer_name, self.cut), encoding="utf-8")
        except OSError as e:
            log.error("could not write work file: %s", e)
            boxes.showwarning("Claude", f"Kunne ikke skrive arbejdsfilen ({e}). Brug kopiér/indsæt i dele.",
                                   parent=self.app.root)
            self._to_parts()
            return
        b = self.body
        self.fsteps = []
        k = self.app.scale
        texts = [
            ("Kopiér arbejdsfilen", f"\"{self.work_file.name}\""),
            ("Indsæt den i en ny chat i Claude (Ctrl+V), og tryk send",
             "Kan du ikke indsætte den, så træk filen fra mappen ind i Claude."),
            ("Hent Claudes svarfil", "Klik på filen i Claudes svar, og tryk Download. Programmet finder den selv "
                                     "i din Overførsler-mappe."),
        ]
        for i, (title, sub) in enumerate(texts):
            row = ttk.Frame(b, padding=(0, 6))
            row.grid(row=i, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)
            icon = tk.Canvas(row, width=int(26 * k), height=int(26 * k), highlightthickness=0, background=CARD_BG)
            icon.grid(row=0, column=0, rowspan=2, sticky="nw", pady=(2, 0))
            t = ttk.Label(row, text=f"{title}", font="SunValleyBodyStrongFont")
            t.grid(row=0, column=1, sticky="w", padx=(10, 0))
            s = ttk.Label(row, text=sub, style="Hint.TLabel", justify="left")
            s.grid(row=1, column=1, sticky="ew", padx=(10, 0))
            self.app._wrap(s, row, int(60 * k))
            self.fsteps.append({"icon": icon, "title": t, "sub": s, "row": row, "state": "todo"})
        acts = ttk.Frame(self.fsteps[0]["row"])
        acts.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        self.copy_file_btn = ttk.Button(acts, text="Kopiér fil", command=self._copy_file, width=12)
        self.copy_file_btn.pack(side="left")
        ttk.Button(acts, text="Vis i mappe", command=lambda: self.app._show_in_folder(self.work_file)).pack(side="left", padx=8)
        acts3 = ttk.Frame(self.fsteps[2]["row"])
        acts3.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        ttk.Button(acts3, text="Vælg svarfilen selv...", command=self._pick_answer).pack(side="left")
        # What the program is doing about step 3 right now (waiting, found a file, checking it)
        self.wait_lbl = ttk.Label(self.fsteps[2]["row"], text="", style="Hint.TLabel", justify="left")
        self.wait_lbl.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))
        self.app._wrap(self.wait_lbl, self.fsteps[2]["row"], int(60 * k))
        self._wait_since: float | None = None
        self.result = ttk.Label(b, text="", justify="left")
        self.result.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.app._wrap(self.result, b, 10)
        self.problem_detail = ttk.Label(b, text="", style="Hint.TLabel", foreground=GREY, justify="left")
        self.app._wrap(self.problem_detail, b, 10)
        self.followup_btn = ttk.Button(b, text="Kopiér besked til Claude", command=self._copy_followup)
        self._set_fstep(0)

    def _set_fstep(self, active: int, failed: bool = False):
        for i, st in enumerate(self.fsteps):
            st["state"] = "done" if i < active else ("active" if i == active else "todo")
            if failed and i == active:
                st["state"] = "failed"
            st["title"].configure(foreground=GREEN if st["state"] == "done" else
                                  RED if st["state"] == "failed" else
                                  ACCENT if st["state"] == "active" else GREY)
            self._draw_fstep(st)
        self.copy_file_btn.configure(style="Accent.TButton" if active == 0 else "TButton")

    def _draw_fstep(self, st: dict, angle: float = 0.0):
        c, k = st["icon"], self.app.scale
        c.delete("all")
        s = int(c.cget("width"))
        p = 3 * k
        n = str(self.fsteps.index(st) + 1)
        if st["state"] == "done":
            c.create_oval(p, p, s - p, s - p, outline=GREEN, fill=GREEN)
            c.create_line(s * 0.30, s * 0.52, s * 0.45, s * 0.66, s * 0.71, s * 0.37,
                          fill="white", width=2 * k, capstyle="round", joinstyle="round")
        elif st["state"] == "failed":
            c.create_oval(p, p, s - p, s - p, outline=RED, fill=RED)
            c.create_text(s / 2, s / 2, text="!", fill="white", font="SunValleyBodyStrongFont")
        elif st["state"] == "active":
            c.create_oval(p, p, s - p, s - p, outline=ACCENT, fill=ACCENT)
            c.create_text(s / 2, s / 2, text=n, fill="white", font="SunValleyBodyStrongFont")
        else:
            c.create_oval(p, p, s - p, s - p, outline="#c4c4c4", width=1.5 * k)
            c.create_text(s / 2, s / 2, text=n, fill=GREY, font="SunValleyCaptionFont")

    def _copy_file(self):
        if copy_file_to_clipboard(self.work_file):
            self.fsteps[0]["sub"].configure(text=f"Kopieret: \"{self.work_file.name}\"")
            # Step 2 (paste it into Claude) stays open until an answer file shows up: the program cannot see the chat
            if self.fsteps[2]["state"] not in ("active", "failed"):
                self._set_fstep(1)
            if self._wait_since is None:
                self._wait_since = time.time()
        else:
            self.fsteps[0]["sub"].configure(text="Kunne ikke kopiere filen. Klik \"Vis i mappe\", og træk filen ind i Claude.")
            self.app._show_in_folder(self.work_file)

    def _pick_answer(self):
        p = filedialog.askopenfilename(parent=self.app.root, title="Vælg Claudes svarfil",
                                       initialdir=str(downloads_dir()), filetypes=[("Tekst", "*.txt *.srt *.md"), ("Alle filer", "*.*")])
        if p:
            self._try_answer_file(Path(p), picked=True)

    def _scan_downloads(self):
        """Look for a new answer file with our job id in the Downloads folder."""
        d = downloads_dir()
        try:
            files = [f for f in d.iterdir() if f.suffix.lower() in (".txt", ".srt", ".md") and f.is_file()]
        except OSError:
            return
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < self.started - 5 or st.st_size > 50_000_000:
                continue
            key = str(f)
            if self._seen_files.get(key) == st.st_mtime:
                continue
            self._seen_files[key] = st.st_mtime
            try:
                head = f.open("r", encoding="utf-8-sig", errors="replace").readline()
            except OSError:
                continue
            if claude.answer_id(head) == self.job_id:
                self._try_answer_file(f)
                return

    def _try_answer_file(self, f: Path, picked: bool = False):
        # An answer file means steps 1 and 2 happened, whatever was clicked here
        self._set_fstep(2)
        self.wait_lbl.configure(text=f"Fundet: {f.name} - tjekker...")
        self.update_idletasks()
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            self._show_problem(f"Kunne ikke læse {f.name}: {e}",
                               plain="Programmet kunne ikke læse svarfilen. Hent den igen fra Claude.", followup=False)
            return
        aid = claude.answer_id(text)
        if picked and aid not in (None, self.job_id):
            self._show_problem("Det er en svarfil til en anden video eller en tidligere runde.",
                               plain="Den fil hører til en anden video eller en tidligere runde. Vælg svaret fra "
                                     "denne runde.", followup=False)
            return
        have, words, msg = claude.check_file_answer(self.cues, self.codes, text, self.cut)
        log.info("answer file %s: %s", f.name, msg or "ok")
        if msg:
            self._show_problem(msg)
            return
        self.words = list(words)
        self.app.cut_lines = claude.answer_cuts(text) or []
        self.app.note_skill(claude.answer_skill(text))
        self._set_fstep(3)
        self.wait_lbl.configure(text=f"Fundet: {f.name}")
        self.result.configure(text=f"✓  Svaret er i orden ({len(self.cues)} blokke). Programmet fortsætter...",
                              foreground=GREEN, font="SunValleyBodyStrongFont")
        self.problem_detail.grid_remove()
        self.followup_btn.grid_remove()
        self.after(1200, lambda: self._finish(have))

    def _show_problem(self, msg: str, plain: str | None = None, followup: bool = True):
        """One plain sentence for the user; the technical reason only as a small grey line (and in the log)."""
        log.info("answer problem: %s", msg)
        self._followup = claude.followup_text(self.answer_name, msg, self.cut)
        self._set_fstep(2, failed=True)
        self.wait_lbl.configure(text="")
        self.result.configure(text=plain or ("Claudes svar var ikke helt færdigt. Klik \"Kopiér besked til Claude\", "
                                             "indsæt den i samme chat (Ctrl+V), og hent den nye fil."),
                              foreground=RED, font="SunValleyBodyFont")
        self.problem_detail.configure(text=f"Detaljer: {msg}")
        self.problem_detail.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        if followup:
            self.followup_btn.grid(row=5, column=0, sticky="w", pady=(8, 0))
            self.followup_btn.configure(style="Accent.TButton")
        else:
            self.followup_btn.grid_remove()
        self._wait_since = time.time()
        self.started = time.time() - 4  # only newer downloads from now on

    def _copy_followup(self):
        self.clipboard_clear()
        self.clipboard_append(self._followup)
        self.update()
        self.followup_btn.configure(text="Kopieret - indsæt i Claude", style="TButton")
        self.after(3000, lambda: self.winfo_exists() and self.followup_btn.configure(text="Kopiér besked til Claude"))

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.mode == "mcp" and self.on_finish:
            self._mcp_tick()
        elif self.mode == "file" and self.on_finish:
            if self.fsteps[2]["state"] != "done":
                # Also before "Kopiér fil": the user may have dragged the file into Claude themselves
                self._scan_downloads()
            if self._wait_since is not None and self.fsteps[2]["state"] != "done" and \
                    not self.wait_lbl.cget("text").startswith("Fundet"):
                mins = int((time.time() - self._wait_since) // 60)
                self.wait_lbl.configure(text="Venter på Claudes svarfil i Overførsler..."
                                             + (f" ({mins} min)" if mins else ""))
            # Claude's answer pasted as text also counts (if Claude wrote it in the chat instead of a file)
            try:
                text = self.clipboard_get()
            except tk.TclError:
                text = ""
            if text and text != self._seen_clip and "-->" in text and "===" in text:
                self._seen_clip = text
                have, words, msg = claude.check_file_answer(self.cues, self.codes, text + "\n=== slut ===", self.cut)
                if msg is None:
                    self.words = list(words)
                    self.app.cut_lines = claude.answer_cuts(text) or []
                    self.app.note_skill(claude.answer_skill(text))
                    self._set_fstep(3)
                    self.wait_lbl.configure(text="Fundet: Claudes svar (kopieret fra chatten)")
                    self.problem_detail.grid_remove()
                    self.result.configure(text="✓  Svaret er i orden. Programmet fortsætter...", foreground=GREEN)
                    self.after(1200, lambda: self._finish(have))
        elif self.mode == "parts":
            self._watch_clipboard()
        self.after(900, self._tick)

    # --- Parts mode (fallback) -------------------------------------------------------------

    def _to_parts(self):
        self.mode = "parts"
        for w in self.body.winfo_children():
            w.destroy()
        self.switch_btn.grid_remove()
        self.save_btn.grid(row=0, column=2)
        self.parts = claude.split_parts(self.cues, len(self.codes))
        self.answers: list[dict | None] = [None] * len(self.parts)
        self.partial: list[dict] = [{} for _ in self.parts]
        self.copied = [False] * len(self.parts)
        self.words = []
        b = self.body
        b.rowconfigure(2, weight=1)
        self.next_box = tk.Frame(b, background=TINT, padx=12, pady=10)
        self.next_box.grid(row=0, column=0, sticky="ew")
        self.next_box.columnconfigure(0, weight=1)
        self.next_lbl = tk.Label(self.next_box, text="", font="SunValleyBodyStrongFont", background=TINT,
                                 foreground=ACCENT_DARK, justify="left", anchor="w")
        self.next_lbl.grid(row=0, column=0, sticky="ew")
        self.next_box.bind("<Configure>", lambda e: self.next_lbl.configure(wraplength=max(150, e.width - 30)))
        ttk.Label(b, text=(f"Teksten er delt i {len(self.parts)} dele, så Claudes svar ikke bliver afkortet. "
                           "Næste del kan sendes i samme chat." if len(self.parts) > 1 else ""),
                  style="Hint.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 4))
        holder = ttk.Frame(b)
        holder.grid(row=2, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, background=self.app.bg)
        sb = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        rows = ttk.Frame(canvas)
        win = canvas.create_window(0, 0, window=rows, anchor="nw")

        def fit(_=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            if rows.winfo_reqheight() > canvas.winfo_height():
                sb.grid(row=0, column=1, sticky="ns")
            else:
                sb.grid_remove()
        rows.bind("<Configure>", fit)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), fit()))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
                        if str(e.widget).startswith(str(canvas)) else None)
        self.canvas = canvas
        rows.columnconfigure(0, weight=1)
        self.state_labels, self.copy_btns, self.paste_btns, self.part_titles = [], [], [], []
        n0 = 1
        for i, part in enumerate(self.parts):
            box = ttk.Frame(rows, padding=(0, 8))
            box.grid(row=i, column=0, sticky="ew")
            box.columnconfigure(0, weight=1)
            title = ttk.Label(box, text=f"Del {i + 1}  (blok {n0}-{n0 + len(part) - 1})", font="SunValleyBodyStrongFont")
            title.grid(row=0, column=0, sticky="w")
            cb = ttk.Button(box, text="Kopiér", width=9, command=lambda i=i: self._copy(i))
            cb.grid(row=0, column=1, padx=6)
            pb = ttk.Button(box, text="Indsæt svar", width=11, command=lambda i=i: self._paste(i))
            pb.grid(row=0, column=2)
            lab = ttk.Label(box, text="", justify="left", foreground=GREY)
            lab.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))
            box.bind("<Configure>", lambda e, lab=lab: lab.configure(wraplength=max(150, e.width - 10)))
            self.state_labels.append(lab)
            self.copy_btns.append(cb)
            self.paste_btns.append(pb)
            self.part_titles.append(title)
            n0 += len(part)
        self._guide()

    def _guide(self):
        """Parts mode: highlight the one button to press next and say what to do."""
        for b in self.copy_btns + self.paste_btns:
            b.configure(style="TButton")
        self.save_btn.configure(style="TButton", state="disabled")
        nxt = next((i for i, a in enumerate(self.answers) if a is None), None)
        for i, t in enumerate(self.part_titles):
            t.configure(foreground=GREEN if self.answers[i] else (ACCENT if i == nxt else ""))
        if nxt is None:
            self.watching = None
            self.save_btn.configure(state="normal", style="Accent.TButton")
            self.save_btn.focus_set()
            self.next_lbl.configure(text="Alt er klar. Tryk \"Gem og fortsæt\" nederst, så laver programmet resten.")
            return
        n = f"del {nxt + 1}" if len(self.parts) > 1 else "teksten"
        if not self.copied[nxt]:
            self.copy_btns[nxt].configure(style="Accent.TButton")
            self.copy_btns[nxt].focus_set()
            self.next_lbl.configure(text=f"Næste: klik \"Kopiér\" ud for {n}, og indsæt den i Claude (Ctrl+V).")
        else:
            self.paste_btns[nxt].configure(style="Accent.TButton")
            self.next_lbl.configure(text=f"Venter på Claudes svar på {n}. Når Claude er færdig, så klik på kopiknappen "
                                         "øverst på Claudes kodeblok - programmet opdager det selv.")

    def _watch_clipboard(self):
        if self.watching is None:
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if text and text != self._prompt_text and text != self._seen_clip and "-->" in text:
            self._seen_clip = text
            self._paste(self.watching, text)

    def _first_number(self, i: int) -> int:
        return 1 + sum(len(p) for p in self.parts[:i])

    def _copy(self, i: int):
        text = claude.prompt(self.src, self.check, self.targets, self.parts[i], self._first_number(i), i, len(self.parts),
                             self.cut)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self._prompt_text = text
        self.copied[i] = True
        self.watching = i
        self.state_labels[i].configure(text="Kopieret. Indsæt i Claude med Ctrl+V og send.", foreground=GREY)
        self._guide()

    def _paste(self, i: int, text: str | None = None):
        if text is None:
            try:
                text = self.clipboard_get()
            except tk.TclError:
                text = ""
        have, words, msg = claude.check_answer(self.parts[i], self.codes, text, self.partial[i])
        self.partial[i] = have
        cut_lines = claude.answer_cuts(text) if self.cut else None
        if cut_lines is not None:
            self.cut_parts[i] = cut_lines
        self.words += words
        if msg:
            self.answers[i] = None
            got = [claude.LANG_BY_CODE[c].name.lower() for c in self.codes if c in have]
            prefix = f"Har {', '.join(got)}. " if got and len(self.codes) > 1 else ""
            self.state_labels[i].configure(text="✗ " + prefix + msg, foreground=RED)
        else:
            self.answers[i] = have
            extra = f", {len(set(self.words))} ord rettet" if self.words else ""
            self.state_labels[i].configure(text=f"✓ {len(self.parts[i])} blokke ok{extra}", foreground=GREEN)
            if self.watching == i:
                self.watching = None
        self._guide()

    def _save_parts(self):
        if not all(self.answers):
            return
        self.app.cut_lines = [l for i in sorted(self.cut_parts) for l in self.cut_parts[i]]
        self._finish({code: [c for part in self.answers for c in part[code]] for code in self.codes})

    # --- Both modes ------------------------------------------------------------------------

    def _close(self):
        if self.on_finish:
            if not boxes.askyesno("Claude", "Claude-trinnet er ikke færdigt, så videoen bliver ikke lavet.\n\n"
                                       "Afbryd alligevel?", parent=self.app.root):
                return
            done, self.on_finish = self.on_finish, None
            try:
                self.work_file.unlink(missing_ok=True)
            except OSError:
                pass
            done(False)

    def _finish(self, by_code: dict[str, list[Cue]]):
        if not self.on_finish:
            return
        written = []
        for code in self.codes:
            lang = claude.LANG_BY_CODE[code]
            out = srt_name(self.video, lang)
            write_srt(out, by_code[code])
            written.append(out)
            if code == self.src.code:
                textstate.set_checked(out, True)
            self.app.add_file(out)
        new = claude.add_words(list(dict.fromkeys(self.words)))
        log.info("claude round saved: %s; new words %s", [w.name for w in written], new)
        try:
            self.work_file.unlink(missing_ok=True)
        except OSError:
            pass
        # No dialog here: it would stop the run until someone clicks OK (nobody does when Claude drives it).
        # The learned words are shown in the result view and sent to Claude with the status instead.
        self.app.learned_words = list(new)
        done, self.on_finish = self.on_finish, None
        if done:
            done(True)


def connect_claude_desktop() -> Path:
    """Add this program as an MCP server in Claude desktop's config (keeps everything else, backs the file up)."""
    import json as _json
    cfg = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cfg.exists():
        raw = cfg.read_text(encoding="utf-8-sig")
        data = _json.loads(raw) if raw.strip() else {}
        shutil.copyfile(cfg, cfg.with_name(cfg.name + ".videosprog.bak"))
    if getattr(sys, "frozen", False):
        entry = {"command": sys.executable, "args": ["--mcp"]}
    else:
        entry = {"command": sys.executable, "args": [str(Path(__file__).resolve().parent.parent / "main.py"), "--mcp"]}
    servers = data.setdefault("mcpServers", {})
    servers.pop("video-undertekster", None)   # the name before the rename
    servers["videosprog"] = entry
    tmp = cfg.with_name(cfg.name + ".tmp")
    tmp.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg)
    log.info("connected to Claude desktop: %s -> %s", cfg, entry)
    return cfg


SINGLE_INSTANCE = "Local\\Videosprog.SingleInstance"


def already_running() -> bool:
    """Only one window: a second start brings the first one forward instead."""
    try:
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        main.mutex = k32.CreateMutexW(None, False, SINGLE_INSTANCE)
        if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return False
        # By window class too: a message box or a Claude window titled "Videosprog" must not match
        u32.FindWindowW.restype = ctypes.c_void_p
        hwnd = u32.FindWindowW("TkTopLevel", APP_NAME)
        if hwnd:
            hwnd = ctypes.c_void_p(hwnd)
            u32.ShowWindow(hwnd, 9 if u32.IsIconic(hwnd) else 5)  # SW_RESTORE only when minimised, else SW_SHOW
            u32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _safe(fn):
    try:
        fn()
    except Exception:
        log.exception("%s failed", getattr(fn, "__name__", fn))


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    from .installer import offer_install
    if "--from-claude" not in sys.argv and not offer_install():   # installed (or the installed copy started); this copy stops here
        return
    if already_running():
        return
    log.info("start %s %s", APP_NAME, APP_VERSION)
    from .installer import cleanup, running_installed
    if running_installed():   # old version folders nothing runs from any more
        threading.Thread(target=lambda: _safe(cleanup), daemon=True).start()
    root = tk.Tk()
    App(root)
    root.mainloop()
