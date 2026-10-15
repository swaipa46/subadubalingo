"""Test windows on the monitor the user is not working on (the one without the mouse pointer, else not the primary),
so a screenshot run does not cover their work."""
import ctypes
import ctypes.wintypes as w

from PIL import ImageGrab


class _MI(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


def monitors() -> list[tuple[bool, tuple[int, int, int, int]]]:
    """(is primary, work area) per monitor, in physical pixels."""
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(w.RECT), ctypes.c_double)

    def cb(h, dc, r, d):
        mi = _MI()
        mi.cbSize = ctypes.sizeof(_MI)
        ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(h), ctypes.byref(mi))
        a = mi.rcWork
        out.append((bool(mi.dwFlags & 1), (a.left, a.top, a.right, a.bottom)))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(None, None, proto(cb), 0)
    return out


def other_work_area() -> tuple[int, int, int, int] | None:
    pt = w.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    mons = monitors()
    free = [a for _, a in mons if not (a[0] <= pt.x < a[2] and a[1] <= pt.y < a[3])]
    free = free or [a for p, a in mons if not p]
    return free[0] if free else None


def place(root):
    """Move a Tk window to the other monitor, keeping its size (shrunk to fit), before it is shown."""
    area = other_work_area()
    if not area:
        return
    root.update_idletasks()
    left, top, right, bottom = area
    wdt = min(root.winfo_width() if root.winfo_width() > 1 else root.winfo_reqwidth(), right - left - 40)
    hgt = min(root.winfo_height() if root.winfo_height() > 1 else root.winfo_reqheight(), bottom - top - 80)
    root.state("normal")
    root.geometry(f"{wdt}x{hgt}+{left + 20}+{top + 20}")


def grab(bbox):
    """A screenshot of bbox (left, top, right, bottom) in screen coordinates, on any monitor (PIL takes screen
    coordinates also left of or above the primary monitor when all_screens is on)."""
    return ImageGrab.grab(bbox=bbox, all_screens=True)
