"""Self-install: run from the unzipped folder, the exe offers to install itself per user (no admin) and points the
Start menu and desktop shortcuts and Claude desktop's MCP entry at the installed exe. A newer zip offers to update.

One folder per version, so an update never touches a folder something runs from (v1.9.10):

    %LOCALAPPDATA%\\Programs\\SubaDubaLingo\\
        2.0.0\\SubaDubaLingo.exe   Claude may still run the old MCP server from here; it says "restart Claude"
        2.0.1\\SubaDubaLingo.exe   new
        current.txt                 "2.0.1"

Before, an update renamed the one folder while Claude's MCP server ran from it; PyInstaller reopens the exe for each
first import, so the old server read the new exe at old offsets (zlib error -3). Old version folders are removed
once nothing runs from them (at every start); the current and the previous one stay. The installations under
the app's earlier names (Programs\\Videosprog up to v1.9, Programs\\Video undertekster before v1.7) are removed
the same way, and their shortcuts and Claude's entry are moved over.

Installing into AppData is fine here: the user starts the exe from Explorer, not from inside Claude (MSIX), so the
copy lands in the real folder. Data lives in %USERPROFILE%\\.subadubalingo and survives updates.
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

from .common import APP_NAME, APP_VERSION, NO_WINDOW, app_dir, data_dir, log, set_app_icon, set_app_id
from . import i18n
from .i18n import current, t

EXE_NAME = f"{APP_NAME}.exe"
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
    cfg = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
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
    for old in [base.with_name(base.name + ".old")] + old_install_dirs():
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
    # Skip only the top-level models folder (models next to the exe move to the data folder, so they are not
    # copied with every update); everything else is needed (_internal\base_library.zip is Python's standard
    # library - leaving out *.zip everywhere broke the installed exe in v1.7.1).
    shutil.copytree(src, staging, ignore=lambda d, names: list(MODEL_FOLDERS) if Path(d) == src else [])
    (staging / "version.txt").write_text(APP_VERSION, encoding="utf-8")
    for need in ((EXE_NAME,), ("_internal", "base_library.zip")):
        if (src.joinpath(*need)).exists() and not staging.joinpath(*need).exists():
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(t("installer.copy_incomplete", name=need[-1]))
    staging.rename(dst)
    tmp = base / (CURRENT + ".tmp")
    tmp.write_text(APP_VERSION, encoding="utf-8")
    tmp.replace(base / CURRENT)
    for models in (src / m for m in MODEL_FOLDERS):
        if models.is_dir():
            target = data_dir() / "models"
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
        point_claude_at(exe, add_if_missing=connect_claude)
    except Exception:
        log.exception("could not update Claude's config")
    # A server Claude still runs from a versioned folder under the earlier name (v1.9.10) reads current.txt next
    # to its folder: naming this version there makes it say "restart Claude" instead of serving an empty inbox.
    for old in old_install_dirs():
        if (old / CURRENT).exists():
            try:
                (old / CURRENT).write_text(APP_VERSION, encoding="utf-8")
            except OSError:
                pass
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
    from .theme import IMG
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
    from .theme import P
    msg = ttk.Label(f, text="", foreground=P.error, wraplength=460, justify="left")
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
            msg.configure(text=t("installer.app_open", app=APP_NAME))
            return
        try:
            exe = install(connect.get())
        except PermissionError:
            msg.configure(text=t("installer.in_use", app=APP_NAME))
            return
        except Exception as e:
            log.exception("install failed")
            msg.configure(text=t("installer.failed", error=e))
            return
        done = t("installer.done", version=APP_VERSION)
        if connect.get():
            done += "\n\n" + t("installer.restart_claude")
        messagebox.showinfo(APP_NAME, done, parent=root)
        subprocess.Popen([str(exe)], close_fds=True)
        result["go"] = False
        root.destroy()

    def skip():
        root.destroy()

    ttk.Button(b, text=t("installer.run_without"), command=skip).pack(side="left", padx=(0, 8))
    ttk.Button(b, text=btn, style="Accent.TButton", command=do_install).pack(side="left")
