"""Self-install: run from the unzipped folder, the exe offers to install itself per user (no admin) and points the
Start menu and desktop shortcuts and Claude desktop's MCP entry at the installed exe. A newer zip offers to update.

One folder per version, so an update never touches a folder something runs from (v1.9.10):

    %LOCALAPPDATA%\\Programs\\Videosprog\\
        1.9.9\\Videosprog.exe     Claude may still run the old MCP server from here; it says "restart Claude"
        1.9.10\\Videosprog.exe    new
        current.txt              "1.9.10"

Before, an update renamed the one folder while Claude's MCP server ran from it; PyInstaller reopens the exe for each
first import, so the old server read the new exe at old offsets (zlib error -3). Old version folders are removed
once nothing runs from them (at every start); the current and the previous one stay. The flat layout up to v1.9.9
(exe directly in Programs\\Videosprog) is removed the same way.

Installing into AppData is fine here: the user starts the exe from Explorer, not from inside Claude (MSIX), so the
copy lands in the real folder. Data lives in %USERPROFILE%\\.videosprog and survives updates. An installation from
before the rename (Programs\\Video undertekster) is removed.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .common import APP_NAME, APP_VERSION, NO_WINDOW, app_dir, data_dir, log

EXE_NAME = f"{APP_NAME}.exe"
INSTALL_FOLDER = APP_NAME
OLD_FOLDER = "Video undertekster"   # before the rename to Videosprog (v1.7)
OLD_SHORTCUTS = ("Video undertekster.lnk",)
CURRENT = "current.txt"
VERSION_DIR = re.compile(r"^\d+(\.\d+)+$")
FLAT_ITEMS = (EXE_NAME, "_internal", "version.txt")   # the layout up to v1.9.9


def install_dir() -> Path:
    """The base folder; each version lives in a subfolder named after it."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / INSTALL_FOLDER


def old_install_dir() -> Path:
    return install_dir().with_name(OLD_FOLDER)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def installed_version() -> str | None:
    return (_read(install_dir() / CURRENT) or _read(install_dir() / "version.txt")
            or _read(old_install_dir() / "version.txt"))


def current_exe() -> Path | None:
    """The installed exe to start (the current version, or the flat layout up to v1.9.9)."""
    v = _read(install_dir() / CURRENT)
    for exe in ([install_dir() / v / EXE_NAME] if v else []) + [install_dir() / EXE_NAME]:
        if exe.is_file():
            return exe
    return None


def _vtuple(v: str | None):
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


def running_installed() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    here, base = app_dir().resolve(), install_dir().resolve()
    return here == base or here.parent == base


