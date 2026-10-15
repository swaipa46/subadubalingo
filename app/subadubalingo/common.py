"""Shared helpers: paths, languages, logging, subprocess handling and SRT parsing."""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "SubaDubaLingo"
APP_TAGLINE = "Undertekster og dubbing af videoer"
APP_VERSION = "2.0.0"

# --- Paths -------------------------------------------------------------------

def app_dir() -> Path:
    """Folder that holds the exe (or the repo's app/ folder when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def bin_dir() -> Path:
    """Bundled tools (whisper, piper, ffmpeg)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir())) / "bin"
    return Path(__file__).resolve().parent.parent.parent / "bin"


def legacy_data_dir() -> Path:
    """Before v1.7: %LOCALAPPDATA%\\VideoUndertekster (settings and models)."""
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "VideoUndertekster"


def previous_data_dirs() -> list[Path]:
    """The data folder under the app's earlier names, newest first: Videosprog (v1.7-1.9), Video undertekster."""
    return [Path.home() / ".videosprog", Path.home() / ".video-undertekster"]


# Files in the data folder that had Danish names before v2.0
RENAMED = {"rettelser.txt": "corrections.txt", "tekster.json": "text_state.json", "modeller": "models"}
SMALL_FILES = ("settings.json", "corrections.txt", "text_state.json")

_DATA_DIR: Path | None = None


def _old_name(name: str) -> str:
    return next((old for old, new in RENAMED.items() if new == name), name)


def _rename_items(d: Path):
    for old, new in RENAMED.items():
        if (d / old).exists() and not (d / new).exists():
            try:
                (d / old).rename(d / new)
            except OSError:
                pass


def data_dir() -> Path:
    """%USERPROFILE%\\.subadubalingo (hidden), not AppData: Claude desktop is an MSIX app, and everything a
    process it starts (our MCP server, and the app when the server starts it) writes under AppData lands in
    Claude's own package folder instead. The app started by the user would then not see Claude's jobs, settings or
    downloaded models. The user profile root is not redirected.

    The first start after a rename moves the previous folder here (same drive, so instant, models included). If
    it is in use (Claude still runs the old MCP server, which keeps its log open), the small files are copied
    and the models stay where they are until a later start (adopt_previous_models)."""
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR
    d = Path.home() / ".subadubalingo"
    if not d.exists():
        previous = next((x for x in previous_data_dirs() if x.is_dir()), None)
        if previous:
            try:
                previous.rename(d)
            except OSError:
                pass   # in use: the small files are copied below
    created = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    if created:
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(d), 0x2)  # hidden, keeps the profile folder tidy
        except Exception:
            pass
    _rename_items(d)
    # Small files the folder lacks come from the previous folders (newest first), also when it already existed
    # (an earlier start that found nothing, e.g. run from the zip first), and from before v1.7
    for previous in previous_data_dirs() + [legacy_data_dir()]:
        for name in SMALL_FILES:
            src = next((f for f in (previous / name, previous / _old_name(name)) if f.is_file()), None)
            if src and not (d / name).exists():
                try:
                    (d / name).write_bytes(src.read_bytes())
                except OSError:
                    pass
    _DATA_DIR = d
    return d


def adopt_previous_models() -> list[str]:
    """Move model files left in a previous data folder (it was in use at the rename) into ours, and remove that
    folder. Cheap: a rename on the same drive. Called at start, in the background. The rest of the old folder
    (old jobs and log, a few KB) is left alone."""
    target = data_dir() / "models"
    moved = []
    for previous in previous_data_dirs():
        for folder in (previous / "modeller", previous / "models"):
            if not folder.is_dir():
                continue
            target.mkdir(parents=True, exist_ok=True)
            for f in folder.iterdir():
                try:
                    if f.is_file() and not (target / f.name).exists():
                        f.rename(target / f.name)
                        moved.append(f.name)
                except OSError:
                    pass
    return moved


def model_dirs() -> list[Path]:
    """Where models are looked for, in order. The first is where downloads go."""
    out = [data_dir() / "models", app_dir() / "models", app_dir() / "modeller"]
    for previous in previous_data_dirs():
        out += [previous / "modeller", previous / "models"]
    return out + [legacy_data_dir() / "modeller"]


