"""The tkinter GUI, in the app's own language (i18n). All work runs in a worker thread; the GUI polls a queue."""
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
from . import i18n, skin, theme, voice_preview
from .i18n import t
from .pickers import LanguagePicker, ThemePicker, Tooltip, language_tiles, theme_tiles
from .theme import IMG, P
from .common import (APP_NAME, APP_TAGLINE, APP_VERSION, branding_dir, set_app_icon, set_app_id, FFMPEG, LANG_BY_CODE, LANGUAGES, NO_WINDOW, PIPER, WHISPER, Cancelled,
                     Cue, Language, Runner, ToolError, burned_name, copy_file_to_clipboard, cut_name, data_dir, downloads_dir,
                     dub_name, find_cut, find_srt, lang_by_name,
                     free_memory_gb, output_path,
                     log, log_file, model_dirs, read_srt, srt_name, write_srt)

PAD = {"padx": 8, "pady": 4}


def video_types() -> list[tuple[str, str]]:
    return [(t("gui.filetype.video"), "*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.wmv"), (t("gui.filetype.all"), "*.*")]


def join_and(items: list[str]) -> str:
    """"a, b og c" in the app's language."""
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " " + t("gui.and") + " " + items[-1]


def srt_path(video: Path, lang: Language) -> Path:
    """The subtitles in lang already made for video (named in any app language), else where new ones are written."""
    return find_srt(video, lang) or srt_name(video, lang)


