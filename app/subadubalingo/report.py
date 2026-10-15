"""Send fejlrapport (an error report): a zip in Downloads with what helps find a fault, and an e-mail draft.

The zip holds the log, the settings, the app and Windows versions, the app's view of the last run and the last
Claude job's state files (job, status, task without the learned words, cuts without the subtitle excerpts). Never
videos, audio or subtitle texts. The user's home folder and user name are replaced in every text that goes in.

A mailto: link cannot carry an attachment, so the draft's body asks the user to attach the zip, and Explorer shows
it selected. The recipient is not in the (public) repo: it comes from support.txt next to the exe, which build.ps1
copies from the git-ignored support.local.txt. Without it the To field stays empty.
"""
from __future__ import annotations

import copy
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import ttk
from urllib.parse import quote

from .common import APP_NAME, APP_VERSION, app_dir, data_dir, downloads_dir, log, log_file
from .i18n import t

LOG_TAIL = 2_000_000          # bytes of the log that go in (the newest part)
MAIL_COMMENT_MAX = 1200       # characters of the comment in the mailto body (Windows cuts long mailto links)
JOB_FILES = ("job.json", "status.json", "task.json", "cut_decision.json", "cuts.json")
TASK_PRIVATE = ("words", "rules")   # the user's learned words in task.json


# --- Redaction -----------------------------------------------------------------

def _user_name() -> str:
    return os.environ.get("USERNAME") or Path.home().name


def redact(text: str, home: str | None = None, user: str | None = None) -> str:
    """The text without the user's home folder (also with / or \\\\ separators) and user name."""
    home = str(Path.home()) if home is None else home
    user = _user_name() if user is None else user
    if home:
        variants = {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}
        for v in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(v), "%USERPROFILE%", text, flags=re.IGNORECASE)
    # A very short name would match inside ordinary words; such names are left (the home folder is already gone)
    if user and len(user) >= 3:
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", text, flags=re.IGNORECASE)
    return text


def _redact_json(data, **kw):
    return json.loads(redact(json.dumps(data, ensure_ascii=False), **kw))


# --- What goes in -------------------------------------------------------------

def windows_version() -> str:
    """"Windows 11 Enterprise 10.0.26200" (Windows 11 still reports itself as release 10)."""
    try:
        v = sys.getwindowsversion()
        name = "11" if v.major == 10 and v.build >= 22000 else platform.release()
        edition = platform.win32_edition() or ""
        return f"Windows {name} {edition} {v.major}.{v.minor}.{v.build}".replace("  ", " ")
    except Exception:
        return platform.platform()


def version_line() -> str:
    return f"{APP_NAME} {APP_VERSION}, {windows_version()}"


def support_address() -> str:
    """The support address from support.txt next to the exe (from source also the repo's support.local.txt), or
    "" when there is none. The first line with an @ counts."""
    places = [app_dir() / "support.txt"]
    if not getattr(sys, "frozen", False):
        places.append(app_dir().parent / "support.local.txt")
    for f in places:
        try:
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if "@" in line and " " not in line and not line.startswith("#"):
                    return line
        except OSError:
            continue
    return ""


def _job_files(job_dir: Path) -> dict[str, bytes]:
    """The state files of one job, without the learned words and the subtitle excerpts."""
    out = {}
    for name in JOB_FILES:
        f = job_dir / name
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if name == "task.json" and isinstance(data, dict):
            for key in TASK_PRIVATE:
                if key in data:
                    data[key] = f"<{len(data[key])} left out>"
        if name == "cuts.json" and isinstance(data, dict):
            for c in data.get("cuts", []):
                if isinstance(c, dict) and "text" in c:
                    c["text"] = f"<{len(str(c['text']))} characters left out>"
        out[name] = json.dumps(_redact_json(data), ensure_ascii=False, indent=1).encode("utf-8")
    return out


def _latest_job(jobs_root: Path) -> str | None:
    try:
        dirs = [d for d in jobs_root.iterdir() if d.is_dir() and d.name != "inbox" and (d / "status.json").exists()]
    except OSError:
        return None
    return max(dirs, key=lambda d: (d / "status.json").stat().st_mtime).name if dirs else None


