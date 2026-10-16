"""The two looks the user picks between (Indstillinger -> Udseende, or the picker in the header): Lys (assets/ui/a-light)
and Nat (assets/ui/b-night), or Følg Windows (the default). Everything comes from assets/ui (made by the design session,
see its README.md): tokens.json for the colours by role, 9-slice frames for cards, buttons and the progress bar, pixel
icons, and the header and done art. The pixel fonts (assets/fonts) are loaded privately for the process.

A theme is applied before the widgets are built; switching rebuilds the window (App.set_theme), so nothing has to
recolour itself. P holds the current colours by role: P.text, P.heading, P.cut_start_border ...
"""
from __future__ import annotations

import ctypes
import json
import sys
import tkinter as tk
from pathlib import Path

from .common import app_dir, log

THEMES = {"light": "a-light", "night": "b-night"}
CHOICES = ("system", "light", "night")
PIXEL_FONT = "Press Start 2P"
TERMINAL_FONT = "VT323"


def assets_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir())) / "assets"
    return Path(__file__).resolve().parent.parent.parent / "assets"


def windows_prefers_light() -> bool:
    """Windows' app theme (Settings -> Personalisation -> Colours -> app mode)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def resolve(choice: str) -> str:
    """"light" or "night" for a choice of "system", "light" or "night"."""
    if choice in THEMES:
        return choice
    return "light" if windows_prefers_light() else "night"


class Palette:
    """The current theme's colours by role (tokens.json), as attributes."""

    def __init__(self):
        self.name = "light"
        self._tokens: dict = {}

    def load(self, name: str):
        self.name = name
        self._tokens = _tokens()
        self.__dict__.update(self._tokens["themes"][THEMES[name]])

    def nine_slice(self, frame: str, scale2: bool) -> tuple[int, int, int, int]:
        ns = self._tokens["nine_slice"][THEMES[self.name]][frame]
        b = ns["2x"] if scale2 else ns["1x"]
        return tuple(b) if isinstance(b, (list, tuple)) else (b, b, b, b)


_TOKENS = None


def _tokens() -> dict:
    global _TOKENS
    if _TOKENS is None:
        _TOKENS = json.loads((assets_dir() / "ui" / "tokens.json").read_text(encoding="utf-8"))
    return _TOKENS


P = Palette()


class Images:
    """PhotoImages from the current theme's folder at 1x or 2x, cached and kept referenced (Tk drops images that
    Python no longer references). common/ holds the files both themes share."""

    def __init__(self):
        self.root = None
        self.two = False
        self._caches: dict[tuple, dict[str, tk.PhotoImage]] = {}

    @property
    def _cache(self) -> dict[str, tk.PhotoImage]:
        """One cache per theme and scale, kept for the root's life: ttk keeps the image elements it made for a theme
        ("Btn2.border-light"), so their images must outlive a switch to the other theme and back."""
        return self._caches.setdefault((P.name, self.two), {})

    def reset(self, root, two: bool):
        if root is not self.root:
            self._caches = {}
        self.root, self.two = root, two

    def path(self, rel: str) -> Path:
        base = assets_dir() / "ui"
        folder = base / ("common" if rel.startswith("common/") else THEMES[P.name])
        rel = rel.removeprefix("common/")
        stem = rel[:-4] if rel.endswith(".png") else rel
        for name in ([f"{stem}@2x.png"] if self.two else []) + [f"{stem}.png"]:
            if (folder / name).exists():
                return folder / name
        return folder / f"{stem}.png"

    def get(self, rel: str) -> tk.PhotoImage:
        if rel not in self._cache:
            self._cache[rel] = tk.PhotoImage(master=self.root, file=str(self.path(rel)))
        return self._cache[rel]


IMG = Images()


def load_fonts():
    """Press Start 2P and VT323 for this process only (FR_PRIVATE), before Tk starts."""
    try:
        for f in (assets_dir() / "fonts").glob("*.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)
    except Exception:
        log.exception("could not load the pixel fonts")


def dark_title_bar(win, on: bool):
    """Night: a dark window title bar and frame (DWMWA_USE_IMMERSIVE_DARK_MODE: 20, or 19 before Windows 10 20H1).
    Windows only repaints the frame when told to (SWP_FRAMECHANGED). Its own dialogs stay light."""
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        val = ctypes.c_int(1 if on else 0)
        dwm = ctypes.windll.dwmapi
        if dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(val), ctypes.sizeof(val)) != 0:
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 19, ctypes.byref(val), ctypes.sizeof(val))
        u32 = ctypes.windll.user32
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_uint]
        u32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        log.exception("could not set the title bar colour")