def _process_images() -> list[str]:
    """Full exe paths (lower case) of all processes we may query, except this one."""
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
                                               ctypes.POINTER(ctypes.c_ulong)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    pids = (ctypes.c_ulong * 4096)()
    got = ctypes.c_ulong()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got)):
        return []
    out = []
    for pid in pids[:got.value // ctypes.sizeof(ctypes.c_ulong)]:
        if not pid or pid == os.getpid():
            continue
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out.append(buf.value.lower())
        finally:
            k32.CloseHandle(h)
    return out


def running_from(folder: Path, images: list[str] | None = None, directly: bool = False) -> bool:
    """Whether a process (the app, or Claude's MCP server) runs from an exe in `folder` (`directly`: not from a
    subfolder of it). Windows lets such a folder be renamed or partly deleted, which breaks the process."""
    key = str(folder.resolve()).lower().rstrip("\\") + "\\"
    for img in _process_images() if images is None else images:
        if img.startswith(key) and (not directly or "\\" not in img[len(key):]):
            return True
    return False


def app_window_open() -> bool:
    """Whether the app's main window is open in another process (not this installer's own window)."""
    try:
        u32 = ctypes.windll.user32
        u32.FindWindowExW.restype = ctypes.c_void_p
        u32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        hwnd = None
        while True:
            hwnd = u32.FindWindowExW(None, hwnd, "TkTopLevel", APP_NAME)
            if not hwnd:
                return False
            pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                return True
    except Exception:
        return False


def _shortcut_dirs() -> list[Path]:
    """Start menu, desktop (both get a shortcut), and the taskbar pins (copies of a shortcut, updated only)."""
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    return [appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs", Path.home() / "Desktop",
            appdata / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"]


SHORTCUTS_PS = r"""
$sh = New-Object -ComObject WScript.Shell
$base = $env:VU_BASE.ToLower().TrimEnd('\') + '\'
foreach ($f in ($env:VU_MAKE -split '\|' | Where-Object { $_ })) {
    $s = $sh.CreateShortcut($f); $s.TargetPath = $env:VU_TARGET; $s.Save()
}
foreach ($d in ($env:VU_DIRS -split '\|' | Where-Object { $_ })) {
    foreach ($l in Get-ChildItem -LiteralPath $d -Filter *.lnk) {
        $s = $sh.CreateShortcut($l.FullName)
        if ($s.TargetPath.ToLower().StartsWith($base)) {
            $s.TargetPath = $env:VU_TARGET; $s.WorkingDirectory = $env:VU_DIR; $s.IconLocation = $env:VU_TARGET
            $s.Save()
        }
    }
}
"""


def _update_shortcuts(exe: Path):
    """Create the Start menu and desktop shortcuts, and point every shortcut into the installation (also ones
    pinned to the taskbar) at the new exe, so they keep working when the old version folder is removed."""
    dirs = [d for d in _shortcut_dirs() if d.is_dir()]
    makers = [d for d in _shortcut_dirs()[:2] if d.is_dir()]
    for d in makers:
        for old_name in OLD_SHORTCUTS:
            (d / old_name).unlink(missing_ok=True)
    env = dict(os.environ, VU_BASE=str(install_dir()), VU_TARGET=str(exe), VU_DIR=str(exe.parent),
               VU_MAKE="|".join(str(d / f"{APP_NAME}.lnk") for d in makers), VU_DIRS="|".join(map(str, dirs)))
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SHORTCUTS_PS], env=env,
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if r.returncode:
        log.warning("shortcuts: %s", r.stderr.decode("utf-8", "replace")[:500])


def _point_claude_at(exe: Path, add_if_missing: bool) -> bool:
    """Update (or add) the MCP entry in Claude desktop's config to the installed exe."""
    cfg = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    data = {}
    if cfg.exists():
        raw = cfg.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
    servers = data.setdefault("mcpServers", {})
    had = "videosprog" in servers or "video-undertekster" in servers
    if not had and not add_if_missing:
        return False
    if not cfg.parent.exists():
        return False  # Claude desktop is not installed
    if cfg.exists():
        shutil.copyfile(cfg, cfg.with_name(cfg.name + ".videosprog.bak"))
    servers.pop("video-undertekster", None)   # the name before the rename
    servers["videosprog"] = {"command": str(exe), "args": ["--mcp"]}
    tmp = cfg.with_name(cfg.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg)
    return True


def cleanup() -> list[str]:
    """Remove version folders nothing runs from, except the current and the previous one, and leftovers (the flat
    layout, *.new/*.old, the folders from before). Returns what was removed."""
    base = install_dir()
    cur = _read(base / CURRENT)
    if not base.is_dir() or not cur:
        return []
    images = _process_images()
    versions = [d.name for d in base.iterdir() if d.is_dir() and VERSION_DIR.match(d.name)]
    previous = max((v for v in versions if _vtuple(v) < _vtuple(cur)), key=_vtuple, default=None)
    removed = []
    for d in base.iterdir():
        stale = d.is_dir() and ((VERSION_DIR.match(d.name) and d.name not in (cur, previous))
                                or d.name.endswith((".new", ".old")))
        if stale and not running_from(d, images):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    if (base / EXE_NAME).exists() and not running_from(base, images, directly=True):
        for name in FLAT_ITEMS:
            p = base / name
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        removed.append("flat")
    for old in (base.with_name(base.name + ".old"), old_install_dir()):
        if old.exists() and not running_from(old, images):
            shutil.rmtree(old, ignore_errors=True)
            removed.append(old.name)
    if removed:
        log.info("installer cleanup: removed %s", removed)
    return removed


def install(connect_claude: bool) -> Path:
    """Copy this program folder to its own version folder and make it the current one. Never touches a folder
    something runs from, so it works while Claude runs the MCP server. Raises PermissionError only when this very
    version is installed and running (reinstalling it would mean replacing it)."""
    src, base = app_dir(), install_dir()
    dst = base / APP_VERSION
    base.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if running_from(dst):
            raise PermissionError(f"{dst} is in use")
        shutil.rmtree(dst)
    staging = base / f"{APP_VERSION}.new"
    shutil.rmtree(staging, ignore_errors=True)
    # Models next to the exe move to the data folder, so they are not copied with every update
    models = src / "modeller"
    # Skip only the top-level models folder; everything else is needed (_internal\base_library.zip is Python's
    # standard library - leaving out *.zip everywhere broke the installed exe in v1.7.1).
    shutil.copytree(src, staging, ignore=lambda d, names: ["modeller"] if Path(d) == src else [])
    (staging / "version.txt").write_text(APP_VERSION, encoding="utf-8")
    for need in ((EXE_NAME,), ("_internal", "base_library.zip")):
        if (src.joinpath(*need)).exists() and not staging.joinpath(*need).exists():
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(f"Kopieringen er ufuldstændig ({need[-1]} mangler).")
    staging.rename(dst)
    tmp = base / (CURRENT + ".tmp")
    tmp.write_text(APP_VERSION, encoding="utf-8")
    tmp.replace(base / CURRENT)
    if models.is_dir():
        target = data_dir() / "modeller"
        target.mkdir(parents=True, exist_ok=True)
        for f in models.iterdir():
            if f.is_file() and not (target / f.name).exists():
                shutil.copy2(f, target / f.name)
    exe = dst / EXE_NAME
    try:
        _update_shortcuts(exe)
    except Exception:
        log.exception("shortcuts failed")
    try:
        _point_claude_at(exe, add_if_missing=connect_claude)
    except Exception:
        log.exception("could not update Claude's config")
    log.info("installed %s to %s", APP_VERSION, dst)
    try:
        cleanup()
    except Exception:
        log.exception("installer cleanup")
    return exe


def offer_install() -> bool:
    """Run before the app: True means 'carry on starting this copy', False means 'exit' (installed one started)."""
    if not getattr(sys, "frozen", False) or running_installed():
        return True
    have = installed_version()
    if have and _vtuple(have) > _vtuple(APP_VERSION):
        return True  # an older zip: just run it
    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    try:
        import sv_ttk
        sv_ttk.set_theme("light", root)
    except Exception:
        pass
    result = {"go": True}
    f = ttk.Frame(root, padding=24)
    f.pack(fill="both", expand=True)
    if have == APP_VERSION:
        title, text, btn = (f"{APP_NAME} er allerede installeret",
                            "Denne version er allerede installeret. Vil du starte den installerede?", "Start den installerede")
    elif have:
        title, text, btn = (f"Opdatér til version {APP_VERSION}?",
                            f"Version {have} er installeret. Vil du opdatere den til {APP_VERSION}?\n"
                            "Dine indstillinger, ordliste og hentede filer bliver bevaret.", "Opdatér")
    else:
        title, text, btn = (f"Installér {APP_NAME}?",
                            "Programmet bliver lagt i din egen programmappe (det kræver ikke administrator), "
                            "og der kommer en genvej i Start og på skrivebordet.\n\n"
                            "Bagefter kan du slette den mappe, du har pakket ud.", "Installér")
    ttk.Label(f, text=title, font=("Segoe UI Variable Display Semibold", 16)).pack(anchor="w")
    ttk.Label(f, text=text, wraplength=460, justify="left").pack(anchor="w", pady=(10, 14))
    connect = tk.BooleanVar(value=True)
    if have != APP_VERSION:
        ttk.Checkbutton(f, text="Forbind med Claude, så du kan bede Claude om at lave videoer", variable=connect).pack(anchor="w")
    msg = ttk.Label(f, text="", foreground="#c42b1c", wraplength=460, justify="left")
    msg.pack(anchor="w", pady=(8, 0))
    b = ttk.Frame(f)
    b.pack(anchor="e", pady=(16, 0))

    def do_install():
        if have == APP_VERSION and current_exe():
            subprocess.Popen([str(current_exe())], close_fds=True)
            result["go"] = False
            root.destroy()
            return
        if app_window_open():
            # The new one would only bring the old window forward (one window at a time), and it may be working
            msg.configure(text=f"{APP_NAME} er åben. Luk den først (vent, hvis den er i gang med en video), "
                               "og tryk så igen.")
            return
        try:
            exe = install(connect.get())
        except PermissionError:
            msg.configure(text=f"Programmet er i brug. Luk {APP_NAME}, og luk Claude helt (højreklik på "
                               "Claude-ikonet ved uret -> Quit). Tryk så igen.")
            return
        except Exception as e:
            log.exception("install failed")
            msg.configure(text=f"Det lykkedes ikke: {e}")
            return
        extra = ("\n\nGenstart Claude (Quit og åbn igen), så Claude bruger den nye version." if connect.get() else "")
        messagebox.showinfo(APP_NAME, f"Version {APP_VERSION} er installeret.{extra}", parent=root)
        subprocess.Popen([str(exe)], close_fds=True)
        result["go"] = False
        root.destroy()

    def skip():
        root.destroy()

    ttk.Button(b, text="Kør uden at installere", command=skip).pack(side="left", padx=(0, 8))
    ttk.Button(b, text=btn, style="Accent.TButton", command=do_install).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", skip)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["go"]
