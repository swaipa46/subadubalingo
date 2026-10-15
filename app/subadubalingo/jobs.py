"""Jobs shared between the app and the MCP server (Claude), through files under data_dir()/jobs (%USERPROFILE%\\.subadubalingo\\jobs).

    jobs/inbox/<id>.json           a request from Claude: video, languages (the app picks it up when it is free)
    jobs/<id>/job.json             the request, once the app has taken it
    jobs/<id>/status.json          written by the app only: state, percent, step, result ...
    jobs/<id>/source.json          the text in the spoken language (blocks), when it is Claude's turn
    jobs/<id>/task.json            what Claude must do: codes, block count, words
    jobs/<id>/parts/<code>.json    blocks Claude has delivered so far (written by the MCP server)
    jobs/<id>/parts/cuts.json      cut suggestions Claude has delivered (lines, and whether it is done)
    jobs/<id>/complete.json        all languages (and cut suggestions) complete: the app carries on with these
    jobs/<id>/cuts.json            written by the app while the user reviews the cuts: the current list
    jobs/<id>/cut_decision.json    written by the MCP server: cuts to keep/add, and whether to cut now

Only one side writes each file, and every write is atomic (temp file + rename).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .common import Cue, data_dir, log

STATES = {   # told to Claude by the MCP server
    "queued": "waiting for the app to take it",
    "running": "working",
    "claude_turn": "Claude's turn: correct and translate the text",
    "review_cuts": "the user is reviewing the cuts in the app",
    "paused": "paused in the app",
    "done": "done",
    "error": "stopped with an error",
    "cancelled": "cancelled",
    "busy": "the app is busy with another video",
}


def root() -> Path:
    d = data_dir() / "jobs"
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    return d


def job_dir(job_id: str) -> Path:
    d = root() / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # the other process is reading it right now
            time.sleep(0.05)
    tmp.replace(path)


def read_json(path: Path, default=None):
    for _ in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (json.JSONDecodeError, PermissionError):
            time.sleep(0.05)
    return default


def new_request(video: str, lang: str, speech: str | None, subs: str | None, redo: bool, cut: bool = False) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    write_json(root() / "inbox" / f"{job_id}.json", {
        "id": job_id, "video": video, "lang": lang, "speech": speech, "subs": subs, "redo": redo, "cut": cut,
        "source": "claude", "created": time.time()})
    write_json(job_dir(job_id) / "status.json", {"state": "queued", "updated": time.time()})
    return job_id


def take_inbox() -> list[dict]:
    """Requests waiting for the app, oldest first (the files stay until the app accepts or rejects one)."""
    out = []
    for f in sorted((root() / "inbox").glob("*.json")):
        req = read_json(f)
        if req:
            req["_file"] = str(f)
            out.append(req)
    return out


def accept(req: dict):
    Path(req["_file"]).unlink(missing_ok=True)
    data = {k: v for k, v in req.items() if k != "_file"}
    write_json(job_dir(req["id"]) / "job.json", data)


def reject(req: dict, state: str, message: str):
    Path(req["_file"]).unlink(missing_ok=True)
    set_status(req["id"], state=state, message=message)


def set_status(job_id: str, **fields):
    path = job_dir(job_id) / "status.json"
    st = read_json(path, {}) or {}
    st.update(fields)
    st["updated"] = time.time()
    write_json(path, st)


def status(job_id: str) -> dict:
    return read_json(root() / job_id / "status.json", {}) or {}


def latest_job() -> str | None:
    dirs = [d for d in root().iterdir() if d.is_dir() and d.name != "inbox" and (d / "status.json").exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: (d / "status.json").stat().st_mtime).name


def cleanup(days: int = 14):
    """Drop job folders older than `days`."""
    limit = time.time() - days * 86400
    for d in root().iterdir():
        if d.is_dir() and d.name != "inbox":
            try:
                if (d / "status.json").stat().st_mtime < limit:
                    for f in sorted(d.rglob("*"), reverse=True):
                        f.unlink() if f.is_file() else f.rmdir()
                    d.rmdir()
            except OSError:
                pass


def cues_to_json(cues: list[Cue]) -> list[list]:
    return [[c.start, c.end, c.text] for c in cues]


def cues_from_json(rows) -> list[Cue]:
    return [Cue(int(a), int(b), str(t)) for a, b, t in rows]


def log_event(job_id: str, text: str):
    log.info("job %s: %s", job_id, text)