WHISPER = lambda: bin_dir() / "whisper" / "whisper-cli.exe"
VAD_MODEL = lambda: bin_dir() / "whisper" / "ggml-silero-v6.2.0.bin"
PIPER = lambda: bin_dir() / "piper" / "piper.exe"
FFMPEG = lambda: bin_dir() / "ffmpeg" / "ffmpeg.exe"

# --- Languages ---------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    name: str        # "Dansk" (shown in the GUI)
    code: str        # Whisper language code
    plural: str      # "danske" -> "danske undertekster"
    singular: str    # "dansk" -> "dansk dub"
    voice: str       # Piper voice file stem


LANGUAGES = [
    Language("Dansk", "da", "danske", "dansk", "da_DK-talesyntese-medium"),
    Language("Svensk", "sv", "svenske", "svensk", "sv_SE-nst-medium"),
    Language("Norsk", "no", "norske", "norsk", "no_NO-talesyntese-medium"),
    Language("Engelsk", "en", "engelske", "engelsk", "en_US-lessac-medium"),
]
LANG_BY_NAME = {l.name: l for l in LANGUAGES}

# --- Output file names (next to the original, never overwriting it) ----------

MAX_PATH_CHARS = 240   # Windows' limit is 260; ffmpeg and Explorer fail above it
TEAMS_SUFFIX = re.compile(r"-\d{8}_\d{6}-Meeting Recording$|-Meeting Recording$", re.I)


def output_path(video: Path, tail: str) -> Path:
    """`<video name> - <tail>` next to the video. Teams recordings have long names in deep OneDrive folders,
    so the name is shortened when the full path would get too long: first the Teams date/"Meeting Recording"
    suffix, then the end of the name."""
    stem = video.stem
    path = video.with_name(f"{stem} - {tail}")
    if len(str(path)) <= MAX_PATH_CHARS:
        return path
    stem = TEAMS_SUFFIX.sub("", stem).rstrip(" -_")
    room = MAX_PATH_CHARS - len(str(video.parent)) - len(f"\\ - {tail}")
    if len(stem) > room:
        stem = stem[:max(12, room)].rstrip(" -_")
    return video.with_name(f"{stem} - {tail}")


def srt_name(video: Path, lang: Language) -> Path:
    return output_path(video, f"{lang.plural} undertekster.srt")


def burned_name(video: Path, lang: Language) -> Path:
    return output_path(video, f"med {lang.plural} undertekster.mp4")


def dub_name(video: Path, lang: Language, subs: Language | None) -> Path:
    if subs:
        return output_path(video, f"{lang.singular} dub med {subs.plural} undertekster.mp4")
    return output_path(video, f"{lang.singular} dub.mp4")


def cut_name(video: Path) -> Path:
    return output_path(video, "klippet.mp4")


def guess_srt_language(path: Path) -> Language | None:
    name = path.name.lower()
    for l in LANGUAGES:
        if f"{l.plural} undertekster" in name:
            return l
    return None

# --- Logging -----------------------------------------------------------------

def log_file() -> Path:
    return data_dir() / "log.txt"


