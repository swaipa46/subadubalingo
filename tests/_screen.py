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


def offscreen_area() -> tuple[int, int, int, int]:
    """A work area right of every monitor: windows there are invisible but PrintWindow still draws them."""
    u32 = ctypes.windll.user32
    right = u32.GetSystemMetrics(76) + u32.GetSystemMetrics(78)   # SM_XVIRTUALSCREEN + SM_CXVIRTUALSCREEN
    return right + 100, 0, right + 100 + 2000, 1200


def place(root):
    """Move a Tk window to the monitor the user is not working on, keeping its size (shrunk to fit), before it is shown."""
    # On the monitor without the mouse pointer (Tk does not paint windows outside every monitor); the dev venv's
    # sitecustomize.py also makes test windows fully transparent and click-through, so they are never in view
    area = other_work_area() or monitors()[0][1]
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


def grab_window(hwnd: int):
    """A picture of one window drawn by the window itself (PrintWindow), so it also works while the screen is locked
    or the window is covered. Falls back to a screen grab of its rectangle."""
    from PIL import Image
    u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    r = w.RECT()
    u32.GetWindowRect(w.HWND(hwnd), ctypes.byref(r))
    wd, ht = r.right - r.left, r.bottom - r.top
    hdc = u32.GetWindowDC(w.HWND(hwnd))
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, wd, ht)
    gdi.SelectObject(mdc, bmp)
    ok = u32.PrintWindow(w.HWND(hwnd), mdc, 2)   # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                    ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                    ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                    ("biClrImportant", w.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), wd, -ht, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(wd * ht * 4)
    gdi.GetDIBits(mdc, bmp, 0, ht, buf, ctypes.byref(bih), 0)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mdc)
    u32.ReleaseDC(w.HWND(hwnd), hdc)
    if not ok:
        return grab((r.left, r.top, r.right, r.bottom))
    return Image.frombuffer("RGB", (wd, ht), buf, "raw", "BGRX", 0, 1)
