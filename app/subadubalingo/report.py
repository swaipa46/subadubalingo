"""Send fejlrapport (an error report): a JSON report to the relay on swaipa.dk, which makes it a GitHub issue in a
private repo. The contract (fields, limits, answers) is docs/report-relay.md.

What goes in: the error and the failed step, versions, the UI language and theme, the run's options and step states,
the user's comment and the newest part of the log. Never videos, audio or subtitle texts. Everything is redacted here,
before the dialog shows it: the home folder becomes ~, the Windows user name <user>, and the names of videos <video>.
The dialog shows exactly what is sent (the log tail too) and sends only when the user clicks Send.

A report that cannot be sent now (no network, 5xx, 429) waits in ~/.subadubalingo/reports/pending/<report_id>.json and
is sent again by a background thread at the next start (at most PENDING_KEEP are kept; the oldest go first).

A crash (an exception nothing caught: sys.excepthook, threading.excepthook or Tk's report_callback_exception) is saved
as a draft in reports/crash/, never sent by itself. The next error done view or app start offers it in the same dialog.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from tkinter import ttk

from .common import APP_NAME, APP_VERSION, data_dir, log, log_file
from .i18n import t

REPORT_URL = "https://swaipa.dk/api/subadubalingo/v1/reports"
SCHEMA = 1
TITLE_MAX = 120
ERROR_MAX = 4_000            # characters
COMMENT_MAX = 4_000          # characters
LOG_MAX = 200_000            # bytes (UTF-8) of the redacted log tail
LOG_RETRY = 50_000           # the log tail after a 413
BODY_MAX = 256 * 1024        # bytes of the whole JSON body
PENDING_KEEP = 10
CRASH_KEEP = 3
TIMEOUT = 20                 # seconds per request
VIDEO_EXT = ("mp4", "mov", "mkv", "m4v", "avi", "webm", "wmv")
KINDS = ("crash", "error", "user")

_lock = threading.Lock()     # pending/crash folder changes
_in_flight: set[str] = set()   # report_ids being sent right now (the dialog and the retry thread never send one twice)


def report_url() -> str:
    """The relay; SUBADUBALINGO_REPORT_URL points the tests at a local mock."""
    return os.environ.get("SUBADUBALINGO_REPORT_URL") or REPORT_URL


def reports_dir() -> Path:
    return data_dir() / "reports"


def pending_dir() -> Path:
    return reports_dir() / "pending"


def crash_dir() -> Path:
    return reports_dir() / "crash"


# --- Redaction -----------------------------------------------------------------

def _user_name() -> str:
    return os.environ.get("USERNAME") or Path.home().name


_EXT = "|".join(VIDEO_EXT + ("srt",))
# A character of a file name, but never the start of a command line option (" -i") or of the next argument after
# a file with an extension ("ffmpeg.exe -i"), so a logged command line is not taken for one long name
_NAME_CH = r"(?:(?! -\S)(?!\.\w{1,4}\s)[^\\/:*?\"<>|=\r\n])"
# A path component that is a video or subtitle file: its name up to " - <what was made>" or the extension
_PATH_FILE = re.compile(rf"(?<=[\\/]){_NAME_CH}+?(?=(?: - {_NAME_CH}*)?\.(?:{_EXT})\b)", re.IGNORECASE)


class Redactor:
    """Replaces the home folder (also with / or \\\\ separators) with ~, the names of the given videos (their stems,
    so "<stem> - danske undertekster.srt" goes too) and any video or subtitle file in a path with <video>, and the
    user name with <user>."""

    def __init__(self, videos=(), home: str | None = None, user: str | None = None):
        self.home = str(Path.home()) if home is None else home
        self.user = _user_name() if user is None else user
        stems = {Path(str(v)).stem if Path(str(v)).suffix.lower().lstrip(".") in VIDEO_EXT else str(v)
                 for v in videos if v}
        # A very short name would match inside ordinary words
        self.stems = sorted((s for s in stems if len(s.strip()) >= 3), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        if not text:
            return text or ""
        if self.home:
            variants = {self.home, self.home.replace("\\", "/"), self.home.replace("\\", "\\\\")}
            for v in sorted(variants, key=len, reverse=True):
                text = re.sub(re.escape(v), "~", text, flags=re.IGNORECASE)
        for s in self.stems:
            text = re.sub(r"(?<![\w])" + re.escape(s) + r"(?![\w])", "<video>", text, flags=re.IGNORECASE)
        text = _PATH_FILE.sub("<video>", text)
        if self.user and len(self.user) >= 3:
            text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(self.user) + r"(?![A-Za-z0-9])", "<user>", text,
                          flags=re.IGNORECASE)
        return text

    def json(self, data):
        return json.loads(self(json.dumps(data, ensure_ascii=False, default=str)))


def redact(text: str, home: str | None = None, user: str | None = None, videos=()) -> str:
    return Redactor(videos, home, user)(text)


def known_videos(root: Path | None = None) -> set[str]:
    """Video names the app has seen: the stems in text_state.json (its keys are the subtitle files, "<stem> - ...")
    and the videos of Claude jobs. Used so their names go also where no path shows them (log lines with a bare name)."""
    root = root or data_dir()
    out: set[str] = set()
    try:
        state = json.loads((root / "text_state.json").read_text(encoding="utf-8"))
        for key in state if isinstance(state, dict) else ():
            out.add(Path(key).stem.split(" - ")[0])
    except Exception:
        pass
    try:
        for f in (root / "jobs").glob("*/job.json"):
            try:
                video = json.loads(f.read_text(encoding="utf-8")).get("video")
                if video:
                    out.add(Path(video).stem)
            except Exception:
                continue
    except OSError:
        pass
    return {s for s in out if s}


# --- What goes in -------------------------------------------------------------

def windows_version() -> str:
    """"Windows 11 26200" (Windows 11 still reports itself as release 10)."""
    try:
        v = sys.getwindowsversion()
        name = "11" if v.major == 10 and v.build >= 22000 else platform.release()
        return f"Windows {name} {v.build}"
    except Exception:
        return platform.platform()


def _cap(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _tail_bytes(text: str, n: int) -> str:
    """The end of text in at most n bytes of UTF-8, starting at a whole line."""
    raw = text.encode("utf-8")
    if n <= 0:
        return ""
    if len(raw) <= n:
        return text
    cut = raw[-n:].decode("utf-8", "ignore")
    return cut.split("\n", 1)[-1] if "\n" in cut else cut


def log_tail(redactor: Redactor, path: Path | None = None, limit: int = LOG_MAX) -> str:
    """The newest part of the log, redacted, in at most limit bytes."""
    path = path or log_file()
    try:
        with open(path, "rb") as h:
            h.seek(0, 2)
            size = h.tell()
            h.seek(max(0, size - limit * 2))   # redaction can make the text longer or shorter; read some extra
            raw = h.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
    if size > limit * 2:
        text = text.split("\n", 1)[-1]
    return _tail_bytes(redactor(text), limit)


_QUOTED_PATH = re.compile(r"([\"'])[^\"'\n]*[\\/][^\"'\n]*\1")
_PATH_TOKEN = re.compile(r"\S*[\\/]\S*")


def fingerprint(error_type: str, step: str, error: str) -> str:
    """16 hex chars of SHA-256 over the error type, the failed step and the first line of the error without numbers
    and paths, so the same problem gives the same fingerprint on every PC and every video."""
    first = next((line for line in (error or "").splitlines() if line.strip()), "")
    first = _QUOTED_PATH.sub("", first)
    first = _PATH_TOKEN.sub("", first)
    first = re.sub(r"\d+", "", first)
    first = re.sub(r"\s+", " ", first).strip()
    return hashlib.sha256(f"{error_type}\n{step}\n{first}".encode("utf-8")).hexdigest()[:16]


def _first_line(text: str) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def _title(kind: str, error_type: str, step_no: str, step_name: str, error: str, comment: str) -> str:
    """The issue's title. In Danish whatever the UI language: it is for the developer, like the issue's body."""
    if kind == "crash":
        title = f"Nedbrud: {_first_line(error) or error_type}"
    elif kind == "error":
        where = " ".join(x for x in (step_no, step_name) if x)
        title = f"Fejl i {where}: {_first_line(error)}" if where else f"Fejl: {_first_line(error)}"
    else:
        title = f"Rapport fra brugeren: {_first_line(comment)}" if _first_line(comment) else "Rapport fra brugeren"
    return _cap(title, TITLE_MAX)


def build(kind: str, *, error: str = "", error_type: str = "", step: str = "", step_no: str = "",
          step_name: str = "", run: dict | None = None, theme: str | None = None, videos=(),
          log_path: Path | None = None, redactor: Redactor | None = None) -> dict:
    """A schema 1 report without the comment (set_comment adds it), redacted. kind: crash, error or user."""
    assert kind in KINDS, kind
    r = redactor or Redactor(set(videos) | known_videos())
    error = _cap(r(error), ERROR_MAX)
    if theme is None:
        try:
            from .theme import P
            theme = P.name
        except Exception:
            theme = "light"
    try:
        from .i18n import current
        ui_language = current()
    except Exception:
        ui_language = "da"
    payload = {
        "schema": SCHEMA,
        "report_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "fingerprint": "",
        "title": "",
        "app_version": APP_VERSION,
        "os": windows_version(),
        "ui_language": ui_language if ui_language in ("da", "sv", "no", "en") else "en",
        "theme": theme if theme in ("light", "night") else "light",
        "step": step or "",
        "error": error,
        "comment": "",
        "run": r.json(run or {}),
        "log_tail": log_tail(r, log_path),
    }
    payload["_meta"] = {"error_type": error_type, "step_no": step_no, "step_name": r(step_name)}
    _finish(payload)
    return payload


def _finish(payload: dict):
    """Title and fingerprint from the fields (again after the comment changed: a user report has no error)."""
    meta = payload.get("_meta") or {}
    kind, error, comment = payload["kind"], payload["error"], payload["comment"]
    payload["title"] = _title(kind, meta.get("error_type", ""), meta.get("step_no", ""), meta.get("step_name", ""),
                              error, comment)
    if kind == "user" and not error:
        payload["fingerprint"] = fingerprint("user", payload["step"], comment)
    else:
        payload["fingerprint"] = fingerprint(meta.get("error_type") or kind, payload["step"], error)


def set_comment(payload: dict, comment: str, redactor: Redactor | None = None) -> dict:
    payload["comment"] = _cap((redactor or Redactor(known_videos()))(comment or ""), COMMENT_MAX)
    _finish(payload)
    return payload


def body(payload: dict) -> dict:
    """What goes over the wire: the schema 1 fields only, the log tail shortened when the body would be too big."""
    out = {k: v for k, v in payload.items() if not k.startswith("_")}
    while True:
        size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        if size <= BODY_MAX or not out["log_tail"]:
            return out
        # JSON escapes make the log longer on the wire than in UTF-8: cut it in proportion to what it takes there
        raw = len(out["log_tail"].encode("utf-8"))
        wire = len(json.dumps(out["log_tail"], ensure_ascii=False).encode("utf-8"))
        budget = BODY_MAX - (size - wire) - 1024
        out["log_tail"] = _tail_bytes(out["log_tail"], min(raw - 1, int(raw * budget / wire)))


# --- Sending ---------------------------------------------------------------------

class Result:
    """outcome: "sent" (ref: the issue number), "later" (kept in pending; busy: the relay said 429) or "rejected"
    (the relay said 400, or 413 twice; error: its message)."""

    def __init__(self, outcome: str, ref: int | None = None, error: str = "", busy: bool = False):
        self.outcome, self.ref, self.error, self.busy = outcome, ref, error, busy

    def __repr__(self):
        return f"Result({self.outcome}, ref={self.ref}, error={self.error!r}, busy={self.busy})"


def _post(data: dict, url: str, timeout: float) -> tuple[int, dict, dict]:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, text, headers = resp.status, resp.read(65536), dict(resp.headers)
    except urllib.error.HTTPError as e:
        status, headers = e.code, dict(e.headers or {})
        try:
            text = e.read(65536)
        except Exception:
            text = b""
    try:
        answer = json.loads(text.decode("utf-8")) if text else {}
    except Exception:
        answer = {}
    return status, answer if isinstance(answer, dict) else {}, headers


def send(payload: dict, url: str | None = None, timeout: float = TIMEOUT) -> Result:
    """POST the report once (twice after a 413, with the log tail cut to LOG_RETRY). Never raises."""
    url = url or report_url()
    data = body(payload)
    for attempt in (1, 2):
        try:
            status, answer, headers = _post(data, url, timeout)
        except Exception as e:   # no network, DNS, refused, timeout, TLS
            log.info("error report %s not sent: %s", data["report_id"], e)
            return Result("later")
        log.info("error report %s: HTTP %s %s", data["report_id"], status, answer.get("status", ""))
        if status in (200, 201):
            ref = answer.get("ref")
            return Result("sent", ref if isinstance(ref, int) else None)
        if status == 400:
            return Result("rejected", error=str(answer.get("error") or "400"))
        if status == 413 and attempt == 1:
            data = dict(data, log_tail=_tail_bytes(data["log_tail"], LOG_RETRY))
            continue
        if status == 413:
            return Result("rejected", error="413")
        return Result("later", busy=status == 429)
    return Result("later")


def _pending_file(report_id: str) -> Path:
    return pending_dir() / f"{report_id}.json"


def _prune(folder: Path, keep: int):
    files = sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for f in files[:max(0, len(files) - keep)]:
        try:
            f.unlink()
            log.info("dropped old report %s", f.name)
        except OSError:
            pass


def save_pending(payload: dict):
    with _lock:
        pending_dir().mkdir(parents=True, exist_ok=True)
        _pending_file(payload["report_id"]).write_text(json.dumps(body(payload), ensure_ascii=False), encoding="utf-8")
        _prune(pending_dir(), PENDING_KEEP)


def _forget(report_id: str):
    with _lock:
        try:
            _pending_file(report_id).unlink()
        except OSError:
            pass


def submit(payload: dict, url: str | None = None) -> Result:
    """Send a report the user asked to send. It waits in pending until the relay has it (or refused it), so a report
    survives the app being closed mid-send. Never raises."""
    rid = payload["report_id"]
    try:
        save_pending(payload)
    except OSError:
        log.exception("could not keep the report")
    with _lock:
        _in_flight.add(rid)
    try:
        result = send(payload, url)
    finally:
        with _lock:
            _in_flight.discard(rid)
    if result.outcome != "later":
        _forget(rid)
    return result


def retry_pending(url: str | None = None) -> list[Result]:
    """Send the waiting reports, oldest first; stop at the first one that has to wait again (no network, busy)."""
    out = []
    try:
        files = sorted(pending_dir().glob("*.json"), key=lambda f: f.stat().st_mtime)
    except OSError:
        return out
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            assert isinstance(payload, dict) and payload.get("report_id") == f.stem
        except Exception:
            log.warning("dropped unreadable report %s", f.name)
            f.unlink(missing_ok=True)
            continue
        with _lock:
            if f.stem in _in_flight:
                continue
            _in_flight.add(f.stem)
        try:
            result = send(payload, url)
        finally:
            with _lock:
                _in_flight.discard(f.stem)
        out.append(result)
        if result.outcome == "later":
            break
        _forget(f.stem)
    return out


def start_retry() -> threading.Thread | None:
    """retry_pending in a background thread, when anything waits (never blocks the window)."""
    try:
        if not any(pending_dir().glob("*.json")):
            return None
    except OSError:
        return None

    def run():
        try:
            log.info("pending error reports: %s", retry_pending())
        except Exception:
            log.exception("retrying error reports failed")
    th = threading.Thread(target=run, daemon=True, name="report-retry")
    th.start()
    return th


# --- Crashes ---------------------------------------------------------------------

def save_crash(exc_type, exc, tb, run: dict | None = None, theme: str | None = None) -> Path | None:
    """Keep a crash as a draft for the user to send later. Never raises (it runs inside the exception hooks)."""
    try:
        name = getattr(exc_type, "__name__", str(exc_type))
        error = f"{name}: {exc}\n\n" + "".join(traceback.format_exception(exc_type, exc, tb))
        payload = build("crash", error=error, error_type=name, run=run, theme=theme)
        with _lock:
            crash_dir().mkdir(parents=True, exist_ok=True)
            f = crash_dir() / f"{payload['report_id']}.json"
            f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            _prune(crash_dir(), CRASH_KEEP)
        log.info("crash report saved: %s", f.name)
        return f
    except Exception:
        try:
            log.exception("could not save the crash report")
        except Exception:
            pass
        return None


def take_crash() -> dict | None:
    """The newest crash draft (the older ones are dropped: one offer per crash run is enough), or None."""
    try:
        with _lock:
            files = sorted(crash_dir().glob("*.json"), key=lambda f: f.stat().st_mtime)
            for f in files[:-1]:
                f.unlink(missing_ok=True)
        if not files:
            return None
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) and payload.get("kind") == "crash" else None
    except Exception:
        log.exception("could not read the crash report")
        return None


def forget_crash(report_id: str):
    with _lock:
        try:
            (crash_dir() / f"{report_id}.json").unlink()
        except OSError:
            pass


_hooked = False


def install_hooks(root: tk.Tk | None = None, run=None):
    """Save unhandled exceptions as crash drafts: sys.excepthook, threading.excepthook and Tk's
    report_callback_exception. run: a function giving the run's state (called in a try; may be None). The previous
    hooks still run after ours."""
    global _hooked
    state = {"busy": False}

    def handle(exc_type, exc, tb):
        if state["busy"] or exc_type in (KeyboardInterrupt, SystemExit):
            return
        state["busy"] = True
        try:
            log.error("unhandled exception: %s", "".join(traceback.format_exception(exc_type, exc, tb)))
            info = None
            if run and threading.current_thread() is threading.main_thread():   # it reads Tk widgets
                try:
                    info = run()
                except Exception:
                    info = None
            save_crash(exc_type, exc, tb, info)
        except Exception:
            pass
        finally:
            state["busy"] = False

    if not _hooked:
        _hooked = True
        prev_sys, prev_thread = sys.excepthook, threading.excepthook

        def sys_hook(exc_type, exc, tb):
            handle(exc_type, exc, tb)
            prev_sys(exc_type, exc, tb)

        def thread_hook(args):
            if args.exc_type is not SystemExit:
                handle(args.exc_type, args.exc_value, args.exc_traceback)
            prev_thread(args)
        sys.excepthook, threading.excepthook = sys_hook, thread_hook
    if root is not None:
        def tk_hook(exc_type, exc, tb):
            handle(exc_type, exc, tb)
            traceback.print_exception(exc_type, exc, tb)   # what Tk does by default (stderr; nothing in the exe)
        root.report_callback_exception = tk_hook


# --- The dialog -----------------------------------------------------------------

class ReportDialog(tk.Toplevel):
    """Send fejlrapport: what is included (the log tail viewable), a comment field, Annullér and Send, then the
    result line. payload: from build() (or a crash draft from take_crash()); videos: names to redact in the comment."""

    def __init__(self, parent, payload: dict, videos=(), scale: float = 1.0, url: str | None = None):
        from . import skin
        from .gui import place_over
        from .theme import IMG, P
        super().__init__(parent)
        self.withdraw()
        self.title(t("report.title"))
        self.transient(parent)
        self.resizable(False, False)
        self.payload, self.url = payload, url
        self.redactor = Redactor(set(videos) | known_videos())
        self.result: Result | None = None
        self._outcome = None
        k = self.k = scale
        wrap = int(520 * k)
        outer = ttk.Frame(self, padding=(int(20 * k), int(16 * k)))
        outer.pack(fill="both")
        crash = payload.get("kind") == "crash"
        ttk.Label(outer, text=t("report.crash_heading" if crash else "report.heading"), style="Section.TLabel",
                  wraplength=wrap, justify="left").pack(anchor="w", pady=(0, int(12 * k)))

        inc = skin.Card(outer, text=t("report.included"), padding=12)
        inc.pack(fill="x")
        items = (["error"] if payload.get("error") else []) + ["versions", "job", "log"]
        for key in items:
            row = ttk.Frame(inc)
            row.pack(anchor="w", fill="x", pady=int(3 * k))
            tk.Label(row, image=IMG.get("icons/check-disabled"), borderwidth=0,
                     background=P.card).pack(side="left", padx=(0, int(10 * k)))
            ttk.Label(row, text=t(f"report.item_{key}"), wraplength=wrap - int(160 * k),
                      justify="left").pack(side="left")
            if key == "log":
                self.log_btn = ttk.Button(row, text=t("report.show_log"), command=self._toggle_log)
                self.log_btn.pack(side="right")
        # The log tail exactly as it will be sent, read-only, shown on demand
        self.log_box = tk.Frame(inc, background=P.input_border, padx=1, pady=1)
        self.log_text = tk.Text(self.log_box, height=12, width=10, wrap="char", relief="flat", borderwidth=0,
                                padx=int(6 * k), pady=int(4 * k), font=("Consolas", 9), background=P.input_bg,
                                foreground=P.text, selectbackground=P.selection_bg,
                                selectforeground=P.selection_text, highlightthickness=0)
        sb = ttk.Scrollbar(self.log_box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.insert("1.0", payload.get("log_tail") or t("report.log_empty"))
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        self.never = ttk.Label(inc, text=t("report.never"), style="Hint.TLabel", wraplength=wrap - int(20 * k),
                               justify="left")
        self.never.pack(anchor="w", pady=(int(8 * k), 0))

        head = ttk.Frame(outer)
        head.pack(anchor="w", fill="x", pady=(int(16 * k), int(6 * k)))
        ttk.Label(head, text=t("report.comment"), font="SunValleyBodyStrongFont").pack(side="left")
        ttk.Label(head, text=t("report.optional"), style="Hint.TLabel").pack(side="left", padx=(int(4 * k), 0))
        box = tk.Frame(outer, background=P.input_border, padx=max(1, int(2 * k)), pady=max(1, int(2 * k)))
        box.pack(fill="x")
        self.comment = tk.Text(box, height=5, width=10, wrap="word", relief="flat", borderwidth=0,
                               padx=int(8 * k), pady=int(6 * k), font="SunValleyBodyFont",
                               background=P.input_bg, foreground=P.text, insertbackground=P.text,
                               selectbackground=P.selection_bg, selectforeground=P.selection_text,
                               highlightthickness=0)
        self.comment.pack(fill="x")
        if payload.get("comment"):
            self.comment.insert("1.0", payload["comment"])
        ttk.Label(outer, text=t("report.intro"), style="Hint.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(int(12 * k), 0))

        self.status = ttk.Label(outer, text="", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", fill="x", pady=(int(8 * k), 0))
        b = ttk.Frame(outer)
        b.pack(anchor="e", pady=(int(12 * k), 0))
        self.cancel_btn = ttk.Button(b, text=t("gui.cancel"), command=self._cancel)
        self.cancel_btn.pack(side="left", padx=(0, int(12 * k)))
        self.send_btn = skin.IconButton(b, "arrow-right", text=t("report.send"), style="Main.Accent.TButton",
                                        command=self._send, default="active")
        self.send_btn.pack(side="left")
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        place_over(self, parent.winfo_toplevel())
        for frame in (box, self.log_box):   # skin.dialog paints card frames with the card colour
            frame.configure(background=P.input_border)
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

    def _toggle_log(self):
        if self.log_box.winfo_ismapped():
            self.log_box.pack_forget()
            self.log_btn.configure(text=t("report.show_log"))
        else:
            self.log_box.pack(fill="x", pady=(int(8 * self.k), 0), before=self.never)
            self.log_text.see("end")
            self.log_btn.configure(text=t("report.hide_log"))

    def _cancel(self):
        if self.payload.get("kind") == "crash" and self.result is None:
            forget_crash(self.payload["report_id"])   # declined: do not offer this crash again
        self.destroy()

    def _send(self):
        """Send in a thread (the window never waits on the network); _check shows the result."""
        from .theme import P
        set_comment(self.payload, self.comment.get("1.0", "end"), self.redactor)
        self.send_btn.state(["disabled"])
        self.cancel_btn.state(["disabled"])
        self.comment.configure(state="disabled")
        self.status.configure(text=t("report.sending"), foreground=P.hint)

        def work():
            try:
                self._outcome = submit(self.payload, self.url)
            except Exception as e:   # submit does not raise; this is the last safety net
                log.exception("error report failed")
                self._outcome = e
            if self.payload.get("kind") == "crash":
                forget_crash(self.payload["report_id"])
        threading.Thread(target=work, daemon=True, name="report-send").start()
        self.after(100, self._check)

    def _check(self):
        from .theme import P
        if self._outcome is None:
            self.after(100, self._check)
            return
        r = self._outcome
        if isinstance(r, Exception):
            text, colour = t("report.failed", error=r), P.error
        elif r.outcome == "sent":
            text = t("report.sent", ref=r.ref) if r.ref is not None else t("report.sent_no_ref")
            colour = P.success
        elif r.outcome == "later":
            text, colour = t("report.queued_busy" if r.busy else "report.queued"), P.text
        else:
            text, colour = t("report.rejected", error=r.error), P.error
        self.result = r if not isinstance(r, Exception) else None
        self.status.configure(text=text, foreground=colour)
        # One report per click: the main button now closes, so a second click does not send it again
        self.cancel_btn.pack_forget()
        self.send_btn.state(["!disabled"])
        self.send_btn.configure(text=t("report.close"), image="", command=self.destroy)