def setup_logging() -> logging.Logger:
    log = logging.getLogger("subadubalingo")
    if not log.handlers:
        log.setLevel(logging.INFO)
        f = log_file()
        if f.exists() and f.stat().st_size > 5_000_000:
            f.replace(f.with_suffix(".old.txt"))
        h = logging.FileHandler(f, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    return log


log = setup_logging()

# --- Downloads folder and file clipboard ----------------------------------------

def downloads_dir() -> Path:
    """The user's Downloads folder, also when it has been moved (OneDrive, another drive)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
        folder_id = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:
        log.exception("could not look up the Downloads folder")
    return Path.home() / "Downloads"


def copy_file_to_clipboard(path: Path) -> bool:
    """Put a file on the clipboard as a file (as Explorer's Copy does), so Ctrl+V in Claude attaches it."""
    ps = f"Set-Clipboard -LiteralPath '{str(path).replace(chr(39), chr(39) * 2)}'"
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=20, creationflags=NO_WINDOW)
        if r.returncode != 0:
            log.warning("Set-Clipboard failed: %s", r.stderr.decode("utf-8", "replace")[:300])
        return r.returncode == 0
    except Exception:
        log.exception("could not put the file on the clipboard")
        return False

# --- Machine info --------------------------------------------------------------

def free_memory_gb() -> float | None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullAvailPhys / 1024 ** 3
    except Exception:
        return None

# --- Processes -----------------------------------------------------------------

class Cancelled(Exception):
    pass


class ToolError(Exception):
    """A tool failed. The message is shown to the user; details go to the log."""


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Tools run below normal priority so Teams, Outlook and the browser stay responsive.
TOOL_FLAGS = NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
PROCESS_SUSPEND_RESUME = 0x0800


def _suspend(pid: int, resume: bool):
    """Freeze or thaw a process (NtSuspendProcess): it stops using CPU/GPU and continues where it was."""
    try:
        k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        h = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if h:
            (nt.NtResumeProcess if resume else nt.NtSuspendProcess)(h)
            k32.CloseHandle(h)
    except Exception:
        log.exception("suspend/resume failed")


class Runner:
    """Runs one external process at a time; another thread may pause, resume or cancel it."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._running = threading.Event()
        self._running.set()
        self._paused_since: float | None = None
        self._paused_total = 0.0
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def check(self):
        """Raise if cancelled; block while paused (used between steps and in download loops)."""
        while not self._running.wait(0.2):
            pass
        if self.cancelled.is_set():
            raise Cancelled()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def paused_seconds(self) -> float:
        with self._lock:
            extra = time.time() - self._paused_since if self._paused_since else 0.0
            return self._paused_total + extra

    def pause(self):
        with self._lock:
            if self._paused_since:
                return
            self._paused_since = time.time()
            self._running.clear()
            p = self._proc
            if p and p.poll() is None:
                _suspend(p.pid, resume=False)
        log.info("paused")

    def resume(self):
        with self._lock:
            if not self._paused_since:
                return
            p = self._proc
            if p and p.poll() is None:
                _suspend(p.pid, resume=True)
            self._paused_total += time.time() - self._paused_since
            self._paused_since = None
            self._running.set()
        log.info("resumed")

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            p = self._proc
            self._paused_since = None
            self._running.set()
        if p and p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, creationflags=NO_WINDOW)

    def run(self, args, on_line=None, cwd=None, stdin_text: str | None = None, quiet: bool = False) -> tuple[int, list[str]]:
        """Run a process, feed each output line (stdout+stderr) to on_line, return (code, last lines)."""
        self.check()
        args = [str(a) for a in args]
        log.info("RUN %s", subprocess.list2cmdline(args))
        with self._lock:
            p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                 creationflags=TOOL_FLAGS)
            self._proc = p
            if self._paused_since:  # paused between check() and here
                _suspend(p.pid, resume=False)
        if stdin_text is not None:
            def feed():
                try:
                    p.stdin.write(stdin_text.encode("utf-8"))
                    p.stdin.close()
                except OSError:
                    pass
            threading.Thread(target=feed, daemon=True).start()
        tail: list[str] = []
        buf = b""
        # ffmpeg ends progress lines with \r, so split on both.
        while True:
            chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                tail.append(line)
                if len(tail) > 60:
                    del tail[:20]
                if on_line:
                    on_line(line)
        code = p.wait()
        with self._lock:
            self._proc = None
        if self.cancelled.is_set():
            raise Cancelled()
        if code != 0 and not quiet:
            log.warning("exit %s; last output:\n%s", code, "\n".join(tail[-25:]))
        return code, tail

# --- SRT -----------------------------------------------------------------------

@dataclass
class Cue:
    start: int   # ms
    end: int     # ms
    text: str


TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse_srt_text(text: str) -> list[Cue]:
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = TIME_RE.search(lines[i])
        if not m:
            i += 1
            continue
        g = [int(x) for x in m.groups()]
        start = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]
        end = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
        i += 1
        body = []
        while i < len(lines) and lines[i].strip():
            # A number line followed by a time line is the next block, even without a blank line.
            if lines[i].strip().isdigit() and i + 1 < len(lines) and TIME_RE.search(lines[i + 1]):
                break
            body.append(lines[i].strip())
            i += 1
        cues.append(Cue(start, end, "\n".join(body)))
    return cues


def read_srt(path: Path) -> list[Cue]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return parse_srt_text(text)


def format_srt(cues: list[Cue], first_number: int = 1) -> str:
    out = []
    for n, c in enumerate(cues, first_number):
        out.append(f"{n}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text}\n")
    return "\n".join(out)


def write_srt(path: Path, cues: list[Cue]):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(format_srt(cues), encoding="utf-8")
    tmp.replace(path)