def _log_tail(f: Path) -> bytes:
    try:
        with open(f, "rb") as h:
            h.seek(0, 2)
            size = h.tell()
            h.seek(max(0, size - LOG_TAIL))
            raw = h.read()
    except OSError:
        return b""
    text = raw.decode("utf-8", "replace")
    if size > LOG_TAIL:
        text = text.split("\n", 1)[-1]   # start at a whole line
    return redact(text).encode("utf-8")


def contents(app_state: dict | None = None, job_id: str | None = None, root: Path | None = None) -> dict[str, bytes]:
    """{name in the zip: bytes}. app_state: the app's view of the last run (see App._report_state); job_id: the
    Claude job to include (default: the newest)."""
    root = root or data_dir()
    out: dict[str, bytes] = {}
    log_path = root / log_file().name
    if log_path.is_file():
        out["log.txt"] = _log_tail(log_path)
    settings = root / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            out["settings.json"] = json.dumps(_redact_json(data), ensure_ascii=False, indent=2).encode("utf-8")
        except Exception:
            pass
    about = {"app": APP_NAME, "version": APP_VERSION, "windows": windows_version(),
             "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
             "app_dir": redact(str(app_dir())), "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        from .common import free_memory_gb
        mem = free_memory_gb()
        if mem is not None:
            about["free_memory_gb"] = round(mem, 1)
    except Exception:
        pass
    out["about.json"] = json.dumps(about, ensure_ascii=False, indent=2).encode("utf-8")
    if app_state:
        out["last_run.json"] = json.dumps(_redact_json(app_state), ensure_ascii=False, indent=2,
                                          default=str).encode("utf-8")
    jobs_root = root / "jobs"
    job_id = job_id or _latest_job(jobs_root)
    if job_id and (jobs_root / job_id).is_dir():
        for name, data in _job_files(jobs_root / job_id).items():
            out[f"job/{name}"] = data
    return out


def zip_name(day: str | None = None) -> str:
    return t("report.zip_name", app=APP_NAME, date=day or time.strftime("%Y-%m-%d")) + ".zip"


def write_zip(files: dict[str, bytes], comment: str, folder: Path | None = None) -> Path:
    """Write the report to folder (Downloads), as a new file (" (2)" ... when one from today exists)."""
    folder = folder or downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    base = Path(zip_name())
    dest, n = folder / base.name, 2
    while dest.exists():
        dest = folder / f"{base.stem} ({n}){base.suffix}"
        n += 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if comment.strip():
            z.writestr("comment.txt", redact(comment.strip()) + "\n")
        for name, data in files.items():
            z.writestr(name, data)
    dest.write_bytes(buf.getvalue())
    return dest


def mail_body(comment: str, zip_file: str) -> str:
    comment = comment.strip()
    if len(comment) > MAIL_COMMENT_MAX:
        comment = comment[:MAIL_COMMENT_MAX].rstrip() + " ..."
    parts = [comment or t("report.mail_no_comment"), "", t("report.mail_attach", file=zip_file), "", "--",
             version_line()]
    return "\n".join(parts)


def mailto(to: str, subject: str, body: str) -> str:
    """A mailto: link; Windows mail apps want %20 for spaces and CRLF line ends."""
    body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    return f"mailto:{quote(to, safe='@.+-_')}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"


def send(comment: str, app_state: dict | None = None, job_id: str | None = None) -> Path:
    """Write the zip, show it in Explorer and open the e-mail draft. Returns the zip."""
    dest = write_zip(contents(app_state, job_id), comment)
    log.info("error report written: %s", dest.name)
    try:
        subprocess.Popen(["explorer", "/select,", str(dest)])
    except OSError:
        log.exception("could not show the report in Explorer")
    url = mailto(support_address(), t("report.mail_subject", app=APP_NAME, version=APP_VERSION),
                 mail_body(comment, dest.name))
    try:
        os.startfile(url)
    except OSError:
        log.exception("could not open the e-mail draft")
        raise
    return dest


# --- The dialog -----------------------------------------------------------------

class ReportDialog(tk.Toplevel):
    """Send fejlrapport: what is included, a comment field, and Lav mailen."""

    def __init__(self, parent, app_state: dict | None = None, job_id: str | None = None, scale: float = 1.0):
        from . import skin
        from .gui import place_over
        from .theme import IMG, P
        super().__init__(parent)
        self.withdraw()
        self.title(t("report.title"))
        self.transient(parent)
        self.resizable(False, False)
        self.app_state, self.job_id = app_state, job_id
        k = scale
        wrap = int(500 * k)
        outer = ttk.Frame(self, padding=int(14 * k))
        outer.pack(fill="both")
        ttk.Label(outer, text=t("report.intro"), wraplength=wrap, justify="left").pack(anchor="w", pady=(0, int(12 * k)))

        inc = skin.Card(outer, text=t("report.included"), padding=12)
        inc.pack(fill="x")
        for key in ("log", "settings", "versions", "job"):
            row = ttk.Frame(inc)
            row.pack(anchor="w", fill="x", pady=int(2 * k))
            tk.Label(row, image=IMG.get("icons/check-on"), borderwidth=0).pack(side="left", padx=(0, int(8 * k)))
            ttk.Label(row, text=t(f"report.item_{key}"), wraplength=wrap - int(30 * k), justify="left").pack(side="left")
        row = ttk.Frame(inc)
        row.pack(anchor="w", fill="x", pady=(int(6 * k), 0))
        tk.Label(row, image=IMG.get("icons/cancel"), borderwidth=0).pack(side="left", padx=(0, int(8 * k)))
        ttk.Label(row, text=t("report.never"), wraplength=wrap - int(30 * k), justify="left").pack(side="left")
        ttk.Label(inc, text=t("report.private"), style="Hint.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(int(10 * k), 0))

        com = skin.Card(outer, text=t("report.comment"), padding=12)
        com.pack(fill="x", pady=(int(14 * k), 0))
        ttk.Label(com, text=t("report.comment_hint"), style="Hint.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(0, int(8 * k)))
        box = tk.Frame(com, background=P.input_border, padx=max(1, int(2 * k)), pady=max(1, int(2 * k)))
        box.pack(fill="x")
        self.comment = tk.Text(box, height=5, width=10, wrap="word", relief="flat", borderwidth=0,
                               padx=int(8 * k), pady=int(6 * k), font="SunValleyBodyFont",
                               background=P.input_bg, foreground=P.text, insertbackground=P.text,
                               selectbackground=P.selection_bg, selectforeground=P.selection_text,
                               highlightthickness=0)
        self.comment.pack(fill="x")
        self.to = support_address()

        self.status = ttk.Label(outer, text="", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", fill="x", pady=(int(12 * k), 0))
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(int(10 * k), 0))
        ttk.Label(foot, text=f"{APP_NAME} {APP_VERSION}", style="Hint.TLabel").pack(side="left")
        b = ttk.Frame(foot)
        b.pack(side="right")
        self.cancel_btn = ttk.Button(b, text=t("gui.cancel"), command=self.destroy)
        self.cancel_btn.pack(side="left", padx=(0, 6))
        self.make_btn = ttk.Button(b, text=t("report.make"), style="Accent.TButton", command=self._make,
                                   default="active")
        self.make_btn.pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        place_over(self, parent.winfo_toplevel())
        self.box = box
        box.configure(background=P.input_border)   # skin.dialog paints card frames with the card colour
        self.comment.focus_set()
        # Over Indstillinger (which holds the grab) the dialog takes the grab, and gives it back when it closes
        self._prev_grab = self.grab_current()
        self.wait_visibility()
        self.grab_set()

    def destroy(self):
        prev = getattr(self, "_prev_grab", None)
        super().destroy()
        if prev is not None:
            try:
                prev.grab_set()
            except tk.TclError:
                pass

    def _make(self):
        from .theme import P
        try:
            dest = send(self.comment.get("1.0", "end"), self.app_state, self.job_id)
        except Exception as e:
            log.exception("error report failed")
            self.status.configure(text=t("report.failed", error=e), foreground=P.error)
            return
        self.status.configure(text=t("report.done", file=dest.name), foreground=P.success)
        # One report per click: the main button now closes, so a second click does not make a second zip
        self.cancel_btn.pack_forget()
        self.make_btn.configure(text=t("report.close"), command=self.destroy)
