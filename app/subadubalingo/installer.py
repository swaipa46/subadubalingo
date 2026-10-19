"""Self-install: run from the unzipped folder, the exe offers to install itself per user (no admin) and points the
Start menu and desktop shortcuts and Claude desktop's MCP entry at the installed exe. A newer zip offers to update.

One folder per version, so an update never touches a folder something runs from (v1.9.10):

    %LOCALAPPDATA%\\Programs\\SubaDubaLingo\\
        2.0.0\\SubaDubaLingo.exe   Claude may still run the old MCP server from here; it says "restart Claude"
        2.0.1\\SubaDubaLingo.exe   new
        current.txt                 "2.0.1"
        SubaDubaLingo-mcp.exe       the MCP launcher (v2.1.1): runs current.txt's version with --mcp

Before, an update renamed the one folder while Claude's MCP server ran from it; PyInstaller reopens the exe for each
first import, so the old server read the new exe at old offsets (zlib error -3). Old version folders are removed
once nothing runs from them (at every start); the current and the previous one stay. The installations under
the app's earlier names (Programs\\Videosprog up to v1.9, Programs\\Video undertekster before v1.7) are removed
the same way, and their shortcuts and Claude's entry are moved over.

The launcher gives MCP clients other than Claude desktop (whose config the installer rewrites) a path that never
changes: activate() copies it from the version folder at every install and update (tools/mcp_launcher.cs).

Installing into AppData is fine here: the user starts the exe from Explorer, not from inside Claude (MSIX), so the
copy lands in the real folder. Data lives in %USERPROFILE%\\.subadubalingo and survives updates.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable
from tkinter import ttk

from .common import APP_NAME, APP_VERSION, NO_WINDOW, app_dir, data_dir, log, set_app_icon, set_app_id
from . import i18n
from .i18n import current, t
from .theme import IMG, P, PIXEL_FONT

EXE_NAME = f"{APP_NAME}.exe"
LAUNCHER_NAME = f"{APP_NAME}-mcp.exe"   # in the zip next to the exe; in the base folder once installed
INSTALL_FOLDER = APP_NAME
OLD_FOLDERS = ("Videosprog", "Video undertekster")   # the app's earlier names (v1.7-1.9, before v1.7)
OLD_SHORTCUTS = tuple(f"{n}.lnk" for n in OLD_FOLDERS)
OLD_MCP_NAMES = ("videosprog", "video-undertekster")
MCP_NAME = "subadubalingo"
CURRENT = "current.txt"
VERSION_DIR = re.compile(r"^\d+(\.\d+)+$")
FLAT_ITEMS = (EXE_NAME, "_internal", "version.txt")   # the layout up to v1.9.9
MODEL_FOLDERS = ("models", "modeller")   # next to the exe in the zip (modeller before v2.0)
# The user guide next to the exe in the zip, one per UI language (in docs/, copied by build.ps1)
# progress(step, fraction) during install(): the steps in order (the install window's status lines, T12). fraction is
# how far the step is (0..1), or None for "started". Optional everywhere; the --install CLI path passes none.
STEPS = ("start", "copy", "check", "models", "shortcuts", "claude", "cleanup", "final")
Progress = Callable[[str, "float | None"], None]
GUIDE_NAMES = {"da": f"Sådan bruger du {APP_NAME}.txt", "sv": f"Så använder du {APP_NAME}.txt",
               "no": f"Slik bruker du {APP_NAME}.txt", "en": f"How to use {APP_NAME}.txt"}


def guide_name(code: str | None = None) -> str:
    """The user guide's file name in the given UI language (default: the current one)."""
    return GUIDE_NAMES.get(code or current(), GUIDE_NAMES["en"])


def install_dir() -> Path:
    """The base folder; each version lives in a subfolder named after it."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / INSTALL_FOLDER


def old_install_dirs() -> list[Path]:
    return [install_dir().with_name(n) for n in OLD_FOLDERS]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def launcher_path() -> Path:
    """The MCP launcher's path, the same for every version (what the setups for other AI tools point at)."""
    return install_dir() / LAUNCHER_NAME


def update_launcher(version: str) -> bool:
    """Copy the launcher from the version folder to the base folder, when it is new or changed. A client may run the
    old one: Windows lets a running exe be renamed, not replaced, so it is moved aside (*.old-<time>, removed by
    cleanup once nothing runs it). Returns whether the base folder has the version's launcher."""
    src, dst = install_dir() / version / LAUNCHER_NAME, launcher_path()
    if not src.is_file():
        return False   # a build without it (before 2.1.1, or from source): leave what is there
    data = src.read_bytes()
    try:
        if dst.is_file() and dst.read_bytes() == data:
            return True
    except OSError:
        pass
    tmp = dst.with_name(LAUNCHER_NAME + ".tmp")
    tmp.write_bytes(data)
    try:
        tmp.replace(dst)
    except PermissionError:
        dst.rename(dst.with_name(f"{LAUNCHER_NAME}.old-{int(time.time())}"))
        tmp.replace(dst)
    log.info("MCP launcher updated from %s", version)
    return True


