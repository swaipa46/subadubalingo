"""A Windows notification by the clock (a toast on Windows 10/11) when the run waits for the user: the app is often
behind Outlook or Teams then (2026-10-31: Anne missed her turn). It is the notification-area icon's balloon
(Shell_NotifyIcon with NIF_INFO), which Windows shows as a toast without a Start-menu shortcut or an app id of its
own. The icon goes away again after a while, so nothing stays in the notification area. Silent: the window's own
sound (App._flash) is the one sound. Anything that fails is ignored: the window still flashes."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .common import APP_NAME, APP_USER_MODEL_ID, branding_dir, log

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x2, 0x4, 0x10
NIIF_USER, NIIF_NOSOUND, NIIF_LARGE_ICON = 0x4, 0x10, 0x20
UID = 0x5DB1
SHOWN_MS = 30_000


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", _GUID), ("hBalloonIcon", wintypes.HICON)]


_icons: dict[int, int] = {}
_named = []


def _name_app_id():
    """Windows heads the toast with the process's app id (common.set_app_id), "SubaDubaLingo.App", unless the id
    has a display name: give it the app's name and icon, under the user's own registry key (no admin needed)."""
    if _named:
        return
    _named.append(True)
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Classes\AppUserModelId\{APP_USER_MODEL_ID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(branding_dir() / "icon-128.png"))
    except Exception:
        log.exception("could not name the app id for notifications")
_shown = {"hwnd": None, "after": None}


def _icon(size: int) -> int:
    if size not in _icons:
        _icons[size] = ctypes.windll.user32.LoadImageW(None, str(branding_dir() / "SubaDubaLingo.ico"), 1, size,
                                                       size, 0x10) or 0   # IMAGE_ICON, LR_LOADFROMFILE
    return _icons[size]


def _data(hwnd: int) -> NOTIFYICONDATAW:
    d = NOTIFYICONDATAW()
    d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    d.hWnd = hwnd
    d.uID = UID
    return d


def show(root, hwnd: int, title: str, text: str):
    """Show `title` and `text` by the clock; `hwnd` is the main window's frame (the icon's owner)."""
    try:
        _name_app_id()
        shell = ctypes.windll.shell32
        if _shown["after"] is not None:
            root.after_cancel(_shown["after"])
            _shown["after"] = None
        hide()
        d = _data(hwnd)
        d.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
        d.hIcon = _icon(16)
        d.szTip = APP_NAME
        d.szInfoTitle = title[:63]
        d.szInfo = text[:255]
        d.dwInfoFlags = NIIF_USER | NIIF_NOSOUND | NIIF_LARGE_ICON
        d.hBalloonIcon = _icon(32)
        if not shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(d)):
            shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))
        _shown["hwnd"] = hwnd
        _shown["after"] = root.after(SHOWN_MS, hide)
    except Exception:
        log.exception("toast")


def hide():
    """Take the icon (and with it a toast still showing) away."""
    hwnd, _shown["hwnd"] = _shown["hwnd"], None
    if hwnd is None:
        return
    try:
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(_data(hwnd)))
    except Exception:
        pass