def cut_path(video: Path) -> Path:
    """The cut video already made from video (named in any app language), else where a new one is written."""
    return find_cut(video) or cut_name(video)


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
        self.theme = "system"              # Udseende: "system" (follow Windows), "light" (Lys) or "night" (Nat)
        self.ui_language = ""              # Programmets sprog: da, sv, no or en ("" until picked: Windows' language)
        self.last_dir = str(Path.home() / "Videos")
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.__dict__.update(saved)
            if "ui_language" not in saved:
                self.ui_language = "da"   # used before 2.1, when the app was Danish only
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
    skin.dialog(win)
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
        i18n.set_language(i18n.resolve(self.settings.ui_language))
        self.events: queue.Queue = queue.Queue()
        self.runner: Runner | None = None
        self.created: list[Path] = []
        self.learned_words: list[tuple[str, str]] = []
        self.job: dict | None = None
        self._running = False
        self._starting = False   # The user's own start is waiting in a dialog (a question, the model download)

        root.title(APP_NAME)
        set_app_icon(root)
        # Sizes in logical pixels, scaled to the display (the process is DPI aware), within the screen.
        scale = self.scale = root.winfo_fpixels("1i") / 96
        self.theme_name = theme.resolve(self.settings.theme)
        skin.apply(root, self.theme_name, scale)
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
        self.bg = P.window

        self.video = tk.StringVar()
        # The comboboxes show words in the app's language; _lang/_speech_lang/_subs_lang read the language behind them
        self.lang = tk.StringVar(value=LANG_BY_CODE["da"].name)
        self.speech = tk.StringVar()       # "Original (dansk)" or a language's name
        self.subs = tk.StringVar()         # "Ingen" or a language's subtitle-list word ("Danske", "Svenske", ...)
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
        self._progress_names: list[str] = []   # the worker's progress steps (engine.Progress), in order
        self.mcp_job: str | None = None      # the job Claude started through the MCP server, if any
        self._inbox_tick = 0
        self._last_result = None            # (kind, info) of the done view, to draw it again after a theme switch
        self._theme_pending = None          # a theme picked during a run, applied at its end
        self._language_pending = None       # the same for the app's language
        self._status_written = 0.0
        try:
            jobs.cleanup()
        except Exception:
            log.exception("job cleanup failed")

        self._build()
        skin.skin_cards(self.main)
        self._refresh()
        self.video.trace_add("write", lambda *_: self._refresh())
        root.after(50, lambda: theme.dark_title_bar(root, self.theme_name == "night"))
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
        self._build_header()
        f = self.main = ttk.Frame(self.root, padding=(20, 16))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=3, uniform="cols")
        self._set_claude_width(False)
        f.rowconfigure(1, weight=1)

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
        self.claude_box = skin.Card(self.claude_wrap, text=" Claude ", padding=12)
        self.claude_box.grid(row=0, column=0, sticky="nsew")
        self.claude_box.columnconfigure(0, weight=1)
        self.claude_box.rowconfigure(0, weight=1)
        self.claude_idle = ttk.Frame(self.claude_box)
        self.claude_idle.grid(row=0, column=0, sticky="nsew")
        # While Claude waits: the app icon in the middle of the card, PLAYER 2 KLAR, and what will happen
        # (design review round 2, item 11)
        k = self.scale
        mid = ttk.Frame(self.claude_idle)
        mid.place(relx=0.5, rely=0.45, anchor="center")
        self._idle_icon = tk.PhotoImage(master=self.root, file=str(branding_dir() / (
            "icon-128.png" if IMG.two else "icon-64.png")))
        ttk.Label(mid, image=self._idle_icon).pack()
        ttk.Label(mid, text=t("gui.claude.player2").upper(), style="Hint.TLabel",
                  font=(theme.PIXEL_FONT, -int(12 * k))).pack(pady=(int(14 * k), int(12 * k)))
        idle = ttk.Label(mid, justify="center", style="Hint.TLabel", text=t("gui.claude.idle"),
                         wraplength=int(360 * k))
        idle.pack()

    def _build_header(self):
        """The header band across the window: logo and CUT · DUB · SUB, then from the right Indstillinger (the cog),
        the language picker and the theme picker, and the four-colour stripe under it (assets/ui/README.md "Header")."""
        k = self.scale
        band = self.header = tk.Frame(self.root, background=P.header_band, padx=int(24 * k), pady=int(10 * k))
        band.pack(side="top", fill="x")
        brand = tk.Frame(band, background=P.header_band)
        brand.pack(side="left")
        # The logo art is the icon and the wordmark side by side; they are split so the brand words can sit under the
        # wordmark, next to the icon (design review round 1, item 5)
        logo = IMG.get("art/header-logo")
        two = 2 if IMG.two else 1
        icon, mark = tk.PhotoImage(master=self.root), tk.PhotoImage(master=self.root)
        icon.tk.call(icon, "copy", logo, "-from", 0, 0, 56 * two, logo.height())
        mark.tk.call(mark, "copy", logo, "-from", 60 * two, 10 * two, logo.width(), 38 * two)
        self._logo_parts = (icon, mark)
        tk.Label(brand, image=icon, background=P.header_band, borderwidth=0).grid(row=0, column=0, rowspan=2, sticky="w")
        tk.Label(brand, image=mark, background=P.header_band, borderwidth=0).grid(row=0, column=1, sticky="sw",
                                                                                 padx=(int(8 * k), 0))
        self._brand_words(brand).grid(row=1, column=1, sticky="nw", padx=(int(8 * k), 0), pady=(int(6 * k), 0))
        cog = ttk.Button(band, image=IMG.get("icons/settings"), style="Icon.TButton", command=self._settings_dialog)
        cog.pack(side="right")
        Tooltip(cog, t("gui.settings.title"))
        self.language_picker = LanguagePicker(band, self)
        self.language_picker.pack(side="right", padx=(0, int(12 * k)))
        self.theme_picker = ThemePicker(band, self)
        self.theme_picker.pack(side="right", padx=(0, int(12 * k)))
        stripe = tk.Canvas(self.root, height=max(6, int(6 * k)), highlightthickness=0, borderwidth=0,
                           background=P.header_band)
        stripe.pack(side="top", fill="x")
        self.header_stripe = stripe

        def draw(e=None):
            stripe.delete("all")
            w, h = stripe.winfo_width(), int(stripe.cget("height"))
            for i, colour in enumerate(P.header_stripe):
                stripe.create_rectangle(w * i / 4, 0, w * (i + 1) / 4 + 1, h, fill=colour, outline="")
        stripe.bind("<Configure>", draw)

    # --- Theme -----------------------------------------------------------------------------

    def set_theme(self, choice: str):
        """Udseende: "system", "light" or "night". The window is rebuilt in the new look (the choices made in it are
        kept); during a run the switch waits for the end, so nothing the run shows is lost."""
        self.settings.theme = choice
        self.settings.save()
        name = theme.resolve(choice)
        if name == self.theme_name:
            self.theme_picker.refresh()
            return
        if self._running:
            self._theme_pending = name
            self.theme_picker.refresh()
            self.detail.configure(text=t("gui.theme.after_run"))
            return
        self._apply_theme(name)

    def _brand_words(self, parent) -> tk.Canvas:
        """CUT · DUB · SUB as on the title screen: VT323 20 px with 3 px between the letters, in the text colour, with a
        pink and a cyan 5 px square between the words (tk fonts have no letter spacing, so it is drawn)."""
        k = self.scale
        font = tkfont.Font(family=theme.TERMINAL_FONT, size=-int(20 * k))
        gap, dot = int(3 * k), int(5 * k)
        cyan = "#22e4ff" if P.name == "night" else "#22a6c9"
        height = font.metrics("linespace")
        c = tk.Canvas(parent, height=height, highlightthickness=0, borderwidth=0, background=P.header_band)
        x = 0
        for i, word in enumerate(("CUT", "DUB", "SUB")):
            if i:
                x += int(9 * k)
                y = height // 2 - dot // 2
                c.create_rectangle(x, y, x + dot, y + dot, fill=("#ff2e88", cyan)[i - 1], outline="")
                x += dot + int(12 * k)
            for ch in word:
                c.create_text(x, height // 2, text=ch, font=font, fill=P.text, anchor="w")
                x += font.measure(ch) + gap
            x -= gap
        c.configure(width=x)
        return c

    # --- The app's language ----------------------------------------------------------------

    def set_language(self, code: str):
        """Programmets sprog: the window is rebuilt in the new language, like a theme switch (the choices made in it
        are kept); during a run the switch waits for the end."""
        self.settings.ui_language = code
        self.settings.save()
        if code == i18n.current():
            self.language_picker.refresh(code)
            return
        if self._running:
            self._language_pending = code
            self.language_picker.refresh(code)
            self.detail.configure(text=t("pickers.after_run", lang=code))
            return
        self._apply_language(code)

    def _apply_language(self, code: str):
        self._language_pending = None
        lang, speech, subs = self._lang(), self._speech_lang(), self._subs_lang()
        i18n.set_language(code)
        self.root.title(APP_NAME)
        self._apply_theme(self.theme_name)
        self.lang.set(lang.name)
        self._refresh(lang_changed=True)
        self.speech.set(speech.name if speech else self._speech_items(lang)[0][1])
        self.subs.set(subs.word("choice") if subs else t("gui.setup.subs_none"))
        self._refresh()

    def _apply_pending(self):
        """A theme or language picked during the run that just ended."""
        if self._language_pending:
            if self._theme_pending:
                self.theme_name, self._theme_pending = self._theme_pending, None
                skin.apply(self.root, self.theme_name, self.scale)
                theme.dark_title_bar(self.root, self.theme_name == "night")
            self._apply_language(self._language_pending)
        elif self._theme_pending:
            self._apply_theme(self._theme_pending)

    def _apply_theme(self, name: str):
        self._theme_pending = None
        self.theme_name = name
        view = getattr(self, "view", "setup")
        self.header.destroy()
        self.header_stripe.destroy()
        self.main.destroy()
        skin.apply(self.root, name, self.scale)
        self.bg = P.window
        theme.dark_title_bar(self.root, name == "night")
        self._build()
        self._refresh()
        for path in self.created:
            self.files.insert("end", path.name)
        self._show(view)
        if view == "done" and self._last_result:
            self._show_result(*self._last_result, replay=True)
        skin.skin_cards(self.main)

    def _build_setup(self, parent) -> ttk.Frame:
        v = ttk.Frame(parent)
        v.columnconfigure(0, weight=1)

        g1 = skin.Card(v, text=t("gui.setup.group_video"), padding=12)
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
        self.v_btn = skin.IconButton(card, "choose-video", text=t("gui.setup.pick_video"), command=self._pick_video)
        self.v_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        ttk.Label(g1, text=t("gui.setup.spoken_language")).grid(row=1, column=0, sticky="w", pady=(14, 0))
        c_lang = ttk.Combobox(g1, textvariable=self.lang, values=[l.name for l in LANGUAGES], state="readonly", width=14)
        c_lang.grid(row=1, column=1, sticky="w", padx=10, pady=(14, 0))
        c_lang.bind("<<ComboboxSelected>>", lambda e: self._refresh(lang_changed=True))

        g2 = skin.Card(v, text=t("gui.setup.group_output"), padding=12)
        g2.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        g2.columnconfigure(1, weight=1)
        self.cb_cut = ttk.Checkbutton(g2, variable=self.cut, command=self._cut_changed,
                                      text=t("gui.setup.cut_first"))
        self.cb_cut.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(g2, text=t("gui.setup.speech")).grid(row=1, column=0, sticky="w")
        speech_row = ttk.Frame(g2)
        speech_row.grid(row=1, column=1, sticky="w", padx=8, pady=3)
        self.c_speech = ttk.Combobox(speech_row, textvariable=self.speech, state="readonly", width=22)
        self.c_speech.pack(side="left")
        # Lyt til stemmen: shown only when a dub language is chosen (see _refresh)
        self.voice_btn = voice_preview.PreviewButton(speech_row, self._speech_lang, lambda: self.settings.voice_mode,
                                                     self._get_voice_models)
        self.c_speech.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        ttk.Label(g2, text=t("gui.setup.subtitles")).grid(row=2, column=0, sticky="w")
        self.c_subs = ttk.Combobox(g2, textvariable=self.subs, state="readonly", width=22,
                                   values=[label for _, label in self._subs_items()])
        self.c_subs.grid(row=2, column=1, sticky="w", padx=8, pady=3)
        self.c_subs.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        # Short checkbox labels; what happens otherwise is on a wrapped hint line under each (lined up with the text)
        indent = int(30 * self.scale)   # under the label: indicator 16 + gap 12 + the check button's 2 px padding
        self.cb_redo = ttk.Checkbutton(g2, variable=self.redo, command=self._refresh, text=t("gui.setup.redo"))
        self.cb_redo.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.redo_hint = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.redo_hint.grid(row=4, column=0, columnspan=2, sticky="ew", padx=(indent, 0))
        self._wrap(self.redo_hint, g2, 40 + indent)
        self.cb_recut = ttk.Checkbutton(g2, variable=self.recut, command=self._refresh, text=t("gui.setup.recut"))
        self.cb_recut.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.recut_hint = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.recut_hint.grid(row=6, column=0, columnspan=2, sticky="ew", padx=(indent, 0))
        self._wrap(self.recut_hint, g2, 40 + indent)
        self._recut_forced = False   # ticked by the program because the video is listened to again
        # What the user gets, under a dashed line: a small DU FÅR, then the file name (design review round 2, item 10)
        k = self.scale
        self.plan_row = ttk.Frame(g2)
        self.plan_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(int(14 * k), 0))
        self.plan_row.columnconfigure(1, weight=1)
        line = tk.Canvas(self.plan_row, height=max(2, int(2 * k)), highlightthickness=0, borderwidth=0,
                         background=P.card)
        line.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(14 * k)))
        line.bind("<Configure>", lambda e: (line.delete("all"), line.create_line(
            0, 1, e.width, 1, fill=P.step_pending_icon, width=max(2, int(2 * k)), dash=(4, 4))))
        ttk.Label(self.plan_row, text=t("gui.plan.you_get_label"), font=("Segoe UI Semibold", -int(13 * k)),
                  foreground=P.heading if P.name == "light" else P.accent).grid(row=1, column=0, sticky="nw",
                                                                                  padx=(0, int(10 * k)))
        self.plan_result = ttk.Label(self.plan_row, text="", font="SunValleyBodyStrongFont", justify="left")
        self.plan_result.grid(row=1, column=1, sticky="ew")
        self._wrap(self.plan_result, g2, int(120 * k))
        self.plan_steps = ttk.Label(g2, text="", style="Hint.TLabel", justify="left")
        self.plan_steps.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(int(6 * k), 0))
        self._wrap(self.plan_steps, g2, 40)

        g3 = ttk.Frame(v, padding=(0, 6))
        g3.grid(row=2, column=0, sticky="ew")
        g3.columnconfigure(0, weight=1)
        self.start_btn = skin.IconButton(g3, "play", text=t("gui.setup.start"), style="Main.Accent.TButton", command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="w")
        hint = ttk.Label(g3, text=t("gui.setup.start_hint"), style="Hint.TLabel", justify="left")
        hint.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._wrap(hint, g3, 10)
        return v

    def _build_work(self, parent) -> ttk.Frame:
        v = ttk.Frame(parent)
        v.columnconfigure(0, weight=1)
        ttk.Label(v, text=t("gui.work.working_on"), style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        self.w_video = ttk.Label(v, text="", style="Section.TLabel", foreground=P.heading)
        self.w_video.grid(row=1, column=0, sticky="ew")
        self._wrap(self.w_video, v)
        self.w_result = ttk.Label(v, text="", style="Hint.TLabel")
        self.w_result.grid(row=2, column=0, sticky="ew", pady=(2, 12))
        self._wrap(self.w_result, v)

        box = skin.Card(v, text=t("gui.work.steps"), padding=14, style="Blue.TLabelframe")
        box.grid(row=3, column=0, sticky="ew")
        box.columnconfigure(2, weight=1)
        self.steps_box = box

        prog = ttk.Frame(v, padding=(0, 16, 0, 0))
        prog.grid(row=4, column=0, sticky="ew")
        prog.columnconfigure(1, weight=1)
        self.w_pct = ttk.Label(prog, text="0%", style="Pct.TLabel", width=5)
        self.w_pct.grid(row=0, column=0, rowspan=2, sticky="w")
        self.bar = SegmentBar(prog, self.scale)
        self.bar.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.status = ttk.Label(prog, text="")
        self.status.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))

        # The "your turn" band (frames/callout): DIN TUR in the pixel font, the sentence, and an arrow to the Claude card
        k = self.scale
        self.callout = ttk.Frame(v, style="Callout.TFrame", padding=(int(16 * k), int(10 * k), int(18 * k), int(12 * k)))
        self.callout.grid(row=5, column=0, sticky="ew", pady=(20, 0))
        self.callout.columnconfigure(2, weight=1)
        tk.Label(self.callout, image=IMG.get("icons/play-selected"), background=P.callout_bg,
                 borderwidth=0).grid(row=0, column=0, sticky="w", padx=(0, int(6 * k)))
        tk.Label(self.callout, text=t("gui.work.callout_tag"), background=P.callout_bg, foreground=P.callout_text,
                 font=(theme.PIXEL_FONT, -int(13 * k)), borderwidth=0, padx=0, pady=0).grid(row=0, column=1, sticky="w")
        hint = tk.Label(self.callout, text=t("gui.work.your_turn_callout"), background=P.callout_bg,
                        foreground=P.callout_text, font="SunValleyBodyStrongFont", justify="left", anchor="w",
                        borderwidth=0, padx=0, pady=0)
        hint.grid(row=0, column=2, sticky="w", padx=(int(12 * k), 0))
        self.callout.bind("<Configure>", lambda e: hint.configure(wraplength=max(150, e.width - int(260 * k))))
        self._arrow_img = tk.PhotoImage(master=self.root, file=str(theme.assets_dir() / "ui" / theme.THEMES[P.name]
                                                                     / "icons" / "arrow-right-selected@2x.png"))
        self.arrow = tk.Label(self.callout, image=self._arrow_img, background=P.callout_bg, borderwidth=0)
        self.arrow.grid(row=0, column=3, sticky="e", padx=(10, 0))
        self.callout.grid_remove()

        self.detail = ttk.Label(v, text="", style="Hint.TLabel", justify="left")
        self.detail.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        self._wrap(self.detail, v)

        btns = ttk.Frame(v)
        btns.grid(row=7, column=0, sticky="w", pady=(18, 0))
        self.pause_btn = skin.IconButton(btns, "pause", text=t("gui.work.pause"), command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        self.cancel_btn = skin.IconButton(btns, "cancel", text=t("gui.cancel"), command=self._cancel)
        self.cancel_btn.pack(side="left", padx=10)
        self.pause_hint = ttk.Label(btns, text=t("gui.work.pause_hint"),
                                    style="Hint.TLabel")
        self.pause_hint.pack(side="left", padx=6)
        return v

    class _FileRows(tk.Frame):
        """The done view's "Alle filer fra denne kørsel" (design review round 2, item 20): 36 px rows with the
        file-subtitles or file-video icon, the name and the type on the right in hint; the row under the mouse or
        with keyboard focus in picker_hover_bg. Double-click or Enter opens the file. Keeps the bits of the old
        Listbox's interface the rest of the app uses (insert, delete, get, curselection)."""

        def __init__(self, parent, app: "App"):
            super().__init__(parent, background=P.card, borderwidth=0, highlightthickness=0)
            self.app = app
            self.columnconfigure(0, weight=1)
            self.rows: list[dict] = []
            self._hover: tk.Frame | None = None
            self._current = -1

        def insert(self, _index, name: str):
            k = self.app.scale
            i = len(self.rows)
            srt = name.lower().endswith(".srt")
            row = tk.Frame(self, background=P.card, height=int(36 * k), takefocus=1, borderwidth=0,
                           highlightthickness=0)
            row.grid(row=i, column=0, sticky="ew")
            row.grid_propagate(False)
            row.columnconfigure(1, weight=1)
            row.rowconfigure(0, weight=1)
            parts = [tk.Label(row, image=IMG.get("icons/file-subtitles" if srt else "icons/file-video"),
                              background=P.card, borderwidth=0),
                     tk.Label(row, text=name, background=P.card, foreground=P.text, font="SunValleyBodyFont",
                              anchor="w", borderwidth=0, padx=0, pady=0),
                     tk.Label(row, text=t("gui.done.type_subtitles" if srt else "gui.done.type_video"),
                              background=P.card, foreground=P.hint, font=("Segoe UI", -int(13 * k)),
                              borderwidth=0, padx=0, pady=0)]
            parts[0].grid(row=0, column=0, padx=(int(10 * k), int(12 * k)))
            parts[1].grid(row=0, column=1, sticky="ew")
            parts[2].grid(row=0, column=2, padx=(int(12 * k), int(10 * k)))
            self.rows.append({"frame": row, "parts": parts, "name": name})
            for w in [row] + parts:
                w.bind("<Enter>", lambda e, r=row: self._set_hover(r))
                w.bind("<Leave>", lambda e, r=row: self._left(r))
                w.bind("<Button-1>", lambda e, n=i: self._focus(n))
                w.bind("<Double-Button-1>", lambda e, n=i: self._open(n))
            row.bind("<Return>", lambda e, n=i: self._open(n))
            row.bind("<KP_Enter>", lambda e, n=i: self._open(n))
            row.bind("<Up>", lambda e, n=i: self._focus(n - 1))
            row.bind("<Down>", lambda e, n=i: self._focus(n + 1))
            row.bind("<FocusIn>", lambda e, n=i: self._focused(n))
            row.bind("<FocusOut>", lambda e: self._paint())

        def delete(self, _first, _last=None):
            for r in self.rows:
                r["frame"].destroy()
            self.rows, self._hover, self._current = [], None, -1

        def get(self, _first, _last=None):
            return tuple(r["name"] for r in self.rows)

        def size(self) -> int:
            return len(self.rows)

        def curselection(self):
            return (self._current,) if 0 <= self._current < len(self.rows) else ()

        def _focus(self, n: int):
            if 0 <= n < len(self.rows):
                self.rows[n]["frame"].focus_set()

        def _focused(self, n: int):
            self._current = n
            self._paint()

        def _open(self, n: int):
            self._current = n
            self.app._open_selected()

        def _set_hover(self, row):
            self._hover = row
            self._paint()

        def _left(self, row):
            # Leave also fires when the pointer moves onto one of the row's own labels
            try:
                w = self.winfo_containing(*self.winfo_pointerxy())
            except (tk.TclError, KeyError):
                w = None
            if w is None or not str(w).startswith(str(row)):
                if self._hover is row:
                    self._hover = None
                self._paint()

        def _paint(self):
            try:
                focus = self.focus_get()
            except (tk.TclError, KeyError):
                focus = None
            for r in self.rows:
                lit = r["frame"] is self._hover or r["frame"] is focus
                for w in [r["frame"]] + r["parts"]:
                    w.configure(background=P.picker_hover_bg if lit else P.card)

    def _build_done(self, parent) -> ttk.Frame:
        """The done view (design review round 2, items 19-22): on the left the banner (STAGE CLEAR!, or CONTINUE?
        after an error or a stop), the result card and the run's files; on the right (560 of 1600 px) "Hvad nu?"
        and the run's steps in a compact list. The view takes the whole width (_show hides the Claude card)."""
        k = self.scale
        style = ttk.Style(self.root)
        main_pad = style.lookup("Main.Accent.TButton", "padding")
        # Full-width stacked buttons with the icon and text on the left; the secondary ones as tall as the main one
        for st, extra in (("Wide.Main.Accent.TButton", {}), ("Wide.Tall.TButton", {"padding": main_pad}),
                          ("Tall.TButton", {"padding": main_pad})):
            style.configure(st, **({"anchor": "w"} if st.startswith("Wide") else {}), **extra)
            style.configure(f"Card.{st}", background=P.card)
        v = ttk.Frame(parent)
        v.columnconfigure(0, weight=96, uniform="done")
        v.columnconfigure(1, weight=56, uniform="done")
        v.rowconfigure(0, weight=1)
        left, right = ttk.Frame(v), ttk.Frame(v)
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew", padx=(int(32 * k), 0))
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        card_pad = (int(18 * k), int(14 * k), int(22 * k), int(18 * k))

        self.d_banner = tk.Label(left, image=IMG.get("art/done-banner"), background=P.window, borderwidth=0)
        self.d_banner.grid(row=0, column=0, sticky="w", pady=(0, int(24 * k)))

        # The result card: the title (with the step-error icon after an error), the file, where it is saved, the
        # run's numbers, a message (learned words, what failed, the error) and Afspil / Vis i mappe / Åbn logfil
        self.d_main = skin.Card(left, padding=card_pad)
        self.d_main.grid(row=1, column=0, sticky="ew")
        self.d_main.columnconfigure(0, weight=1)
        head = ttk.Frame(self.d_main)
        head.grid(row=0, column=0, sticky="ew")
        self.d_icon = tk.Label(head, image=IMG.get("icons/step-error"), background=P.card, borderwidth=0)
        self.d_icon.grid(row=0, column=0, sticky="w", padx=(0, int(12 * k)))
        self.d_title = tk.Label(head, text="", background=P.card, foreground=P.heading, borderwidth=0, padx=0, pady=0,
                                font=("Segoe UI Semibold", -int(22 * k)), anchor="w", justify="left")
        self.d_title.grid(row=0, column=1, sticky="w")
        self.d_file = tk.Label(self.d_main, text="", background=P.card, foreground=P.text, borderwidth=0, padx=0,
                               font="SunValleyBodyStrongFont", anchor="w", justify="left")
        self.d_file.grid(row=1, column=0, sticky="ew", pady=(int(10 * k), 0))
        self._wrap(self.d_file, self.d_main, int(50 * k))
        self.d_where = ttk.Label(self.d_main, text="", style="Hint.TLabel", justify="left")
        self.d_where.grid(row=2, column=0, sticky="ew", pady=(int(2 * k), 0))
        self._wrap(self.d_where, self.d_main, int(50 * k))
        self.d_stats = ttk.Frame(self.d_main)
        self.d_stats.grid(row=3, column=0, sticky="w", pady=(int(14 * k), 0))
        self.d_msg = tk.Label(self.d_main, text="", background=P.card, foreground=P.text, borderwidth=0, padx=0,
                              font="SunValleyBodyFont", anchor="w", justify="left")
        self.d_msg.grid(row=4, column=0, sticky="ew", pady=(int(6 * k), 0))
        self._wrap(self.d_msg, self.d_main, int(50 * k))
        self.d_error = ttk.Label(self.d_main, text="", style="Hint.TLabel", justify="left")
        self.d_error.grid(row=5, column=0, sticky="ew", pady=(int(4 * k), 0))
        self._wrap(self.d_error, self.d_main, int(50 * k))
        self.d_extra = ttk.Label(self.d_main, text="", style="Hint.TLabel", justify="left")
        self.d_extra.grid(row=6, column=0, sticky="ew", pady=(int(8 * k), 0))
        self._wrap(self.d_extra, self.d_main, int(50 * k))
        self.d_acts = ttk.Frame(self.d_main)
        self.d_acts.grid(row=7, column=0, sticky="w", pady=(int(16 * k), 0))
        self.d_play = skin.IconButton(self.d_acts, "play", text=t("gui.done.play"), style="Main.Accent.TButton",
                                      command=lambda: self._open(self.d_target))
        self.d_play.pack(side="left", padx=(0, int(14 * k)))
        self.d_folder = skin.IconButton(self.d_acts, "show-in-folder", text=t("gui.show_in_folder"),
                                        style="Tall.TButton", command=lambda: self._show_in_folder(self.d_target))
        self.d_folder.pack(side="left", padx=(0, int(14 * k)))
        self.d_log = ttk.Button(self.d_acts, text=t("gui.open_log"), command=lambda: self._open(log_file()))
        self.d_log.pack(side="left")
        self.d_target: Path | None = None

        self.d_files = g = skin.Card(left, text=t("gui.done.group_files"), padding=card_pad)
        g.grid(row=2, column=0, sticky="ew", pady=(int(24 * k), 0))
        g.columnconfigure(0, weight=1)
        self.files = self._FileRows(g, self)
        self.files.grid(row=0, column=0, sticky="ew")
        hint = ttk.Label(g, text=t("gui.done.files_hint"), style="Hint.TLabel", justify="left")
        hint.grid(row=1, column=0, sticky="ew", pady=(int(10 * k), 0))
        self._wrap(hint, g, int(50 * k))

        # Hvad nu? Full-width 48 px buttons: the main one (another version, or Prøv igen / Fortsæt), Vælg en ny video
        nxt = skin.Card(right, text=t("gui.done.group_next"), padding=card_pad)
        nxt.grid(row=0, column=0, sticky="ew")
        nxt.columnconfigure(0, weight=1)
        self.d_next_hint = ttk.Label(nxt, text="", style="Hint.TLabel", justify="left")
        self.d_next_hint.grid(row=0, column=0, sticky="ew", pady=(0, int(14 * k)))
        self._wrap(self.d_next_hint, nxt, int(50 * k))
        self.d_again = skin.IconButton(nxt, "retry", text=t("gui.done.try_again"), style="Wide.Main.Accent.TButton",
                                       command=self._start)
        self.d_again.grid(row=1, column=0, sticky="ew")
        self.d_same = skin.IconButton(nxt, "copy", text=t("gui.done.same_video"), style="Wide.Main.Accent.TButton",
                                      command=self._same_video)
        self.d_same.grid(row=2, column=0, sticky="ew")
        self.d_new = skin.IconButton(nxt, "choose-video", text=t("gui.done.new_video"), style="Wide.Tall.TButton",
                                     command=self._new_video)
        self.d_new.grid(row=3, column=0, sticky="ew", pady=(int(14 * k), 0))

        # The run's steps, compact: 34 px rows, no connectors (filled in by _show_result)
        self.d_steps = skin.Card(right, text=t("gui.done.all_steps"), padding=card_pad)
        self.d_steps.grid(row=1, column=0, sticky="ew", pady=(int(24 * k), 0))
        self.d_steps.columnconfigure(2, weight=1)
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
        return lang_by_name(self.lang.get()) or LANG_BY_CODE["da"]

    def _speech_items(self, l: Language) -> list[tuple[Language | None, str]]:
        """The speech combobox: (None = keep the original speech, its label), then the other languages."""
        return [(None, t("gui.setup.speech_original", singular=l.singular))] + [(x, x.name) for x in LANGUAGES if x != l]

    def _show_voice_btn(self):
        """The play button next to Ny tale: only with a dub language; a change of language stops the sample."""
        sp = self._speech_lang()
        if sp != getattr(self, "_voice_btn_lang", None):
            self.voice_btn.stop()
        self._voice_btn_lang = sp
        if sp and not self.voice_btn.winfo_manager():
            self.voice_btn.pack(side="left", padx=(int(8 * self.scale), 0))
        elif not sp:
            self.voice_btn.pack_forget()

    def _get_voice_models(self, missing, parent=None) -> bool:
        """Fetch the voices a sample needs (the same dialog as before a run)."""
        return bool(ModelDialog(parent or self.root, missing, scale=self.scale).result)

    def _subs_items(self) -> list[tuple[Language | None, str]]:
        """The subtitles combobox: (None = no subtitles, its label), then every language."""
        return [(None, t("gui.setup.subs_none"))] + [(x, x.word("choice")) for x in LANGUAGES]

    def _speech_lang(self) -> Language | None:
        """None = keep the original speech. Anything that is not a language (the "Original (...)" entry, also in
        another app language) means the original."""
        text = self.speech.get()
        for x, label in self._speech_items(self._lang()):
            if label == text:
                return x
        return lang_by_name(text)

    def _subs_lang(self) -> Language | None:
        """None = no subtitles ("Ingen", also in another app language)."""
        text = self.subs.get()
        for x, label in self._subs_items():
            if label == text:
                return x
        from .i18n import CODES
        return next((x for x in LANGUAGES for c in CODES if x.word("choice", c) == text), None) or lang_by_name(text)

    def _refresh(self, lang_changed: bool = False):
        l = self._lang()
        self.lang.set(l.name)
        sp, su, had_subs = self._speech_lang(), self._subs_lang(), bool(self.subs.get())
        items = self._speech_items(l)
        self.c_speech.configure(values=[label for _, label in items])
        if lang_changed or not self.speech.get() or sp is None or sp == l:
            self.speech.set(items[0][1])
        else:
            self.speech.set(sp.name)
        self._show_voice_btn()
        self.c_subs.configure(values=[label for _, label in self._subs_items()])
        if not had_subs or (lang_changed and su is not None):
            su = l
        self.subs.set(su.word("choice") if su else t("gui.setup.subs_none"))
        v = self._video()
        mode, _ = self._cut_mode(v)
        show_recut = bool(v and v.is_file() and self.cut.get() and textstate.cut_status(cut_path(v), v) == "ok")
        if mode == "reuse":
            v = cut_path(v)
        has_srt = bool(v and v.is_file() and textstate.status(srt_path(v, l), v) == "ok")
        self.redo_hint.configure(text=t("gui.setup.redo_hint", plural=l.plural))
        for w in (self.cb_redo, self.redo_hint):
            w.grid() if has_srt else w.grid_remove()
        # Listening again always cuts again (see _cut_mode): show that instead of an option that does nothing
        if show_recut and self.redo.get():
            if not self.recut.get():
                self.recut.set(True)
                self._recut_forced = True
            self.cb_recut.state(["disabled"])
            self.recut_hint.configure(text=t("gui.setup.recut_forced"))
        else:
            if self._recut_forced:
                self.recut.set(False)
                self._recut_forced = False
            self.cb_recut.state(["!disabled"])
            self.recut_hint.configure(text=t("gui.setup.recut_hint"))
        for w in (self.cb_recut, self.recut_hint):
            w.grid() if show_recut else w.grid_remove()
        result, rest = self._plan_text()
        if result:
            self.plan_result.configure(text=result)
            self.plan_row.grid()
            self.plan_steps.configure(text=rest)
        else:
            self.plan_row.grid_remove()
            self.plan_steps.configure(text=rest)
        v = self._video()
        self.start_btn.configure(state="normal" if v and v.is_file() else "disabled")
        if not v:
            self.v_name.configure(text=t("gui.setup.no_video"), foreground=P.hint)
            self.v_dir.configure(text=t("gui.setup.pick_video_hint"), foreground="")
            self.v_btn.configure(text=t("gui.setup.pick_video"), style="Accent.TButton")
        else:
            self.v_name.configure(text=v.name, foreground="")
            self.v_dir.configure(text=str(v.parent) if v.is_file() else t("gui.setup.video_gone"),
                                 foreground="" if v.is_file() else P.error)
            self.v_btn.configure(text=t("gui.setup.change_video"), style="TButton")

    def _claude_plan(self, v: Path, lang: Language, transcribe: bool, targets) -> tuple[bool, list[Language]]:
        """(check the spoken-language text?, languages to translate to) for the Claude round.

        Claude checks the text once (after that it is reused, also if the user edited it by hand), and a
        translation is reused when it matches the spoken-language subtitles' time codes."""
        orig = srt_path(v, lang)
        check = transcribe or not textstate.is_checked(orig)
        orig_cues = None
        if not transcribe and orig.exists():
            try:
                orig_cues = read_srt(orig)
            except Exception:
                orig_cues = None
        needed = []
        for target in dict.fromkeys(x for x in targets if x and x != lang):
            existing = find_srt(v, target)
            ok = False
            if orig_cues is not None and existing:
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
        return srt_path(v, l)

    def _cut_mode(self, v: Path | None) -> tuple[str | None, Path | None]:
        """(None | "new" | "reuse", the cut video's path). "reuse": the cut video made earlier from this video
        is used again (for another language), unless the user asks to cut again or to listen again."""
        if not v or not v.is_file() or not self.cut.get():
            return None, None
        edited = cut_path(v)
        if not self.recut.get() and not self.redo.get() and textstate.cut_status(edited, v) == "ok":
            return "reuse", edited
        return "new", edited

    def _cut_changed(self):
        self.settings.cut_first = bool(self.cut.get())
        self.settings.save()
        self._refresh()

    def _plan_text(self) -> tuple[str, str]:
        """(the "Du får: ..." line or "" when there is nothing to make, the lines under it)."""
        v = self._video()
        if not v:
            return "", t("gui.setup.pick_video_hint")
        if not v.is_file():
            return "", t("gui.plan.video_missing")
        l, sp, su = self._lang(), self._speech_lang(), self._subs_lang()
        mode, edited = self._cut_mode(v)
        if mode == "reuse":
            if not sp and not su:
                return "", t("gui.plan.cut_exists", name=edited.name, recut=t("gui.setup.recut"))
            v = edited
        base = edited if mode else v
        if mode == "new" and not sp and not su:
            result = t("gui.plan.you_get_cut", name=edited.name, plural=l.plural)
        elif sp or su:
            result = t("gui.plan.you_get", name=self._result_name(base, l, sp, su).name)
        else:
            result = t("gui.plan.you_get_text", name=self._result_name(base, l, sp, su).name)
        lines = []
        if mode == "reuse":
            lines.append(t("gui.plan.cut_reused", name=edited.name))
        state = textstate.status(srt_path(v, l), v)
        transcribe = self.redo.get() or state != "ok"
        if state == "changed" and not self.redo.get():
            lines.append(t("gui.plan.video_changed", plural=l.plural))
        elif state == "old" and not self.redo.get():
            lines.append(t("gui.plan.old_version", plural=l.plural))
        elif transcribe:
            lines.append(t("gui.plan.transcribe", plural=l.plural))
        check, need = self._claude_plan(v, l, transcribe, [sp, su])
        if mode == "new" and not check and not need:
            check = True
        todo = ([t("gui.plan.task_check", plural=l.plural)] if check else []) + \
               ([t("gui.plan.task_translate", langs=join_and([x.singular for x in need]))] if need else []) + \
               ([t("gui.plan.task_cut")] if mode == "new" else [])
        if todo:
            lines.append(t("gui.plan.claude", tasks=join_and(todo)))
        if mode == "new":
            lines.append(t("gui.plan.cut_review"))
        return result, "\n".join(lines)

    def _pick_video(self):
        p = filedialog.askopenfilename(parent=self.root, title=t("gui.setup.pick_video_title"), filetypes=video_types(),
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
                boxes.showwarning(APP_NAME, t("gui.validate.pick_first"), parent=self._owner())
            return None
        lang = self._lang()
        speech, subs = self._speech_lang(), self._subs_lang()
        mode, edited = self._cut_mode(v)
        original = v
        if mode == "reuse":
            if not speech and not subs:
                self._auto_reason = f"Den klippede video findes allerede ({edited.name}). Vælg tale eller undertekster."
                if not auto:
                    boxes.showinfo(APP_NAME, t("gui.validate.cut_exists", name=edited.name, recut=t("gui.setup.recut")),
                                   parent=self._owner())
                return None
            v = edited
        orig_srt = srt_path(v, lang)
        job = {"video": v, "original": original, "lang": lang, "speech": speech, "subs": subs, "outputs": [],
               "translate": [], "cut": mode == "new", "edited": edited, "cut_reused": mode == "reuse"}
        job["transcribe"] = self.redo.get() or textstate.status(orig_srt, v) != "ok"
        if not speech and not subs and not job["transcribe"] and not job["cut"]:
            self._auto_reason = f"De {lang.word('plural', 'da')} undertekster findes allerede ({orig_srt.name}). Vælg tale eller undertekster."
            if auto:
                return None
            boxes.showinfo(APP_NAME, t("gui.validate.srt_exists", plural=lang.plural, name=orig_srt.name,
                                       redo=t("gui.setup.redo")), parent=self._owner())
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
        if exists and not auto and not boxes.askyesno(APP_NAME, t("gui.validate.overwrite",
                                                                  files="\n".join(o.name for o in exists)),
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
                if not boxes.askyesno(APP_NAME, t("gui.validate.low_memory", gb=f"{mem:.1f}"), parent=self._owner()):
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
            names.append(t("gui.step.extract"))
        if job["transcribe"]:
            names.append(t("gui.step.transcribe"))
        claude_at = -1
        self.review_at = -1
        if job["check"] or job["translate"]:
            claude_at = len(names)
            what = ([t("gui.step.task_check")] if job["check"] else []) + \
                   ([t("gui.step.task_translate", langs=join_and([x.singular for x in job["translate"]]))]
                    if job["translate"] else []) + \
                   ([t("gui.step.task_cut")] if job["cut"] else [])
            names.append(t("gui.step.claude_chat" if job.get("mcp") else "gui.step.claude_turn", tasks=join_and(what)))
        if job["cut"]:
            self.review_at = len(names)
            names += [t("gui.step.review"), t("gui.step.cut")]
        self.manual_steps = [i for i in (claude_at, self.review_at) if i >= 0]
        if job["speech"]:
            names += [t("gui.step.voice_in", singular=job["speech"].singular), t("gui.step.dub")]
        elif job["subs"]:
            names += [t("gui.step.burn")]
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
            if child is not self.steps_box.title:   # the card's own title
                child.destroy()
        self.step_rows = []
        # A level map: the steps 40 px apart, joined by a dotted line between their icons (design review round 1,
        # items 15-16); the level numbers in a fixed 34 px column, centred on the 24 px icon
        k = self.scale
        self.steps_box.columnconfigure(1, minsize=int(34 * k))
        icon_w = IMG.get("icons/step-pending").width()
        for i, name in enumerate(names):
            if i:
                link = tk.Canvas(self.steps_box, width=icon_w, height=int(16 * k), highlightthickness=0,
                                 borderwidth=0, background=P.card)
                link.grid(row=2 * i - 1, column=0)
                link.create_line(icon_w // 2, 0, icon_w // 2, int(16 * k), fill=P.step_pending_icon,
                                 width=max(2, int(2 * k)), dash=(4, 4))
            icon = tk.Label(self.steps_box, background=P.card, borderwidth=0)
            icon.grid(row=2 * i, column=0, sticky="w")
            tk.Label(self.steps_box, text=f"1-{i + 1}", foreground=P.hint, background=P.card, borderwidth=0,
                     font=(theme.PIXEL_FONT, -int(10 * k)), pady=0).grid(row=2 * i, column=1, sticky="w",
                                                                          padx=(int(10 * k), 0), pady=(int(2 * k), 0))
            text = tk.Label(self.steps_box, text=name, foreground=P.step_pending, background=P.card,
                            font="SunValleyBodyFont", anchor="w", padx=0, pady=0, borderwidth=0)
            text.grid(row=2 * i, column=2, sticky="w", padx=(int(6 * k), 0))
            row = {"icon": icon, "text": text, "state": "todo"}
            self.step_rows.append(row)
            self._draw_icon(row)
        self.active_step = -1
        self.w_video.configure(text=job["video"].name)
        self.w_result.configure(text=t("gui.work.you_get", name=job["result"].name))
        self.bar.configure(value=0)
        self.w_pct.configure(text="0%")
        self.status.configure(text=t("gui.work.starting"))
        self.detail.configure(text="")
        self.callout.grid_remove()
        self.pause_btn.icon = "pause"
        self.pause_btn.configure(state="normal", text=t("gui.work.pause"), style="TButton")
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
            self.status.configure(text=t("gui.work.claude_stops"))
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
            # A language code, or (requests before 2.1) its Danish name
            self.lang.set((lang_by_name(req.get("lang")) or LANG_BY_CODE["da"]).name)
            self._refresh(lang_changed=True)
            speech = lang_by_name(req.get("speech"))
            self.speech.set(speech.name if speech else self._speech_items(self._lang())[0][1])
            subs = lang_by_name(req.get("subs"))
            self.subs.set(subs.word("choice") if subs else t("gui.setup.subs_none"))
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
            colour = {"done": P.step_done, "active": P.step_active, "turn": P.your_turn_title}.get(state, P.step_pending)
            strong = state in ("active", "turn")
            row["text"].configure(foreground=colour, font="SunValleyBodyStrongFont" if strong else "SunValleyBodyFont")
            self._draw_icon(row)

    def _draw_icon(self, row: dict, frame: int = 0):
        """The step's pixel icon: pending, done, error, the user's turn (blinking) or working (animated)."""
        name = {"todo": "step-pending", "done": "step-done", "error": "step-error",
                "turn": f"step-your-turn-{frame % 2}"}.get(row["state"], f"step-active-{frame % 6}")
        row["icon"].configure(image=IMG.get(f"icons/{name}"))

    def _animate(self):
        """Smooth spinner on the active step, and the pulsing border on the Claude panel."""
        self._spin += 1
        if self._running and 0 <= self.active_step < len(self.step_rows):
            row = self.step_rows[self.active_step]
            if row["state"] == "active" and not (self.runner and self.runner.paused) and self._spin % 3 == 0:
                self._draw_icon(row, frame=self._spin // 3)   # about 120 ms a frame
            elif row["state"] == "turn" and self._spin % 12 == 0:
                self._draw_icon(row, frame=self._spin // 12)   # blinks about every 500 ms
        if self._pulsing:
            if self._spin % 11 == 0:
                self.claude_box.configure(style="TurnA.TLabelframe" if (self._spin // 11) % 2 == 0 else "TurnB.TLabelframe")
            self._draw_arrow()
        self.root.after(40, self._animate)

    def _draw_arrow(self):
        """The arrow in the band nudges towards the Claude card (about every 440 ms)."""
        k = self.scale
        self.arrow.grid_configure(padx=(int((10 + (4 if (self._spin // 11) % 2 else 0)) * k), 0))

    def _busy(self) -> bool:
        return self._running

    def _on_close(self):
        if self._busy():
            if not boxes.askyesno(APP_NAME, t("gui.work.close_running", pause=t("gui.work.pause")),
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
            self.pause_btn.icon = "pause"
            self.pause_btn.configure(text=t("gui.work.pause"))
            self.status.configure(text=self._status_before_pause)
        else:
            r.pause()
            self.pause_btn.icon = "play"
            self.pause_btn.configure(text=t("gui.work.resume"), style="Accent.TButton")
            self._status_before_pause = self.status.cget("text")
            self.status.configure(text=t("gui.work.paused", resume=t("gui.work.resume")))
        if not r.paused:
            self.pause_btn.configure(style="TButton")
        self._jstatus(state="paused" if r.paused else "running")

    def _user_turn(self, active: bool):
        """While the run waits for the user (the Claude round, the cut review) the percentage is grey, and there is
        no Pause: nothing runs then, and a paused run would not notice her answer. Pause comes back afterwards,
        unless the run has stopped meanwhile."""
        r = self.runner
        self.w_pct.configure(foreground=P.hint if active else P.step_active)
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
            if not boxes.askyesno(APP_NAME, t("gui.work.cancel_ask", pause=t("gui.work.pause")), parent=self._owner()):
                return
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")
            self.status.configure(text=t("gui.work.cancelling"))
            self.runner.cancel()

    # --- Worker -------------------------------------------------------------------------

    def _work(self, job: dict, runner: Runner):
        post = self.events.put
        v: Path = job["video"]
        lang: Language = job["lang"]
        speech, subs = job["speech"], job["subs"]
        cut = job["cut"]
        # The step names are shown in the status line and are how prog.start finds a step: read them once, so a
        # switch of the app's language during the run cannot make them differ
        s_extract, s_transcribe, s_cut = t("gui.step.extract"), t("gui.step.transcribe"), t("gui.step.cut")
        s_voice, s_dub, s_burn = t("gui.step.voice"), t("gui.step.dub"), t("gui.step.burn")
        steps = []
        if job["transcribe"] or cut:
            steps.append((s_extract, 1))
        if job["transcribe"]:
            steps.append((s_transcribe, 20))
        if cut:
            steps.append((s_cut, 6))
        if speech:
            steps += [(s_voice, 4), (s_dub, 6 if subs else 1)]
        elif subs:
            steps += [(s_burn, 6)]
        self._progress_names = [n for n, _ in steps]
        prog = engine.Progress(lambda o, s, d: post(("progress", o, s, d, prog.step)), steps, runner)
        work = Path(tempfile.mkdtemp(prefix="sdl-"))
        log.info("JOB video=%s lang=%s speech=%s subs=%s transcribe=%s check=%s translate=%s cut=%s reused_cut=%s", v,
                 lang.code, speech and speech.code, subs and subs.code, job["transcribe"], job["check"],
                 [t.code for t in job["translate"]], cut, job["cut_reused"])
        orig_srt = srt_path(v, lang)

        def srt_of(l: Language) -> Path:
            return srt_path(v, l)

        try:
            info = engine.probe(runner, v)
            log.info("duration %.1f min, audio=%s", info.duration_ms / 60000, info.has_audio)
            wav = work / "audio.wav"
            if job["transcribe"] or cut:
                if not info.has_audio:
                    raise ToolError(t("gui.error.no_audio"))
                prog.start(s_extract)
                engine.extract_audio(runner, v, wav)
            if job["transcribe"]:
                prog.start(s_transcribe)
                cues = engine.transcribe(runner, wav, lang, job["model"], info.duration_ms,
                                         self.settings.use_gpu, prog, work)
                if not cues:
                    raise ToolError(t("gui.error.no_speech"))
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
                    post(("skipped", t("gui.done.claude_skipped", plural=lang.plural)))
                    return
            if cut:
                v, info = self._cut_step(job, v, info, wav, prog, runner, work, s_cut)
                if v is None:   # nothing cut and nothing else to make
                    post(("done", orig_srt))
                    return
            if speech:
                out = dub_name(v, speech, subs)
                engine.dub(runner, v, read_srt(srt_of(speech)), job["voice"], out, srt_of(subs) if subs else None,
                           self.settings.keep_original_audio, info, prog, work, s_voice, s_dub,
                           voices=job.get("voices"), palette=job.get("palette"),
                           speaker_model=job.get("speaker_model"))
                post(("file", out))
            elif subs:
                prog.start(s_burn)
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
            post(("error", t("gui.error.unexpected", error=e)))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _cut_step(self, job: dict, v: Path, info, wav: Path, prog, runner: Runner, work: Path, step_name: str):
        """Suggest cuts (silent pauses + Claude's), let the user review them, cut, and move the subtitles over.
        Returns (the video to carry on with, its info), or (None, None) when nothing was cut and the job only
        asked for cutting."""
        post = self.events.put
        lang = job["lang"]
        cues = read_srt(srt_path(v, lang))
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
        post(("review", v, info.duration_ms, cues, suggestions, env, wav))
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
        prog.start(step_name)
        cutting.cut_video(runner, v, ranges, edited, info.duration_ms, info.has_audio, prog, work)
        textstate.record_cut(edited, v, ranges)
        post(("file", edited))
        # The subtitles that belong to the original (and match its blocks) are moved to the cut video.
        checked = textstate.is_checked(srt_path(v, lang))
        for l in LANGUAGES:
            src = find_srt(v, l)
            if not src:
                continue
            try:
                these = read_srt(src)
            except Exception:
                continue
            if l != lang and engine.compare(cues, these) is not None:
                continue
            out = srt_path(edited, l)
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

    def _without_step_number(self, status: str, step: int | None) -> str:
        """The worker's status from the step's name on, without the step number Progress puts in front."""
        names = self._progress_names
        if step is not None and 0 <= step < len(names):
            i = status.find(names[step])
            if i > 0:
                return status[i:]
        return status

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
                        self.w_pct.configure(text=f"{overall * 100:.0f}%")
                    step_txt = self._without_step_number(status, step)
                    self.status.configure(text=step_txt)
                    if detail:
                        self.detail.configure(text=t("gui.work.now", text=detail.strip()))
                    if self.mcp_job and overall is not None and time.time() - self._status_written > 1.5:
                        self._status_written = time.time()
                        name, _, eta = step_txt.partition(" - ")
                        self._jstatus(state="running", percent=int(overall * 100), step=name, eta=eta)
                elif kind == "file":
                    self.add_file(ev[1])
                elif kind == "result":
                    self.job["result"] = ev[1]
                    self.w_result.configure(text=t("gui.work.you_get", name=ev[1].name))
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
                        self.status.configure(text=t("gui.work.claude_in_chat"))
                        self._jstatus(state="claude_turn", step="Claude retter og oversætter", eta="",
                                      percent=int(float(self.bar.cget("value")) / 10), message="Teksten er klar til dig.")
                    else:
                        self.status.configure(text=t("gui.work.waiting_for_you"))
                        self.callout.grid()
                        self._pulsing = True
                        self.claude_box.configure(style="TurnA.TLabelframe")
                        self._call_attention()
                    skin.skin_cards(self.main)   # after the your-turn style, so the panel takes that card's colour
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
                    if self._theme_pending or self._language_pending:
                        self.root.after(300, self._apply_pending)
                elif kind == "selfcheck":
                    self._show_selfcheck(ev[1])
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _show_result(self, kind: str, info, replay: bool = False):
        """The done view for a run's end: kind "done" (info: the result file), "error" (info: the error text),
        "skipped" (info: the message) or "cancelled". replay: drawn again after a theme or language switch, without
        the side effects (skill guide, attention)."""
        self._last_result = (kind, info)
        k = self.scale
        done = kind == "done"
        self.d_banner.configure(image=IMG.get("art/done-banner" if done else "art/continue-banner"))
        steps = self._result_steps(kind, replay)
        failed = next(((i, name) for i, (name, state) in enumerate(steps) if state == "error"), None)
        for w in (self.d_icon, self.d_where, self.d_stats, self.d_msg, self.d_error, self.d_extra, self.d_acts,
                  self.d_again, self.d_same):
            w.grid_remove()
        for w in (self.d_log, self.d_folder, self.d_play):
            w.pack_forget()
        msg = ""
        if done:
            target: Path = info
            self.d_target = target
            self.status.configure(text=t("gui.done.status"))
            text_only = target.suffix.lower() == ".srt"
            self.d_title.configure(text=t("gui.done.text_title" if text_only else "gui.done.video_title"),
                                   foreground=P.heading)
            self.d_file.configure(text=target.name)
            self.d_where.configure(text=t("gui.done.saved_in", folder=target.parent))
            self.d_where.grid()
            self._fill_stats([] if text_only else self._result_stats(target, replay))
            learned = ", ".join(f"{w} → {r}" for w, r in self.learned_words)
            if learned:
                msg = t("gui.done.learned", words=learned)
            self.d_play.configure(text=t("gui.done.open" if text_only else "gui.done.play"))
            self.d_play.pack(side="left", padx=(0, int(14 * k)))
            self.d_folder.pack(side="left", padx=(0, int(14 * k)))
            self.d_acts.grid()
            if self._skill_warning and not replay:
                reason, have = self._skill_warning
                self._skill_warning = None
                self.root.after(1200, lambda: self.show_skill_guide(reason, have))
            self.d_next_hint.configure(text=t("gui.done.next_hint"))
            self.d_same.grid()
            if not replay:
                self._call_attention()
        else:
            self.d_target = None
            if kind == "cancelled":
                self.d_title.configure(text=t("gui.done.cancelled"), foreground=P.heading)
                msg = self._cancelled_text()
            elif kind == "skipped":
                self.d_title.configure(text=t("gui.done.not_finished"), foreground=P.warning)
                msg = info or ""
            else:
                self.d_icon.grid()
                self.d_title.configure(text=t("gui.done.failed"), foreground=P.error)
                if failed:
                    msg = t("gui.done.failed_at", n=f"1-{failed[0] + 1}", step=failed[1])
                self.d_error.configure(text=info or "")
                self.d_error.grid()
                self.d_log.pack(side="left")
                self.d_acts.grid()
                if not replay:
                    self._call_attention()
            self.d_file.configure(text="")
            if self.created:
                self.d_extra.configure(text=t("gui.done.files_made"))
                self.d_extra.grid()
            self.status.configure(text=self.d_title.cget("text"))
            self.d_again.configure(text=t("gui.done.continue") if kind == "skipped" else t("gui.done.try_again"))
            self.d_again.grid()
            self.d_next_hint.configure(text=t("gui.done.next_hint_continue" if kind == "skipped"
                                              else "gui.done.next_hint_retry"))
        self.d_file.grid() if self.d_file.cget("text") else self.d_file.grid_remove()
        self.d_msg.configure(text=msg)
        if msg:
            self.d_msg.grid()
        self.d_files.grid() if self.files.size() else self.d_files.grid_remove()
        self._fill_steps(steps, done)
        self._show("done")
        skin.skin_cards(self.views["done"])

    def _result_steps(self, kind: str, replay: bool) -> list[tuple[str, str]]:
        """The run's steps for the done view's compact list, as (name, state): all done after a finished run; after an
        error the step that was running failed ("error"), and after a stop it was not finished ("todo"). The names
        lose the " - din tur" / " - i chatten" ending, which says nothing once the run is over. A replay after a theme
        switch (the step list's widgets are gone then) uses the steps kept from the run's end."""
        rows = []
        for row in self.step_rows:
            try:
                rows.append((row.get("name") or row["text"].cget("text"), row["state"]))
            except (tk.TclError, KeyError):
                rows = None
                break
        if rows is None or (replay and not rows):
            return getattr(self, "_done_steps", [])
        ends = []
        for key in ("gui.step.claude_turn", "gui.step.claude_chat"):
            end = t(key, tasks="\x00").partition("\x00")[2]
            if end.strip():
                ends.append(end)
        out = []
        for name, state in rows:
            for end in ends:
                if name.endswith(end):
                    name = name[:-len(end)]
                    break
            if kind == "done":
                state = "done"
            elif state in ("active", "turn"):
                state = "error" if kind == "error" else "todo"
            elif state != "done":
                state = "todo"
            out.append((name, state))
        self._done_steps = out
        return out

    def _fill_steps(self, steps: list[tuple[str, str]], done: bool):
        """The compact step list in the right column: 34 px rows, the step icon, the level number and the name in the
        state's colour (step_done, error or step_pending), no connectors."""
        k = self.scale
        box = self.d_steps
        for child in box.winfo_children():
            if child is not box.title:
                child.destroy()
        box.configure(text=t("gui.done.all_steps") if done else t("gui.work.steps"))
        if not steps:
            box.grid_remove()
            return
        box.grid()
        box.columnconfigure(1, minsize=int(34 * k))
        texts = []
        for i, (name, state) in enumerate(steps):
            box.rowconfigure(i, minsize=int(34 * k))
            icon = {"done": "step-done", "error": "step-error"}.get(state, "step-pending")
            colour = {"done": P.step_done, "error": P.error}.get(state, P.step_pending)
            tk.Label(box, image=IMG.get(f"icons/{icon}"), background=P.card,
                     borderwidth=0).grid(row=i, column=0, sticky="w")
            tk.Label(box, text=f"1-{i + 1}", foreground=P.hint, background=P.card, borderwidth=0, pady=0,
                     font=(theme.PIXEL_FONT, -int(10 * k))).grid(row=i, column=1, sticky="w", padx=(int(10 * k), 0),
                                                                pady=(int(2 * k), 0))
            text = tk.Label(box, text=name, foreground=colour, background=P.card, font="SunValleyBodyFont",
                            anchor="w", justify="left", padx=0, pady=0, borderwidth=0)
            text.grid(row=i, column=2, sticky="ew", padx=(int(6 * k), 0))
            texts.append(text)

        def wrap(e=None):
            width = box.winfo_width() - int(130 * k)
            if width > 50:
                for x in texts:
                    x.configure(wraplength=width)
        box.bind("<Configure>", wrap)
        wrap()

    def _result_stats(self, target: Path, replay: bool) -> list[tuple[str, str]]:
        """The numbers on a finished video: how much was cut away and the new length (when the run cut the video),
        the speech and the subtitles. What the run does not know is left out. Kept for a replay of the same result."""
        kept = getattr(self, "_done_stats", (None, []))   # (result, its stats), kept across a rebuild
        if kept[0] == target and replay:
            return kept[1]
        job = self.job or {}
        stats = []

        def clock(ms: int) -> str:
            s = round(ms / 1000)
            return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
        if job.get("cut") and self._review_result:
            try:
                removed = cutting.removed_ms(cutting.active(self._review_result, 10 ** 9))
            except Exception:
                log.exception("cut stats")
                removed = 0
            if removed:
                stats.append((f"-{round(removed / 1000)} s" if removed < 59500 else f"-{clock(removed)}",
                              t("gui.done.stat_cut")))
                try:
                    stats.append((clock(engine.probe(Runner(), target).duration_ms), t("gui.done.stat_length")))
                except Exception:
                    log.info("no length for %s", target)
        if job.get("speech"):
            sp = job["speech"]
            stats.append((sp.code, t("gui.done.stat_speech", singular=sp.singular, name=sp.name.lower())))
        if job.get("subs"):
            su = job["subs"]
            stats.append((su.code, t("gui.done.stat_subs", plural=su.plural)))
        self._done_stats = (target, stats)
        return stats

    def _fill_stats(self, stats: list[tuple[str, str]]):
        """The stats row in the result card: the value in the pixel font (upper case), its label under it in hint."""
        k = self.scale
        for child in self.d_stats.winfo_children():
            child.destroy()
        if not stats:
            return
        for i, (value, label) in enumerate(stats):
            colour = P.accent if value.startswith("-") else P.heading
            tk.Label(self.d_stats, text=value.upper(), foreground=colour, background=P.card, borderwidth=0, padx=0,
                     pady=0, font=(theme.PIXEL_FONT, -int(22 * k))).grid(row=0, column=i, sticky="w",
                                                                        padx=(0, int(36 * k)))
            ttk.Label(self.d_stats, text=label, style="Hint.TLabel").grid(row=1, column=i, sticky="w",
                                                                         padx=(0, int(36 * k)), pady=(int(2 * k), 0))
        self.d_stats.grid()

    def _cancelled_text(self) -> str:
        """What a stopped run kept: the subtitles in the spoken language, if this run wrote them or reused them
        (they were current next to the video), are used again next time. A run that was listening again and
        was stopped before it finished has saved nothing new."""
        job = self.job or {}
        lang = job.get("lang")
        kept = any(p.suffix.lower() == ".srt" for p in self.created)
        if not kept and lang and job.get("video") and not job.get("transcribe"):
            try:
                kept = textstate.status(srt_path(job["video"], lang), job["video"]) == "ok"
            except Exception:
                kept = False
        if kept and lang:
            return t("gui.done.cancelled_kept", plural=lang.plural)
        return t("gui.done.cancelled_nothing")

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
        if sel and sel[0] < len(self.created):
            self._open(self.created[sel[0]])

    # --- Self check (also tells us whether App Control blocks the bundled tools) ----------

    def _self_check(self):
        problems = []
        for name, exe, args in (("ffmpeg", FFMPEG(), ["-version"]), ("Whisper", WHISPER(), ["--help"]),
                                ("Piper", PIPER(), ["--help"])):
            try:
                if not exe.exists():
                    problems.append(t("gui.selfcheck.missing", name=name, path=exe))
                    continue
                subprocess.run([str(exe)] + args, capture_output=True, timeout=60, creationflags=NO_WINDOW,
                               cwd=str(exe.parent))
            except OSError as e:
                problems.append(t("gui.selfcheck.cannot_start", name=name, error=e))
            except subprocess.TimeoutExpired:
                pass
        log.info("self check: %s", problems or "ok")
        if problems:
            self.events.put(("selfcheck", problems))

    def _show_selfcheck(self, problems):
        boxes.showerror(APP_NAME, t("gui.selfcheck.message", problems="\n".join(problems), log=log_file()),
                        parent=self._owner())

    # --- Dialogs and the Claude panel ---------------------------------------------------

    def _settings_dialog(self):
        SettingsDialog(self.root, self.settings, on_theme=self.set_theme, on_language=self.set_language,
                       voice_lang=self._preview_lang, get_models=self._get_voice_models)

    def _preview_lang(self) -> Language:
        """The language Indstillinger's play button samples: the chosen dub language, else the app's language."""
        return self._speech_lang() or LANG_BY_CODE.get(i18n.current()) or LANGUAGES[0]

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
            self.status.configure(text=t("gui.work.thanks_continues"))
        self._translation_done.set()

    # --- The cut review ---------------------------------------------------------------------

    def _start_review(self, video: Path, duration: int, cues, suggestions, env, wav: Path | None = None):
        from .review import CutReview
        self._set_step(self.review_at)
        self._review = CutReview(self.left, self, video, duration, cues, suggestions, env,
                                 on_done=self._review_finished, mcp_job=self.mcp_job, wav=wav)
        self._review.grid(row=0, column=0, sticky="nsew")
        skin.skin_cards(self.main)
        for frame in self.views.values():
            frame.grid_remove()
        self.view = "review"
        self._user_turn(True)
        self.status.configure(text=t("gui.work.waiting_review"))
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
        self.status.configure(text=t("gui.work.thanks_cutting") if chosen else t("gui.work.no_cuts"))
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


class SegmentBar(ttk.Frame):
    """The arcade progress bar: 20 segments, 22 px tall with 3 px between them, in the progress-trough frame with 4 px
    inside it (design review round 1, item 17), the last filled one highlighted. Takes the Progressbar calls the app
    makes: configure(value=0..1000) and cget("value")."""
    SEGMENTS = 20

    def __init__(self, parent, scale: float):
        self.k = 2 if IMG.two else 1
        bl, bt, br, bb = P.nine_slice("progress-trough", IMG.two)
        pad = 4 * self.k
        super().__init__(parent, style="Trough.TFrame", padding=(bl // 2 + pad, bt // 2 + pad, br // 2 + pad,
                                                                 bb // 2 + pad))
        self.canvas = tk.Canvas(self, height=22 * self.k, highlightthickness=0, borderwidth=0,
                                background=P.progress_trough)
        self.canvas.pack(fill="x")
        self._value = 0.0
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def configure(self, cnf=None, **kw):
        if "value" in kw:
            self._value = max(0.0, min(1000.0, float(kw.pop("value"))))
            self._draw()
        return super().configure(cnf, **kw) if (cnf or kw) else None

    config = configure

    def cget(self, key):
        return self._value if key == "value" else super().cget(key)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        k, w, h = self.k, c.winfo_width(), int(c.cget("height"))
        if w < 10:
            return
        gap = 3 * k
        seg = (w - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        filled = int(round(self._value / 1000 * self.SEGMENTS))
        for i in range(filled):
            x0 = i * (seg + gap)
            colour = P.progress_fill_current if i == filled - 1 and filled < self.SEGMENTS else P.progress_fill
            c.create_rectangle(x0, 0, x0 + seg, h, fill=colour, outline="")


class SkillGuide(tk.Toplevel):
    """How to put the new Claude skill into Claude: after an update (this version ships a newer skill), or when
    Claude worked without the skill or with an old one."""

    def __init__(self, parent, app: App, reason: str, have: int | None = None):
        super().__init__(parent)
        self.withdraw()   # shown by place_over, so it never flashes in Windows' default spot
        self.app = app
        heading = t("gui.skill.heading_first") if reason == "first" else t("gui.skill.heading_update")
        self.title(heading)
        self.transient(parent)
        self.resizable(False, False)
        intro = t(f"gui.skill.intro_{reason}", app=APP_NAME, have=have, newest=claude.SKILL_VERSION)
        f = ttk.Frame(self, padding=18)
        f.pack(fill="both", expand=True)
        wrap = int(520 * app.scale)
        ttk.Label(f, text=heading, style="Section.TLabel", foreground=P.heading).pack(anchor="w")
        ttk.Label(f, text=intro, wraplength=wrap, justify="left").pack(anchor="w", pady=(6, 12))
        old = (claude.SKILL_NAME,) + claude.OLD_SKILL_NAMES
        steps = [t("gui.skill.step_save", save=t("gui.skill.save")),
                 t("gui.skill.step_open"),
                 t("gui.skill.step_delete", names=f" {t('gui.and')} ".join(f"\"{n}\"" for n in old)),
                 t("gui.skill.step_upload", file=f"{claude.SKILL_NAME}-skill.zip")]
        # The delete step stays on "first" too: skill_installed 0 means unknown, not absent (installed through
        # Settings, "Senere", or before tracking), and uploading over an existing one is what it prevents. The skill
        # under the app's earlier name would otherwise answer next to the new one.
        for i, step in enumerate(steps, 1):
            ttk.Label(f, text=f"{i}.  {step}", wraplength=wrap, justify="left").pack(anchor="w", pady=2)
        self.status = ttk.Label(f, text="", style="Hint.TLabel", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", pady=(10, 0))
        b = ttk.Frame(f)
        b.pack(anchor="w", pady=(14, 0))
        save_btn = ttk.Button(b, text=t("gui.skill.save"), style="Accent.TButton", command=self._save, default="active")
        save_btn.pack(side="left")
        ttk.Button(b, text=t("gui.skill.done"), command=self._done).pack(side="left", padx=8)
        ttk.Button(b, text=t("gui.skill.later"), command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._save())
        place_over(self, parent.winfo_toplevel())
        save_btn.focus_set()

    def _save(self):
        dest = downloads_dir() / f"{claude.SKILL_NAME}-skill.zip"
        try:
            claude.skill_zip(dest)
        except OSError as e:
            self.status.configure(text=t("gui.skill.save_failed", error=e), foreground=P.error)
            return
        self.status.configure(text=t("gui.skill.saved", path=dest), foreground=P.success)
        subprocess.Popen(["explorer", "/select,", str(dest)])

    def _done(self):
        self.app.settings.skill_installed = claude.SKILL_VERSION
        self.app.settings.save()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """Indstillinger (the cog in the header): Programmets sprog and Udseende as tiles, then text and voices."""

    def __init__(self, parent, settings: Settings, on_theme=None, on_language=None, voice_lang=None, get_models=None):
        super().__init__(parent)
        self.withdraw()
        self.title(t("gui.settings.title"))
        self.transient(parent)
        self.resizable(False, False)
        self.s = settings
        self.on_theme, self.on_language = on_theme, on_language
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both")
        lang = skin.Card(outer, text=t("pickers.lang_title"), padding=12)
        lang.pack(fill="x")
        ttk.Label(lang, text=t("pickers.lang_hint"), style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        self.ui_language = tk.StringVar(value=i18n.current())
        language_tiles(lang, self.ui_language).pack(fill="x")
        look = skin.Card(outer, text=t("gui.settings.look"), padding=12)
        look.pack(fill="x", pady=(14, 0))
        ttk.Label(look, text=t("pickers.look_hint"), style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        self.look = tk.StringVar(value=settings.theme)
        theme_tiles(look, self.look).pack(fill="x")
        f = skin.Card(outer, text=t("pickers.text_voices"), padding=12)
        f.pack(fill="x", pady=(14, 0))
        ttk.Label(f, text=t("gui.settings.model")).grid(row=0, column=0, sticky="w")
        self.model = tk.StringVar(value=settings.whisper_model)
        for i, key in enumerate(engine.WHISPER_MODELS, 1):
            ttk.Radiobutton(f, text=engine.model_label(key), value=key, variable=self.model).grid(row=i, column=0, sticky="w", padx=12)
        self.gpu = tk.BooleanVar(value=settings.use_gpu)
        ttk.Checkbutton(f, text=t("gui.settings.gpu"), variable=self.gpu).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.keep = tk.BooleanVar(value=settings.keep_original_audio)
        self.voice_mode = tk.StringVar(value=settings.voice_mode)
        dubopts = ttk.Frame(f)
        dubopts.grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(dubopts, text=t("gui.settings.keep_audio"), variable=self.keep).pack(anchor="w")
        voices_row = ttk.Frame(dubopts)
        voices_row.pack(anchor="w", fill="x", pady=(8, 0))
        ttk.Label(voices_row, text=t("gui.settings.voices")).pack(side="left")
        if voice_lang:   # Lyt til stemmen: the chosen voice mode, in the dub language chosen in the main window
            self.voice_btn = voice_preview.PreviewButton(
                voices_row, voice_lang, self.voice_mode.get,
                (lambda missing: get_models(missing, self)) if get_models else None,
                label=lambda: t("voice.listen", singular=voice_lang().singular))
            self.voice_btn.pack(side="left", padx=(8, 0))
            self.voice_mode.trace_add("write", lambda *a: self.voice_btn.stop())
        for value in ("person", "gender", "one"):
            ttk.Radiobutton(dubopts, text=t(f"gui.settings.voice_{value}"), value=value,
                            variable=self.voice_mode).pack(anchor="w", padx=12)
        links = ttk.Frame(f)
        links.grid(row=6, column=0, sticky="w", pady=(12, 0))
        ttk.Button(links, text=t("gui.open_model_dir"), command=lambda: self._open(model_dirs()[0])).pack(side="left")
        ttk.Button(links, text=t("gui.settings.edit_corrections"), command=lambda: self._open(engine.corrections_file())).pack(side="left", padx=6)
        ttk.Button(links, text=t("gui.settings.save_skill"), command=self._save_skill).pack(side="left", padx=(0, 6))
        ttk.Button(links, text=t("gui.settings.connect"), command=self._connect_claude).pack(side="left", padx=(0, 6))
        ttk.Button(links, text=t("gui.open_log"), command=lambda: self._open(log_file())).pack(side="left")
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(14, 0))
        ttk.Label(foot, text=f"{APP_NAME} {APP_VERSION}", style="Hint.TLabel").pack(side="left")
        b = ttk.Frame(foot)
        b.pack(side="right")
        ttk.Button(b, text=t("gui.cancel"), command=self.destroy).pack(side="left", padx=(0, 6))
        save_btn = ttk.Button(b, text=t("gui.save"), style="Accent.TButton", command=self._save, default="active")
        save_btn.pack(side="left")
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
            boxes.showerror(t("gui.settings.connect"), t("gui.settings.connect_failed", error=e), parent=self)
            return
        boxes.showinfo(t("gui.settings.connect"), t("gui.settings.connect_done", path=path), parent=self)

    def _save_skill(self):
        dest = filedialog.asksaveasfilename(parent=self, title=t("gui.settings.save_skill_title"), defaultextension=".zip",
                                            initialdir=str(Path.home() / "Downloads"),
                                            initialfile=f"{claude.SKILL_NAME}.zip", filetypes=[("Zip", "*.zip")])
        if not dest:
            return
        claude.skill_zip(Path(dest))
        boxes.showinfo(t("gui.settings.skill_title"), t("gui.settings.skill_saved"), parent=self)

    def _save(self):
        self.s.whisper_model, self.s.use_gpu, self.s.keep_original_audio = self.model.get(), self.gpu.get(), self.keep.get()
        self.s.voice_mode = self.voice_mode.get()
        self.s.save()
        look, code = self.look.get(), self.ui_language.get()
        self.destroy()
        if self.on_theme and look != self.s.theme:
            self.on_theme(look)
        if self.on_language and code != i18n.current():
            self.on_language(code)


class ModelDialog(tk.Toplevel):
    """Shown when model files are missing: download them, or fetch them by hand."""

    def __init__(self, parent, missing: list[engine.ModelFile], scale: float = 1.0):
        super().__init__(parent)
        self.withdraw()
        self.title(t("gui.models.title"))
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
        ttk.Label(f, text=t("gui.models.intro", mb=mb),
                  wraplength=wrap).pack(anchor="w")
        for m in missing:
            row = ttk.Frame(f)
            row.pack(anchor="w", fill="x", pady=1)
            ttk.Label(row, text="•  " + m.filename).pack(side="left")
            lnk = ttk.Label(row, text=t("gui.models.link"), foreground="#0645ad", cursor="hand2")
            lnk.pack(side="left", padx=8)
            lnk.bind("<Button-1>", lambda e, u=m.url: webbrowser.open(u))
        ttk.Label(f, wraplength=wrap, style="Hint.TLabel",
                  text=t("gui.models.manual", download=t("gui.models.download"))).pack(anchor="w", pady=(8, 4))
        self.bar = ttk.Progressbar(f, maximum=1000)
        self.bar.pack(fill="x", pady=4)
        self.status = ttk.Label(f, text="")
        self.status.pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(10, 0))
        self.get_btn = ttk.Button(b, text=t("gui.models.download"), style="Accent.TButton", command=self._download)
        self.get_btn.pack(side="left")
        ttk.Button(b, text=t("gui.open_model_dir"), command=self._open_dir).pack(side="left", padx=6)
        ttk.Button(b, text=t("gui.models.placed"), command=self._check).pack(side="left")
        ttk.Button(b, text=t("gui.cancel"), command=self._close).pack(side="left", padx=6)
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
            boxes.showwarning(t("gui.models.still_missing_title"), t("gui.models.still_missing", files="\n".join(still)),
                              parent=self)
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
                    boxes.showerror(t("gui.models.failed_title"), t("gui.models.failed", error=ev[1]), parent=self)
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
        self.work_file = output_path(video, t("file.task"))
        self.answer_name = output_path(video, t("file.answer")).name
        self.started = time.time()
        self.mode = "file"
        self.watching: int | None = None
        self._prompt_text = ""
        self._seen_clip = ""
        self._seen_files: dict[str, float] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        todo = ([t("gui.claude.task_check", plural=src.plural)] if check else []) + \
               ([t("gui.claude.task_translate", langs=join_and([x.singular for x in self.targets]))]
                if self.targets else []) + \
               ([t("gui.claude.task_cut")] if cut else [])
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="w")
        self.head = ttk.Label(top, text=t("gui.claude.your_turn"), style="Big.TLabel", foreground=P.your_turn_title)
        self.head.pack(side="left", anchor="s")
        ttk.Label(top, text="PLAYER 1", style="Hint.TLabel", font=(theme.PIXEL_FONT, -int(10 * app.scale))).pack(
            side="left", anchor="s", padx=(int(16 * app.scale), 0), pady=(0, int(10 * app.scale)))
        intro = ttk.Label(self, justify="left", text=t("gui.claude.intro", tasks=join_and(todo)))
        intro.grid(row=1, column=0, sticky="ew", pady=(2, 12))
        app._wrap(intro, self, 10)
        self.body = ttk.Frame(self)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.footer = ttk.Frame(self)
        self.footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.footer.columnconfigure(0, weight=1)
        self.switch_btn = ttk.Button(self.footer, text=t("gui.claude.to_parts"), command=self._to_parts)
        self.switch_btn.grid(row=0, column=0, sticky="w")
        ttk.Button(self.footer, text=t("gui.claude.stop"), command=self._close).grid(row=0, column=1, padx=(0, 8))
        self.save_btn = ttk.Button(self.footer, text=t("gui.claude.save_continue"), command=self._save_parts)
        # Whatever is on the clipboard now is old (maybe an answer from an earlier round): never accept it.
        try:
            self._seen_clip = self.clipboard_get()
        except tk.TclError:
            self._seen_clip = ""
        if mcp_job:
            self._build_mcp_mode()
        else:
            self._build_file_mode()
            self._spin_fsteps()
        self.after(700, self._tick)

    # --- Claude does it in the chat (job from the MCP server) ----------------------------------

    def _build_mcp_mode(self):
        self.mode = "mcp"
        self.head.configure(text=t("gui.claude.working"))
        d = jobs.job_dir(self.mcp_job)
        jobs.write_json(d / "source.json", jobs.cues_to_json(self.cues))
        jobs.write_json(d / "task.json", {
            "src": self.src.code, "check": self.check, "targets": [t.code for t in self.targets],
            "codes": self.codes, "blocks": len(self.cues), "words": claude.known_words(),
            "cut": self.cut, "duration": self.duration,
            "rules": [list(r) for r in claude.word_rules()[:80]]})
        b = self.body
        msg = ttk.Label(b, justify="left", text=t("gui.claude.mcp_intro"))
        msg.grid(row=0, column=0, sticky="ew")
        self.app._wrap(msg, b, 10)
        names = {l.code: l.name for l in LANGUAGES}
        self.mcp_bars = {}
        for i, code in enumerate(self.codes, 1):
            row = ttk.Frame(b, padding=(0, 10, 0, 0))
            row.grid(row=i, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)
            label = t("gui.claude.corrected", name=names[code]) if code == self.src.code else names[code]
            ttk.Label(row, text=label, width=16).grid(row=0, column=0, sticky="w")
            bar = ttk.Progressbar(row, maximum=len(self.cues))
            bar.grid(row=0, column=1, sticky="ew", padx=8)
            lab = ttk.Label(row, text=t("gui.claude.n_of", n=0, total=len(self.cues)), style="Hint.TLabel", width=12)
            lab.grid(row=0, column=2, sticky="e")
            self.mcp_bars[code] = (bar, lab)
        self.mcp_cut_lbl = None
        if self.cut:
            self.mcp_cut_lbl = ttk.Label(b, text=t("gui.claude.cuts_none"), style="Hint.TLabel")
            self.mcp_cut_lbl.grid(row=len(self.codes) + 1, column=0, sticky="w", pady=(10, 0))
        self.switch_btn.configure(text=t("gui.claude.to_file"), command=self._mcp_to_file)

    def _mcp_to_file(self):
        self.mode = "file"
        self.head.configure(text=t("gui.claude.your_turn"))
        for w in self.body.winfo_children():
            w.destroy()
        self.switch_btn.configure(text=t("gui.claude.to_parts"), command=self._to_parts)
        self.app.callout.grid()
        self.app._pulsing = True
        self._build_file_mode()
        self._spin_fsteps()
        skin.skin_cards(self.app.main)

    def _mcp_tick(self):
        d = jobs.root() / self.mcp_job
        for code, (bar, lab) in self.mcp_bars.items():
            n = len(jobs.read_json(d / "parts" / f"{code}.json", {}) or {})
            bar.configure(value=n)
            lab.configure(text=t("gui.claude.n_of", n=n, total=len(self.cues)))
        if self.mcp_cut_lbl is not None:
            k = jobs.read_json(d / "parts" / "cuts.json", {}) or {}
            n = len(k.get("lines") or [])
            self.mcp_cut_lbl.configure(text=t("gui.claude.cuts_done" if k.get("done") else "gui.claude.cuts_got", n=n))
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
            boxes.showwarning("Claude", t("gui.claude.work_file_failed", error=e),
                                   parent=self.app.root)
            self._to_parts()
            return
        b = self.body
        self.fsteps = []
        k = self.app.scale
        texts = [
            (t("gui.claude.step_copy"), f"\"{self.work_file.name}\""),
            (t("gui.claude.step_paste"), t("gui.claude.step_paste_hint")),
            (t("gui.claude.step_download"), t("gui.claude.step_download_hint")),
        ]
        for i, (title, sub) in enumerate(texts):
            row = ttk.Frame(b, padding=(0, 6))
            row.grid(row=i, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)
            icon = tk.Label(row, image=IMG.get("icons/step-pending"), borderwidth=0, background=P.card)
            icon.grid(row=0, column=0, rowspan=2, sticky="nw", pady=(2, 0))
            head = ttk.Label(row, text=f"{title}", font="SunValleyBodyStrongFont")
            head.grid(row=0, column=1, sticky="w", padx=(10, 0))
            s = ttk.Label(row, text=sub, style="Hint.TLabel", justify="left")
            s.grid(row=1, column=1, sticky="ew", padx=(10, 0))
            self.app._wrap(s, row, int(60 * k))
            self.fsteps.append({"icon": icon, "title": head, "sub": s, "row": row, "state": "todo"})
        acts = ttk.Frame(self.fsteps[0]["row"])
        acts.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        self.copy_file_btn = skin.IconButton(acts, "copy", text=t("gui.claude.copy_file"), command=self._copy_file)
        self.copy_file_btn.pack(side="left")
        skin.IconButton(acts, "show-in-folder", text=t("gui.show_in_folder"), command=lambda: self.app._show_in_folder(self.work_file)).pack(side="left", padx=8)
        acts3 = ttk.Frame(self.fsteps[2]["row"])
        acts3.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        skin.IconButton(acts3, "choose-video", text=t("gui.claude.pick_answer"), command=self._pick_answer).pack(side="left")
        # What the program is doing about step 3 right now (waiting, found a file, checking it)
        self.wait_lbl = ttk.Label(self.fsteps[2]["row"], text="", style="Hint.TLabel", justify="left")
        self.wait_lbl.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))
        self.app._wrap(self.wait_lbl, self.fsteps[2]["row"], int(60 * k))
        self._wait_since: float | None = None
        self._answer_found = False   # the waiting line shows a found answer (file or clipboard), not the wait
        self.result = ttk.Label(b, text="", justify="left")
        self.result.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.app._wrap(self.result, b, 10)
        self.problem_detail = ttk.Label(b, text="", style="Hint.TLabel", foreground=P.hint, justify="left")
        self.app._wrap(self.problem_detail, b, 10)
        self.followup_btn = ttk.Button(b, text=t("gui.claude.copy_followup"), command=self._copy_followup)
        self._set_fstep(0)

    def _set_fstep(self, active: int, failed: bool = False):
        for i, st in enumerate(self.fsteps):
            st["state"] = "done" if i < active else ("active" if i == active else "todo")
            if failed and i == active:
                st["state"] = "failed"
            st["title"].configure(foreground=P.success if st["state"] == "done" else
                                  P.error if st["state"] == "failed" else
                                  P.heading if st["state"] == "active" else P.hint)
            self._draw_fstep(st)
        self.copy_file_btn.configure(style="Accent.TButton" if active == 0 else "TButton")

    def _draw_fstep(self, st: dict, frame: int = 0):
        """The sub-step's pixel icon, as in the step list: done, failed, the current one (animated) or not yet."""
        name = {"done": "step-done", "failed": "step-error", "active": f"step-active-{frame % 6}"}.get(
            st["state"], "step-pending")
        st["icon"].configure(image=IMG.get(f"icons/{name}"))

    def _spin_fsteps(self, frame: int = 0):
        """About 120 ms a frame on the current sub-step, while the panel lives."""
        if not self.winfo_exists():
            return
        for st in getattr(self, "fsteps", []):
            if st["state"] == "active":
                self._draw_fstep(st, frame)
        self.after(120, lambda: self._spin_fsteps(frame + 1))

    def _copy_file(self):
        if copy_file_to_clipboard(self.work_file):
            self.fsteps[0]["sub"].configure(text=t("gui.claude.copied_file", name=self.work_file.name))
            # Step 2 (paste it into Claude) stays open until an answer file shows up: the program cannot see the chat
            if self.fsteps[2]["state"] not in ("active", "failed"):
                self._set_fstep(1)
            if self._wait_since is None:
                self._wait_since = time.time()
        else:
            self.fsteps[0]["sub"].configure(text=t("gui.claude.copy_failed", show=t("gui.show_in_folder")))
            self.app._show_in_folder(self.work_file)

    def _pick_answer(self):
        p = filedialog.askopenfilename(parent=self.app.root, title=t("gui.claude.pick_answer_title"),
                                       initialdir=str(downloads_dir()),
                                       filetypes=[(t("gui.filetype.text"), "*.txt *.srt *.md"), (t("gui.filetype.all"), "*.*")])
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
        self._answer_found = True
        self.wait_lbl.configure(text=t("gui.claude.found_checking", name=f.name))
        self.update_idletasks()
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            self._show_problem(t("gui.claude.read_failed_detail", name=f.name, error=e),
                               plain=t("gui.claude.read_failed"), followup=False)
            return
        aid = claude.answer_id(text)
        if picked and aid not in (None, self.job_id):
            self._show_problem(t("gui.claude.other_round_detail"), plain=t("gui.claude.other_round"), followup=False)
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
        self.wait_lbl.configure(text=t("gui.claude.found", name=f.name))
        self.result.configure(text=t("gui.claude.answer_ok_blocks", n=len(self.cues)),
                              foreground=P.success, font="SunValleyBodyStrongFont")
        self.problem_detail.grid_remove()
        self.followup_btn.grid_remove()
        self.after(1200, lambda: self._finish(have))

    def _show_problem(self, msg: str, plain: str | None = None, followup: bool = True):
        """One plain sentence for the user; the technical reason only as a small grey line (and in the log)."""
        log.info("answer problem: %s", msg)
        self._followup = claude.followup_text(self.answer_name, msg, self.cut)
        self._set_fstep(2, failed=True)
        self._answer_found = False
        self.wait_lbl.configure(text="")
        self.result.configure(text=plain or t("gui.claude.answer_incomplete", button=t("gui.claude.copy_followup")),
                              foreground=P.error, font="SunValleyBodyFont")
        self.problem_detail.configure(text=t("gui.claude.details", text=msg))
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
        self.followup_btn.configure(text=t("gui.claude.followup_copied"), style="TButton")
        self.after(3000, lambda: self.winfo_exists() and self.followup_btn.configure(text=t("gui.claude.copy_followup")))

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.mode == "mcp" and self.on_finish:
            self._mcp_tick()
        elif self.mode == "file" and self.on_finish:
            if self.fsteps[2]["state"] != "done":
                # Also before "Kopiér fil": the user may have dragged the file into Claude themselves
                self._scan_downloads()
            if self._wait_since is not None and self.fsteps[2]["state"] != "done" and not self._answer_found:
                mins = int((time.time() - self._wait_since) // 60)
                self.wait_lbl.configure(text=t("gui.claude.waiting_minutes", mins=mins) if mins
                                        else t("gui.claude.waiting"))
            # Claude's answer pasted as text also counts (if Claude wrote it in the chat instead of a file)
            try:
                text = self.clipboard_get()
            except tk.TclError:
                text = ""
            if text and text != self._seen_clip and "-->" in text and "===" in text:
                self._seen_clip = text
                have, words, msg = claude.check_file_answer(self.cues, self.codes, text + "\n=== end ===", self.cut)
                if msg is None:
                    self.words = list(words)
                    self.app.cut_lines = claude.answer_cuts(text) or []
                    self.app.note_skill(claude.answer_skill(text))
                    self._set_fstep(3)
                    self._answer_found = True
                    self.wait_lbl.configure(text=t("gui.claude.found_clipboard"))
                    self.problem_detail.grid_remove()
                    self.result.configure(text=t("gui.claude.answer_ok"), foreground=P.success)
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
        self.next_box = tk.Frame(b, background=P.card, padx=12, pady=10)
        self.next_box.grid(row=0, column=0, sticky="ew")
        self.next_box.columnconfigure(0, weight=1)
        self.next_lbl = tk.Label(self.next_box, text="", font="SunValleyBodyStrongFont", background=P.card,
                                 foreground=P.heading, justify="left", anchor="w")
        self.next_lbl.grid(row=0, column=0, sticky="ew")
        self.next_box.bind("<Configure>", lambda e: self.next_lbl.configure(wraplength=max(150, e.width - 30)))
        ttk.Label(b, text=(t("gui.parts.split", n=len(self.parts)) if len(self.parts) > 1 else ""),
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
            title = ttk.Label(box, text=t("gui.parts.title", n=i + 1, first=n0, last=n0 + len(part) - 1),
                              font="SunValleyBodyStrongFont")
            title.grid(row=0, column=0, sticky="w")
            cb = ttk.Button(box, text=t("gui.parts.copy"), width=9, command=lambda i=i: self._copy(i))
            cb.grid(row=0, column=1, padx=6)
            pb = ttk.Button(box, text=t("gui.parts.paste"), width=11, command=lambda i=i: self._paste(i))
            pb.grid(row=0, column=2)
            lab = ttk.Label(box, text="", justify="left", foreground=P.hint)
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
        for i, title in enumerate(self.part_titles):
            title.configure(foreground=P.success if self.answers[i] else (P.step_active if i == nxt else ""))
        if nxt is None:
            self.watching = None
            self.save_btn.configure(state="normal", style="Accent.TButton")
            self.save_btn.focus_set()
            self.next_lbl.configure(text=t("gui.parts.all_ready", save=t("gui.claude.save_continue")))
            return
        n = t("gui.parts.part", n=nxt + 1) if len(self.parts) > 1 else t("gui.parts.the_text")
        if not self.copied[nxt]:
            self.copy_btns[nxt].configure(style="Accent.TButton")
            self.copy_btns[nxt].focus_set()
            self.next_lbl.configure(text=t("gui.parts.next_copy", copy=t("gui.parts.copy"), part=n))
        else:
            self.paste_btns[nxt].configure(style="Accent.TButton")
            self.next_lbl.configure(text=t("gui.parts.waiting", part=n))

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
        self.state_labels[i].configure(text=t("gui.parts.copied"), foreground=P.hint)
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
            got = [claude.LANG_BY_CODE[c].singular for c in self.codes if c in have]
            prefix = t("gui.parts.have", langs=", ".join(got)) + " " if got and len(self.codes) > 1 else ""
            self.state_labels[i].configure(text="✗ " + prefix + msg, foreground=P.error)
        else:
            self.answers[i] = have
            if self.words:
                ok = t("gui.parts.ok_words", n=len(self.parts[i]), words=len(set(self.words)))
            else:
                ok = t("gui.parts.ok", n=len(self.parts[i]))
            self.state_labels[i].configure(text="✓ " + ok, foreground=P.success)
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
            if not boxes.askyesno("Claude", t("gui.claude.stop_ask"), parent=self.app.root):
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
            out = srt_path(self.video, lang)
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
    from .installer import point_claude_at
    if getattr(sys, "frozen", False):
        exe, args = Path(sys.executable), ["--mcp"]
    else:
        exe, args = Path(sys.executable), [str(Path(__file__).resolve().parent.parent / "main.py"), "--mcp"]
    cfg = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    point_claude_at(exe, args)
    log.info("connected to Claude desktop: %s -> %s %s", cfg, exe, args)
    return cfg


SINGLE_INSTANCE = f"Local\\{APP_NAME}.SingleInstance"


def already_running() -> bool:
    """Only one window: a second start brings the first one forward instead."""
    try:
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        main.mutex = k32.CreateMutexW(None, False, SINGLE_INSTANCE)
        if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return False
        # By window class too: a message box or a Claude window titled like the app must not match
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
    settings = Settings()
    i18n.set_language(i18n.resolve(settings.ui_language))
    from .installer import offer_install
    if "--from-claude" not in sys.argv and not offer_install():   # installed (or the installed copy started); this copy stops here
        return
    if already_running():
        return
    log.info("start %s %s", APP_NAME, APP_VERSION)
    from .installer import cleanup, running_installed
    from .common import adopt_previous_models
    if running_installed():   # old version folders nothing runs from any more, models left under an earlier name
        threading.Thread(target=lambda: (_safe(cleanup), _safe(adopt_previous_models)), daemon=True).start()
    set_app_id()
    theme.load_fonts()
    root = tk.Tk()
    App(root)
    root.mainloop()