def installed_version() -> str | None:
    for d in [install_dir()] + old_install_dirs():
        v = _read(d / CURRENT) or _read(d / "version.txt")
        if v:
            return v
    return None


def installed_under_old_name() -> bool:
    return not _read(install_dir() / CURRENT) and any(d.is_dir() for d in old_install_dirs())


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
$bases = $env:SDL_BASES -split '\|' | Where-Object { $_ } | ForEach-Object { $_.ToLower().TrimEnd('\') + '\' }
foreach ($f in ($env:SDL_MAKE -split '\|' | Where-Object { $_ })) {
    $s = $sh.CreateShortcut($f); $s.TargetPath = $env:SDL_TARGET; $s.Save()
}
foreach ($d in ($env:SDL_DIRS -split '\|' | Where-Object { $_ })) {
    foreach ($l in Get-ChildItem -LiteralPath $d -Filter *.lnk) {
        $s = $sh.CreateShortcut($l.FullName)
        $t = $s.TargetPath.ToLower()
        if ($bases | Where-Object { $t.StartsWith($_) }) {
            $s.TargetPath = $env:SDL_TARGET; $s.WorkingDirectory = $env:SDL_DIR; $s.IconLocation = $env:SDL_TARGET
            $s.Save()
        }
    }
}
"""


def _update_shortcuts(exe: Path):
    """Create the Start menu and desktop shortcuts, and point every shortcut into the installation (also ones
    pinned to the taskbar, also from under an earlier name) at the new exe, so they keep working when the old
    folder is removed. A pin keeps its old label; that is Windows' business."""
    dirs = [d for d in _shortcut_dirs() if d.is_dir()]
    makers = [d for d in _shortcut_dirs()[:2] if d.is_dir()]
    for d in makers:
        for old_name in OLD_SHORTCUTS:
            (d / old_name).unlink(missing_ok=True)
    env = dict(os.environ, SDL_BASES="|".join(map(str, [install_dir()] + old_install_dirs())), SDL_TARGET=str(exe),
               SDL_DIR=str(exe.parent), SDL_MAKE="|".join(str(d / f"{APP_NAME}.lnk") for d in makers),
               SDL_DIRS="|".join(map(str, dirs)))
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SHORTCUTS_PS], env=env,
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if r.returncode:
        log.warning("shortcuts: %s", r.stderr.decode("utf-8", "replace")[:500])


def point_claude_at(exe: Path, args: list[str] | None = None, add_if_missing: bool = True) -> bool:
    """Update (or add) the MCP entry in Claude desktop's config to `exe`, replacing the entries under the app's
    earlier names. Keeps every other server, and a backup of the file next to it."""
    cfg = claude_config_path()
    data = {}
    if cfg.exists():
        raw = cfg.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
    servers = data.setdefault("mcpServers", {})
    had = any(n in servers for n in (MCP_NAME,) + OLD_MCP_NAMES)
    if not had and not add_if_missing:
        return False
    if not cfg.parent.exists():
        return False  # Claude desktop is not installed
    if cfg.exists():
        shutil.copyfile(cfg, cfg.with_name(cfg.name + f".{MCP_NAME}.bak"))
    for old in OLD_MCP_NAMES:
        servers.pop(old, None)
    servers[MCP_NAME] = {"command": str(exe), "args": args or ["--mcp"]}
    tmp = cfg.with_name(cfg.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg)
    return True


def claude_config_path() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"


def claude_desktop_running(images: list[str] | None = None) -> bool:
    """Is Claude desktop running? It keeps its config in memory and writes it back when it saves its preferences, so a
    change made while it runs can be overwritten (found 2026-10-16: the 2.1.0 install pointed Claude at the new exe at
    14:35, Claude wrote its old copy back at 15:23 and started the old Videosprog). Claude Code's claude.exe does not
    count: it does not use this file."""
    for img in images if images is not None else _process_images():
        if img.endswith(r"\claude.exe") and (r"\windowsapps\claude_" in img or "\\anthropicclaude\\" in img):
            return True
    return False


def claude_config_state(exe: Path) -> str:
    """"ok" (Claude points at exe, no entries under the earlier names), "stale" (it points elsewhere or still has an
    earlier name) or "none" (Claude desktop is not set up for the app, or not installed)."""
    cfg = claude_config_path()
    try:
        servers = json.loads(cfg.read_text(encoding="utf-8-sig") or "{}").get("mcpServers", {})
    except (OSError, ValueError):
        return "none"
    if not any(n in servers for n in (MCP_NAME,) + OLD_MCP_NAMES):
        return "none"
    entry = servers.get(MCP_NAME) or {}
    same = str(entry.get("command", "")).lower() == str(exe).lower()
    return "ok" if same and not any(n in servers for n in OLD_MCP_NAMES) else "stale"


def keep_claude_pointed(exe: Path, on_waiting=None, poll_s: float = 5.0, max_wait_s: float = 12 * 3600) -> threading.Thread | None:
    """At every start of the installed app: if Claude desktop's config is stale, fix it, but only while Claude desktop
    is not running (else it would write its old copy back). While it runs, a background thread waits for it to quit
    and fixes the config then; on_waiting() is called once so the app can ask the user to quit Claude."""
    if claude_config_state(exe) != "stale":
        return None
    if not claude_desktop_running():
        point_claude_at(exe, add_if_missing=False)
        log.info("Claude config pointed at %s", exe)
        return None
    if on_waiting:
        on_waiting()

    def wait():
        waited = 0.0
        while waited < max_wait_s:
            time.sleep(poll_s)
            waited += poll_s
            if not claude_desktop_running():
                time.sleep(2)   # let it finish writing its file
                if claude_config_state(exe) == "stale":
                    point_claude_at(exe, add_if_missing=False)
                    log.info("Claude config pointed at %s after Claude quit", exe)
                return
    t = threading.Thread(target=wait, daemon=True)
    t.start()
    return t


def cleanup() -> list[str]:
    """Remove version folders nothing runs from, except the current and the previous one and the newest one above
    the current (an update unpacked by update.stage, waiting for the restart), and leftovers (the flat layout,
    *.new/*.old, the folders from before). Returns what was removed."""
    base = install_dir()
    cur = _read(base / CURRENT)
    if not base.is_dir() or not cur:
        return []
    images = _process_images()
    versions = [d.name for d in base.iterdir() if d.is_dir() and VERSION_DIR.match(d.name)]
    previous = max((v for v in versions if _vtuple(v) < _vtuple(cur)), key=_vtuple, default=None)
    staged = max((v for v in versions if _vtuple(v) > _vtuple(cur)), key=_vtuple, default=None)   # self-update
    removed = []
    for d in base.iterdir():
        stale = d.is_dir() and ((VERSION_DIR.match(d.name) and d.name not in (cur, previous, staged))
                                or d.name.endswith((".new", ".old")))
        if stale and not running_from(d, images):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    if (base / EXE_NAME).exists() and not running_from(base, images, directly=True):
        for name in FLAT_ITEMS:
            p = base / name
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        removed.append("flat")
    for f in base.glob(LAUNCHER_NAME + ".old-*"):   # launchers moved aside while a client ran them
        try:
            f.unlink()
            removed.append(f.name)
        except OSError:
            pass
    for old in [base.with_name(base.name + ".old")] + old_install_dirs():
        if old.exists() and not running_from(old, images):
            shutil.rmtree(old, ignore_errors=True)
            removed.append(old.name)
    if removed:
        log.info("installer cleanup: removed %s", removed)
    return removed


def _report(progress: Progress | None, step: str, fraction: float | None = None):
    """Tell the install window how far it is; a failing callback never stops the install."""
    if progress:
        try:
            progress(step, fraction)
        except Exception:
            log.exception("install progress callback")


def _tree_size(src: Path, skip: tuple[str, ...] = ()) -> int:
    total = 0
    for d, dirs, files in os.walk(src):
        if Path(d) == src:
            dirs[:] = [x for x in dirs if x not in skip]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


def place(src: Path, version: str, progress: Progress | None = None) -> Path:
    """Copy the program folder src into its own version folder (via <version>.new, checked, then renamed), without
    making it the current one. Never touches a folder something runs from: raises PermissionError when that very
    version folder is in use. Used by install() and by the self-update (update.stage) for an unpacked release."""
    base = install_dir()
    dst = base / version
    base.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if running_from(dst):
            raise PermissionError(f"{dst} is in use")
        shutil.rmtree(dst)
    staging = base / f"{version}.new"
    shutil.rmtree(staging, ignore_errors=True)
    # Skip only the top-level models folder (models next to the exe move to the data folder, so they are not
    # copied with every update); everything else is needed (_internal\base_library.zip is Python's standard
    # library - leaving out *.zip everywhere broke the installed exe in v1.7.1).
    copy = shutil.copy2
    if progress:
        total, done = max(1, _tree_size(src, MODEL_FOLDERS)), 0
        _report(progress, "copy", 0.0)

        def copy(s, d, *, follow_symlinks=True):
            nonlocal done
            out = shutil.copy2(s, d, follow_symlinks=follow_symlinks)
            try:
                done += os.path.getsize(s)
            except OSError:
                pass
            _report(progress, "copy", min(1.0, done / total))
            return out
    shutil.copytree(src, staging, ignore=lambda d, names: list(MODEL_FOLDERS) if Path(d) == src else [],
                    copy_function=copy)
    _report(progress, "check")
    (staging / "version.txt").write_text(version, encoding="utf-8")
    for need in ((EXE_NAME,), ("_internal", "base_library.zip")):
        if (src.joinpath(*need)).exists() and not staging.joinpath(*need).exists():
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(t("installer.copy_incomplete", name=need[-1]))
    staging.rename(dst)
    return dst


def activate(version: str, connect_claude: bool, models_from: Path | None = None,
             progress: Progress | None = None) -> Path:
    """Make an installed version folder the current one: current.txt, the MCP launcher, the shortcuts and Claude's MCP
    entry point at its exe (a server Claude still runs from the old folder then says "restart Claude"), models next to the exe in
    models_from move to the data folder, and old folders nothing runs from are removed. Returns the exe."""
    base = install_dir()
    exe = base / version / EXE_NAME
    if not exe.is_file():
        raise FileNotFoundError(exe)
    _report(progress, "models")
    tmp = base / (CURRENT + ".tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(base / CURRENT)
    moves = [f for m in MODEL_FOLDERS if models_from and (models_from / m).is_dir()
             for f in (models_from / m).iterdir() if f.is_file()]
    for i, f in enumerate(moves):
        target = data_dir() / "models"
        target.mkdir(parents=True, exist_ok=True)
        if not (target / f.name).exists():
            shutil.copy2(f, target / f.name)
        _report(progress, "models", (i + 1) / len(moves))
    _report(progress, "shortcuts")
    try:
        update_launcher(version)
    except Exception:
        log.exception("could not update the MCP launcher")
    try:
        _update_shortcuts(exe)
    except Exception:
        log.exception("shortcuts failed")
    if connect_claude:
        _report(progress, "claude")
    try:
        point_claude_at(exe, add_if_missing=connect_claude)
    except Exception:
        log.exception("could not update Claude's config")
    # A server Claude still runs from a versioned folder under the earlier name (v1.9.10) reads current.txt next
    # to its folder: naming this version there makes it say "restart Claude" instead of serving an empty inbox.
    for old in old_install_dirs():
        if (old / CURRENT).exists():
            try:
                (old / CURRENT).write_text(version, encoding="utf-8")
            except OSError:
                pass
    log.info("current version is now %s (%s)", version, exe)
    _report(progress, "cleanup")
    try:
        cleanup()
    except Exception:
        log.exception("installer cleanup")
    return exe


def install(connect_claude: bool, progress: Progress | None = None) -> Path:
    """Copy this program folder to its own version folder and make it the current one. Never touches a folder
    something runs from, so it works while Claude runs the MCP server. Raises PermissionError only when this very
    version is installed and running (reinstalling it would mean replacing it). progress(step, fraction): see
    STEPS (the install window runs this on a worker thread and shows the steps)."""
    src = app_dir()
    _report(progress, "start")
    place(src, APP_VERSION, progress)
    exe = activate(APP_VERSION, connect_claude, models_from=src, progress=progress)
    _report(progress, "final")
    log.info("installed %s to %s", APP_VERSION, exe.parent)
    return exe


def offer_install() -> bool:
    """Run before the app: True means 'carry on starting this copy', False means 'exit' (installed one started)."""
    if not getattr(sys, "frozen", False) or running_installed():
        return True
    have = installed_version()
    if have and _vtuple(have) > _vtuple(APP_VERSION):
        return True  # an older zip: just run it
    set_app_id()
    settings = _read_settings()
    from . import skin, theme
    theme.load_fonts()
    root = tk.Tk()
    root.title(APP_NAME)
    set_app_icon(root)
    root.resizable(False, False)
    try:
        skin.apply(root, theme.resolve(settings.get("theme", "system")), root.winfo_fpixels("1i") / 96)
        skin.dialog(root)
    except Exception:
        log.exception("could not theme the install window")
    result = {"go": True}
    if not settings.get("ui_language"):
        # The very first start: the app's language before anything else (page 1/2), preselected from Windows
        _language_page(root, settings, lambda: _main_page(root, have, result, "2/2"))
    else:
        _main_page(root, have, result)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["go"]


def _read_settings() -> dict:
    try:
        return json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_ui_language(code: str):
    """Programmets sprog into the app's settings (gui.Settings reads it; other keys are kept)."""
    data = _read_settings()
    data["ui_language"] = code
    try:
        (data_dir() / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        log.exception("could not save the language")


def _brand(parent, step: str | None = None):
    """The logo as in the app's header, and the page number on the right."""
    top = ttk.Frame(parent)
    top.pack(fill="x", pady=(0, 16))
    try:
        ttk.Label(top, image=IMG.get("art/header-logo")).pack(side="left")
    except Exception:
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
    if step:
        ttk.Label(top, text=step, style="Pixel.TLabel").pack(side="right", anchor="n")


def _language_page(root, settings: dict, then):
    from .pickers import language_tiles
    f = ttk.Frame(root, padding=24)
    f.pack(fill="both", expand=True)
    _brand(f, "1/2")
    title = ttk.Label(f, text=t("pickers.first_title"), style="Section.TLabel")
    title.pack(anchor="w", pady=(0, 12))
    # Someone who used the app before 2.1 had it in Danish; a new user gets Windows' language
    choice = tk.StringVar(value=settings.get("ui_language") or ("da" if settings else i18n.windows_language()))
    hint = ttk.Label(f, style="Hint.TLabel", justify="left")
    buttons = ttk.Frame(f)
    go = ttk.Button(buttons, style="Accent.TButton")
    skip = ttk.Button(buttons, command=root.destroy)

    def show(code=None):
        # The page speaks the picked language at once, so the user sees what they chose
        code = choice.get()
        i18n.set_language(code)
        hint.configure(text=t("pickers.first_hint"))
        go.configure(text=t("pickers.continue"))
        skip.configure(text=t("installer.run_without"))

    def next_page():
        _save_ui_language(choice.get())
        f.destroy()
        then()

    language_tiles(f, choice, columns=2, on_change=show).pack(fill="x")
    hint.pack(anchor="w", pady=(10, 0))
    buttons.pack(anchor="e", pady=(24, 0))
    skip.pack(side="left", padx=(0, 8))
    go.configure(command=next_page)
    go.pack(side="left")
    show()
    go.focus_set()


def _main_page(root, have: str | None, result: dict, step: str | None = None):
    """Install, update or start the installed copy (page 2/2 on the first start)."""
    f = ttk.Frame(root, padding=24)
    f.pack(fill="both", expand=True)
    _brand(f, step)
    if have == APP_VERSION:
        page = "installed"
    elif have and installed_under_old_name():
        page = "renamed"
    elif have:
        page = "update"
    else:
        page = "install"
    title, text, btn = (t(f"installer.{page}.{part}", app=APP_NAME, version=APP_VERSION, have=have or "")
                        for part in ("title", "text", "button"))
    ttk.Label(f, text=title, style="Section.TLabel").pack(anchor="w")
    ttk.Label(f, text=text, wraplength=460, justify="left").pack(anchor="w", pady=(10, 14))
    connect = tk.BooleanVar(value=True)
    if have != APP_VERSION:
        ttk.Checkbutton(f, text=t("installer.connect_claude"), variable=connect).pack(anchor="w")
    b = ttk.Frame(f)
    b.pack(anchor="e", pady=(16, 0))

    def do_install():
        if have == APP_VERSION and current_exe():
            subprocess.Popen([str(current_exe())], close_fds=True)
            result["go"] = False
            root.destroy()
            return
        _run_install(root, have, result, connect.get())

    def skip():
        root.destroy()

    ttk.Button(b, text=t("installer.run_without"), command=skip).pack(side="left", padx=(0, 8))
    ttk.Button(b, text=btn, style="Accent.TButton", command=do_install).pack(side="left")


# The install window while it installs, and its done and error screens (design T12, assets/ui/README.md "Install
# window"). install() runs on a worker thread and reports its steps through a queue that the window polls, so the
# window never freezes; the title bar's close button does nothing until it is done.
LINE_LOOP = {"start": "cut", "copy": "rewind", "check": "cut", "models": "rewind", "shortcuts": "cut",
             "claude": "phone", "cleanup": "cut", "final": "rewind"}
LOOPS = {"cut": ("install-anim-{}", 8, 110), "rewind": ("install-anim-rewind-{}", 4, 140),
         "phone": ("install-anim-phone-{}", 4, 160), "cheer": ("install-anim-cheer-{}", 3, 180),
         "fail": ("install-anim-fail-{}", 2, 400)}
# Where each step starts and ends on the bar: the copy is most of the work
BAR_SPAN = {"start": (0.0, 0.02), "copy": (0.02, 0.80), "check": (0.80, 0.83), "models": (0.83, 0.87),
            "shortcuts": (0.87, 0.93), "claude": (0.93, 0.95), "cleanup": (0.95, 0.99), "final": (0.99, 1.0)}
MIN_LINE_S = 2.0   # each status line stays at least this long; a step done sooner is skipped
POLL_MS = 50
PAGE_WIDTH = 640   # the content's width at 100 %, as in the mockups


def reduced_motion() -> bool:
    """Whether Windows has animations turned off (SPI_GETCLIENTAREAANIMATION): the sprites then stand still."""
    try:
        on = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):
            return not on.value
    except Exception:
        pass
    return False


def _scale(root) -> float:
    return root.winfo_fpixels("1i") / 96


def _closable(root, on: bool):
    """The title bar's ✕ (and Alt+F4): greyed out and ignored while the copy runs; on the done and error screens it
    closes the window."""
    try:
        u32 = ctypes.windll.user32
        u32.GetSystemMenu.restype = ctypes.c_void_p
        u32.GetSystemMenu.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        u32.EnableMenuItem.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        menu = u32.GetSystemMenu(ctypes.c_void_p(int(root.wm_frame(), 16)), False)
        if menu:
            u32.EnableMenuItem(menu, 0xF060, 0 if on else 0x3)   # SC_CLOSE: MF_ENABLED / MF_GRAYED | MF_DISABLED
    except Exception:
        log.exception("could not change the close button")


class _Sprite(tk.Label):
    """One of the install animations (art/install-anim-*), looping; frame 0 still with reduced motion (cheer: 1)."""

    def __init__(self, parent):
        super().__init__(parent, borderwidth=0, highlightthickness=0, padx=0, pady=0, background=P.card)
        self.loop = None
        self._job = None

    def play(self, loop: str):
        if loop == self.loop:
            return
        self.stop()
        self.loop = loop
        pattern, n, ms = LOOPS[loop]
        frames = [IMG.get(f"art/{pattern.format(i)}") for i in range(n)]
        if reduced_motion():
            self.configure(image=frames[1 if loop == "cheer" else 0])
            return

        def tick(i=0):
            self.configure(image=frames[i % n])
            self._job = self.after(ms, tick, i + 1)
        tick()

    def stop(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None

    def destroy(self):
        self.stop()
        super().destroy()


class _Bar(ttk.Frame):
    """The segmented progress bar (gui.SegmentBar's drawing: progress-trough frame, 20 segments, the last filled one
    highlighted), without importing the app's window module. set(0..1)."""
    SEGMENTS = 20

    def __init__(self, parent):
        self.k = 2 if IMG.two else 1
        bl, bt, br, bb = P.nine_slice("progress-trough", IMG.two)
        pad = 4 * self.k
        super().__init__(parent, style="Trough.TFrame",
                         padding=(bl // 2 + pad, bt // 2 + pad, br // 2 + pad, bb // 2 + pad))
        self.canvas = tk.Canvas(self, height=22 * self.k, highlightthickness=0, borderwidth=0,
                                background=P.progress_trough)
        self.canvas.pack(fill="x")
        self.value = 0.0
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def set(self, value: float):
        self.value = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        k, w, h = self.k, c.winfo_width(), c.winfo_pixels(c.cget("height"))
        if w < 10:
            return
        gap = 3 * k
        seg = (w - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        filled = int(self.value * self.SEGMENTS + 0.5) if self.value < 1 else self.SEGMENTS
        filled = max(filled, 1 if self.value > 0 else 0)
        for i in range(filled):
            x0 = i * (seg + gap)
            colour = P.progress_fill_current if i == filled - 1 and filled < self.SEGMENTS else P.progress_fill
            c.create_rectangle(x0, 0, x0 + seg, h, fill=colour, outline="")


def _shadow_heading(parent, text: str, size: int, s: float) -> tk.Canvas:
    """LEVEL UP! / GAME OVER? in the pixel font with its 3 px shadow (A: pink on cyan, B: yellow on pink)."""
    fg, shadow = ("#d1186f", "#22e4ff") if P.name == "light" else ("#ffd400", "#ff2e88")
    k = 2 if IMG.two else 1
    off = 3 * k
    c = tk.Canvas(parent, highlightthickness=0, borderwidth=0, background=P.card)
    font = (PIXEL_FONT, -int(size * s))
    c.create_text(off, off, text=text, font=font, fill=shadow, anchor="nw", justify="left")
    c.create_text(0, 0, text=text, font=font, fill=fg, anchor="nw", justify="left")
    x0, y0, x1, y1 = c.bbox("all")
    c.configure(width=x1, height=y1)
    return c


def _page(root, title: str | None = None):
    """A new page in the install window: the old one goes, the header comes, and the window keeps its centre."""
    centre = root.winfo_x() + root.winfo_width() // 2 if root.winfo_ismapped() else None
    for w in root.winfo_children():
        w.destroy()
    root.unbind("<Return>")
    s = _scale(root)
    f = ttk.Frame(root, padding=24)
    f.pack(fill="both", expand=True)
    _brand(f)
    ttk.Frame(f, width=int(PAGE_WIDTH * s), height=1).pack(side="bottom")   # every page as wide as the mockups
    if title:
        ttk.Label(f, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 14))
    f.centre = centre
    return f


def _show_page(root, f):
    """Theme the page's card and let the window take its size, centred where it was."""
    from .skin import skin_cards
    skin_cards(f)
    root.geometry("")   # a size set from outside (tests) would keep the old page's size
    root.update_idletasks()
    if f.centre is not None:
        x = max(0, f.centre - root.winfo_reqwidth() // 2)
        root.geometry(f"+{x}+{root.winfo_y()}")


def _card(f, s: float):
    from . import skin
    card = skin.Card(f, padding=(int(36 * s), int(26 * s), int(36 * s), int(26 * s)))
    card.pack(fill="x")
    card.columnconfigure(1, weight=1)
    sprite = _Sprite(card)
    sprite.grid(row=0, column=0, rowspan=3, sticky="w", padx=(0, int(24 * s)))
    return card, sprite


def _run_install(root, have: str | None, result: dict, connect: bool):
    """Install on a worker thread, with the animation, the status lines and the bar; then the done or error screen."""
    if app_window_open():
        # The new one would only bring the old window forward (one window at a time), and it may be working
        _failed_page(root, have, result, connect, t("installer.app_open", app=APP_NAME), unchanged=True)
        return
    s = _scale(root)
    title = (t("installer.progress.title_update", version=APP_VERSION) if have else
             t("installer.progress.title_install", app=APP_NAME, version=APP_VERSION))
    f = _page(root, title)
    card, sprite = _card(f, s)
    wrap = int((PAGE_WIDTH - 72 - 160 - 24) * s)
    head = tk.Label(card, font=(PIXEL_FONT, -int(14 * s)), foreground=P.heading if P.name == "light" else P.accent,
                    background=P.card, justify="left", anchor="w", wraplength=wrap, borderwidth=0, padx=0, pady=0)
    head.grid(row=0, column=1, sticky="sew", pady=(0, int(8 * s)))
    text = tk.Label(card, font="SunValleyBodyFont", foreground=P.text, background=P.card, justify="left",
                    anchor="w", wraplength=wrap, borderwidth=0, padx=0, pady=0)
    text.grid(row=1, column=1, sticky="new")
    card.rowconfigure(0, weight=1)
    card.rowconfigure(1, weight=1)
    bar = _Bar(f)
    bar.pack(fill="x", pady=(int(24 * s), int(6 * s)))
    row = ttk.Frame(f)
    row.pack(fill="x")
    step_label = ttk.Label(row, style="Hint.TLabel")
    step_label.pack(side="left")
    pct = ttk.Label(row, style="Hint.TLabel")
    pct.pack(side="right")
    ttk.Label(f, text=t("installer.progress.hint"), style="Hint.TLabel", wraplength=int(PAGE_WIDTH * s),
              justify="left").pack(anchor="w", pady=(int(18 * s), 0))
    root.protocol("WM_DELETE_WINDOW", lambda: None)   # no closing mid-copy (Alt+F4 lands here too)
    _closable(root, False)

    q: queue.Queue = queue.Queue()
    state = {"step": "start", "shown": None, "since": 0.0, "value": 0.0, "end": None, "touched": False}

    def work():
        try:
            exe = install(connect, progress=lambda step, frac: q.put(("step", step, frac)))
            if not Path(exe).is_file():   # the final check: the installed exe is there
                raise FileNotFoundError(exe)
            q.put(("done", exe))
        except PermissionError:
            log.exception("install: in use")
            q.put(("error", t("installer.in_use", app=APP_NAME)))
        except BaseException as e:
            log.exception("install failed")
            q.put(("error", t("installer.failed", error=e)))

    def show(step):
        state["shown"], state["since"] = step, time.monotonic()
        head.configure(text=t(f"installer.line.{step}.head"))
        text.configure(text=t(f"installer.line.{step}.text"))
        sprite.play(LINE_LOOP[step])
        step_label.configure(text=t("installer.progress.step", n=STEPS.index(step) + 1, total=len(STEPS)))

    def poll():
        try:
            while True:
                msg = q.get_nowait()
                if msg[0] == "step" and msg[1] in BAR_SPAN:
                    _, step, frac = msg
                    state["step"] = step
                    state["touched"] |= STEPS.index(step) >= STEPS.index("models")   # activate() has begun
                    lo, hi = BAR_SPAN[step]
                    state["value"] = max(state["value"], lo + (hi - lo) * (frac or 0.0))
                elif msg[0] in ("done", "error"):
                    state["end"] = msg
        except queue.Empty:
            pass
        if state["end"] is None and not worker.is_alive() and q.empty():
            state["end"] = ("error", t("installer.fail.stopped", folder=data_dir()))   # never hang on a dead worker
        end = state["end"]
        if end and end[0] == "error":
            _failed_page(root, have, result, connect, end[1], unchanged=not state["touched"])
            return
        if end:
            state["value"] = 1.0
        bar.set(state["value"])
        pct.configure(text=f"{int(state['value'] * 100)}%")
        if time.monotonic() - state["since"] >= MIN_LINE_S:
            if end:
                _done_page(root, result, connect, end[1])
                return
            if state["step"] != state["shown"]:
                show(state["step"])   # the step running now; steps finished in the meantime are skipped
        root.after(POLL_MS, poll)

    show("start")   # always INSERT COIN first
    _show_page(root, f)
    worker = threading.Thread(target=work, name="install", daemon=True)
    worker.start()
    root.after(POLL_MS, poll)


def _close_with(root, result: dict):
    def close():
        result["go"] = False
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    _closable(root, True)
    return close


def _done_page(root, result: dict, connect: bool, exe: Path):
    """LEVEL UP!: replaces Windows' grey message box. The Claude note only when Claude was connected."""
    from .skin import IconButton
    s = _scale(root)
    f = _page(root)
    card, sprite = _card(f, s)
    sprite.play("cheer")
    _shadow_heading(card, t("installer.done.head", version=APP_VERSION), 26, s).grid(
        row=0, column=1, sticky="sw", pady=(0, int(10 * s)))
    ttk.Label(card, text=t("installer.done.title", version=APP_VERSION),
              font=("Segoe UI Semibold", -int(17 * s))).grid(row=1, column=1, sticky="w")
    ttk.Label(card, text=t("installer.done.text"), style="Hint.TLabel", wraplength=int(400 * s),
              justify="left").grid(row=2, column=1, sticky="nw", pady=(int(10 * s), 0))
    card.rowconfigure(0, weight=1)
    card.rowconfigure(2, weight=1)
    if connect:
        k = 2 if IMG.two else 1
        band = ttk.Frame(f, style="Callout.TFrame", padding=(int(24 * s), int(14 * s), int(24 * s) + 3 * k,
                                                             int(16 * s) + 3 * k))
        band.pack(fill="x", pady=(int(20 * s), 0))
        band.columnconfigure(1, weight=1)
        tk.Label(band, image=IMG.get("icons/arrow-right-selected"), background=P.callout_bg,
                 borderwidth=0).grid(row=0, column=0, sticky="nw", padx=(0, int(12 * s)), pady=(int(2 * s), 0))
        tk.Label(band, text=t("installer.restart.title"), font="SunValleyBodyStrongFont", background=P.callout_bg,
                 foreground=P.callout_text, borderwidth=0, padx=0, pady=0).grid(row=0, column=1, sticky="w")
        tk.Label(band, text=t("installer.restart.text"), font="SunValleyBodyFont", background=P.callout_bg,
                 foreground=P.callout_text, borderwidth=0, padx=0, pady=0, justify="left", anchor="w",
                 wraplength=int((PAGE_WIDTH - 90) * s)).grid(row=1, column=1, sticky="w")
    close = _close_with(root, result)

    def start():
        try:
            subprocess.Popen([str(exe)], close_fds=True)
        except OSError:
            log.exception("could not start %s", exe)
        close()

    b = ttk.Frame(f)
    b.pack(anchor="e", pady=(int(20 * s), 0))
    ttk.Button(b, text=t("installer.close"), command=close).pack(side="left", padx=(0, 12))
    go = IconButton(b, "play", text=t("installer.start", app=APP_NAME), style="Accent.TButton", command=start)
    go.pack(side="left")
    root.bind("<Return>", lambda e: start())
    go.focus_set()
    _show_page(root, f)


def _failed_page(root, have: str | None, result: dict, connect: bool, error: str, unchanged: bool):
    """GAME OVER? Nej, CONTINUE!: the real error and Prøv igen. "Nothing changed" only when it is true."""
    s = _scale(root)
    f = _page(root)
    card, sprite = _card(f, s)
    sprite.play("fail")
    _shadow_heading(card, t("installer.fail.head"), 18, s).grid(row=0, column=1, sticky="sw",
                                                                 pady=(0, int(10 * s)))
    hint = t("installer.fail.unchanged") if unchanged and have else t("installer.fail.incomplete")
    ttk.Label(card, text=hint, style="Hint.TLabel", wraplength=int(380 * s), justify="left").grid(
        row=1, column=1, sticky="nw")
    card.rowconfigure(0, weight=1)
    card.rowconfigure(1, weight=1)
    err = ttk.Frame(f)
    err.pack(fill="x", pady=(int(18 * s), 0))
    tk.Label(err, image=IMG.get("icons/step-error"), background=P.window, borderwidth=0).pack(
        side="left", anchor="n", padx=(0, int(12 * s)), pady=(int(2 * s), 0))
    ttk.Label(err, text=error, foreground=P.error, wraplength=int((PAGE_WIDTH - 40) * s), justify="left").pack(
        side="left", anchor="w", fill="x")
    close = _close_with(root, result)
    b = ttk.Frame(f)
    b.pack(anchor="e", pady=(int(28 * s), 0))
    ttk.Button(b, text=t("installer.close"), command=close).pack(side="left", padx=(0, 12))
    again = ttk.Button(b, text=t("installer.retry"), style="Accent.TButton",
                       command=lambda: _run_install(root, have, result, connect))
    again.pack(side="left")
    root.bind("<Return>", lambda e: _run_install(root, have, result, connect))
    again.focus_set()
    _show_page(root, f)
