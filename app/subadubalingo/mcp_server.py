"""MCP server (stdio, JSON-RPC) so Claude can drive the app from a chat:

    start_video    start a video job (starts the app if it is not running)
    wait           wait for news on a job (every 10 %, Claude's turn, pause, error, done; at most ~45 s)
    status         the current state of a job
    get_text       the subtitles Claude must correct/translate, a range of blocks at a time
    save_text      hand back corrected/translated blocks; checked at once, the app carries on when all are in
    suggest_cuts   Claude's cut suggestions (by block number), when the video is to be cut first
    approve_cuts   the user's decision on the cuts, told to Claude in the chat (keep some, add some, cut now)
    cancel         stop a job (the user asked in the chat, or it hangs and blocks the app)
    find_videos    recent videos in the usual folders

Tool names, parameters and texts are English (the domain language, docs/glossary.md); Claude answers the user in
Danish. The job lives in the app, so if the chat stops, "status" picks it up again. Started as
"SubaDubaLingo.exe --mcp" by Claude desktop (claude_desktop_config.json), no extra dependencies.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import cutting, jobs
# Imported here, not inside the tools: a frozen exe reads modules from its own file on first import, and once an
# update has replaced that file, a late import reads the new file at the old offsets (zlib error -3).
from .claude import CUT_RULES, SKILL_NAME, SKILL_VERSION
from .common import APP_NAME, APP_VERSION, LANGUAGES, downloads_dir, format_srt, log, parse_srt_text

LANG_ALIASES = {}
for _l in LANGUAGES:
    for alias in (_l.name, _l.code, _l.plural, _l.singular, {"da": "danish", "sv": "swedish", "no": "norwegian",
                                                            "en": "english"}[_l.code]):
        LANG_ALIASES[alias.lower()] = _l
LANG_ALIASES["nb"] = LANG_ALIASES["no"]
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv"}
LANGS = "danish, swedish, norwegian or english"

# --- Tool definitions ---------------------------------------------------------------

TOOLS = [
    {"name": "start_video",
     "description": (f"Start a job in the Windows app '{APP_NAME}' (formerly 'Videosprog'): subtitles and/or a dub of a "
                     "local video, optionally cut first. The app does the slow work (listening through the video can "
                     "take up to an hour on a laptop) and starts by itself if it is not running. Then call 'wait' "
                     "repeatedly and tell the user the progress. When the state becomes 'claude_turn', fetch the text "
                     "with 'get_text', correct/translate it and hand it back with 'save_text'."),
     "inputSchema": {"type": "object", "properties": {
         "video": {"type": "string", "description": "Full path to the video file, e.g. C:\\Users\\me\\Videos\\webinar.mp4"},
         "speech": {"type": "string", "description": f"Speech in the new video: 'original' (keep it) or a language to dub to: {LANGS}"},
         "subtitles": {"type": "string", "description": f"Subtitles in the new video: 'none' or {LANGS}"},
         "spoken_language": {"type": "string", "description": "The language spoken in the video (default danish)"},
         "relisten": {"type": "boolean", "description": "Make the spoken-language text again even if it exists (default false)"},
         "cut": {"type": "boolean", "description": ("Cut the video first (default false): the app finds long silent "
                 "pauses, you suggest what to cut (waiting before the meeting starts, breaks, the end after the "
                 "meeting, inappropriate, off-topic or unintelligible parts), the user reviews the cuts on a "
                 "timeline, and subtitles/dub are made for the cut video. Only cutting: speech 'original', "
                 "subtitles 'none'.")}},
         "required": ["video", "speech", "subtitles"]}},
    {"name": "wait",
     "description": ("Wait for news on a job: returns at every 10 % of progress, when it is Claude's turn, on pause, "
                     "error or when done, or after at most ~45 seconds. Call it again until the job is done. Tell the "
                     "user briefly about each 10 %."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string", "description": "The job id from start_video (default: the latest job)"}}}},
    {"name": "status",
     "description": "The current state of a job (default the latest). Use it to pick up a job again, e.g. in a new chat.",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}}},
    {"name": "get_text",
     "description": ("When a job is in 'claude_turn': get the task (what to correct/translate, word list, rules) and the "
                     "subtitles as SRT, a range of blocks at a time (about 100-150 blocks per call works well)."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "start": {"type": "integer", "description": "First block number (default 1)"},
         "count": {"type": "integer", "description": "Number of blocks (default 120)"},
         "skill": {"type": "integer", "description": (f"The 'Skill version' number from the {SKILL_NAME} skill. Leave "
                   "it out if you do not have the skill.")}}, "required": ["job_id"]}},
    {"name": "save_text",
     "description": ("Hand back blocks for one language as SRT with the SAME numbers and time codes as from get_text "
                     "(corrected text in the spoken language, or a translation). Checked at once; the answer says what "
                     "is wrong or still missing. When every language is complete, the app carries on (dub/burn-in) by "
                     "itself; then call 'wait' until done. Put corrected misheard words in 'words' as lines "
                     "'wrong => right' so the app learns them."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "language": {"type": "string", "description": "Language code of the blocks: da, sv, no or en"},
         "srt": {"type": "string", "description": "The blocks as SRT"},
         "words": {"type": "string", "description": "Optional: corrected words, one per line 'wrong => right'"}},
         "required": ["job_id", "language", "srt"]}},
    {"name": "suggest_cuts",
     "description": ("When the job's task includes cutting: hand in your cut suggestions, one per line "
                     "'<first block>-<last block> <kind> <short reason>' (kinds: start, pause, end, inappropriate, "
                     "offtopic, unclear), e.g. '1-14 start People join and test the sound'. Block numbers are those "
                     "from get_text. Can be called several times (lines are added); set done=true when you have read "
                     "the whole text, also when there is nothing to cut."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "cuts": {"type": "string", "description": "Cut suggestions, one per line (may be empty)"},
         "done": {"type": "boolean", "description": "true when all suggestions are in"}},
         "required": ["job_id"]}},
    {"name": "approve_cuts",
     "description": ("While the user reviews the cuts in the app (state 'review_cuts'): apply what the user told you "
                     "in the chat. 'keep' = numbers of cuts NOT to make (the content stays in), 'add' = extra cuts, "
                     "one per line, by time 'mm:ss-mm:ss <kind> <reason>' or by block. Unless wait_for_user is true, "
                     "the app then cuts the video at once and carries on, so ask the user first whether the cuts are "
                     "right."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "keep": {"type": "array", "items": {"type": "integer"}, "description": "Numbers of cuts to keep in the video"},
         "add": {"type": "string", "description": "Extra cuts, one per line"},
         "wait_for_user": {"type": "boolean", "description": "true: only update the list; the user approves in the app"}},
         "required": ["job_id"]}},
    {"name": "cancel",
     "description": ("Stop a job in the app (default the one the app is working on). Use it when the user asks to "
                     "stop, or when a job hangs and the app says it is busy with another video; ask the user first."),
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}}},
    {"name": "find_videos",
     "description": "List recent video files in Videos, Downloads and Desktop (or a given folder), newest first.",
     "inputSchema": {"type": "object", "properties": {"folder": {"type": "string"}}}},
]

# --- Helpers -------------------------------------------------------------------------

def _lang(value: str | None, allow_none: tuple[str, ...] = ()):
    v = (value or "").strip().lower()
    if v in allow_none or not v:
        return None
    if v in LANG_ALIASES:
        return LANG_ALIASES[v]
    raise ValueError(f"Unknown language: {value!r}. Use {LANGS}.")


def _app_running() -> bool:
    try:
        # The app's main window (Tk's window class), not a message box or another program titled the same
        return bool(ctypes.windll.user32.FindWindowW("TkTopLevel", APP_NAME))
    except Exception:
        return False


def _start_app():
    if getattr(sys, "frozen", False):
        from .installer import current_exe
        exe = current_exe() if _current_version() else None   # an outdated server starts the current app
        args = [str(exe or sys.executable), "--from-claude"]
    else:
        args = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "--from-claude"]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _describe(job_id: str, st: dict) -> str:
    state = st.get("state", "queued")
    lines = [f"Job {job_id}: {jobs.STATES.get(state, state)}"]
    if st.get("video"):
        lines.append(f"Video: {st['video']}")
    if st.get("result"):
        lines.append(f"Result: {st['result']}")
    if state in ("running", "paused") and st.get("percent") is not None:
        lines.append(f"Progress: {st['percent']} % - {st.get('step', '')} {st.get('eta', '')}".rstrip())
    if st.get("message"):
        lines.append(f"Message from the app (Danish): {st['message']}")
    if state == "review_cuts":
        lines += _cut_list(job_id)
    hint = {
        "queued": "Call 'wait' again.",
        "running": "Call 'wait' again.",
        "paused": "The user has paused the job in the app. Call 'wait' again once they press Fortsæt.",
        "claude_turn": "Your turn: call 'get_text' from block 1, correct/translate, and hand back with 'save_text'.",
        "review_cuts": ("The user is reviewing the cuts in the app. Tell them briefly what will be cut and ask whether "
                        "it is right. If they correct something in the chat, use 'approve_cuts'. If they approve in "
                        "the app, it carries on by itself: call 'wait'."),
        "done": "Tell the user the video is ready and can be played from the app.",
        "error": "Tell the user about the error. The log file can be opened from the app's Indstillinger.",
        "cancelled": "The job has stopped.",
        "busy": ("The app is busy with another video. Try again when it is done. If that other video hangs, ask the "
                 "user whether to stop it with 'cancel'."),
    }.get(state)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


def _cut_list(job_id: str) -> list[str]:
    data = jobs.read_json(jobs.root() / job_id / "cuts.json") or {}
    cuts = data.get("cuts") or []
    if not cuts:
        return ["No cuts on the list."]
    out = ["Cuts (number: time, length, kind, reason):"]
    for i, c in enumerate(cuts, 1):
        kind = cutting.KIND_ALIASES.get(c.get("kind"), c.get("kind") or "cut")
        state = "" if c.get("on", True) else "  [kept - not cut]"
        what = f"{kind}: {c.get('reason')}" if c.get("reason") else kind
        text = f' - "{c["text"]}"' if c.get("text") else ""
        out.append(f"{i}. {cutting.fmt_clock(c['start'])}-{cutting.fmt_clock(c['end'])} "
                   f"({cutting.fmt_length(c['end'] - c['start'])}) {what}{state}{text}")
    if data.get("summary"):
        out.append(f"Summary shown to the user (Danish): {data['summary']}")
    return out

# --- Tools ---------------------------------------------------------------------------

_last_decile: dict[str, int] = {}


def tool_start_video(video: str, speech: str, subtitles: str, spoken_language: str = "danish",
                     relisten: bool = False, cut: bool = False) -> str:
    p = Path(video.strip().strip('"'))
    if not p.is_file():
        return f"The video does not exist: {p}. Ask the user for the full path, or use 'find_videos'."
    lang = _lang(spoken_language) or LANG_ALIASES["danish"]
    new_speech = _lang(speech, ("original", "keep", "same", "none", "originalt", "behold", "samme"))
    subs = _lang(subtitles, ("none", "no", "no subtitles", "ingen", "nej"))
    if new_speech == lang:
        new_speech = None
    if not cut and not new_speech and not subs and not relisten:
        return "Choose at least one thing: new speech, subtitles or cutting (cut=true)."
    job_id = jobs.new_request(str(p), lang.name, new_speech and new_speech.name, subs and subs.name, bool(relisten),
                              bool(cut))
    started = False
    if not _app_running():
        _start_app()
        started = True
    deadline = time.time() + 40
    st = {}
    while time.time() < deadline:
        st = jobs.status(job_id)
        if st.get("state") not in (None, "queued"):
            break
        time.sleep(0.5)
    _last_decile[job_id] = -1
    head = f"I started {APP_NAME}. " if started else ""
    if st.get("state") in (None, "queued"):
        return head + (f"Job {job_id} was sent to the app, but the app has not taken it yet (it may be waiting for the "
                       "user, e.g. a question or files to download). Call 'wait'.")
    return head + _describe(job_id, st)


def tool_wait(job_id: str | None = None) -> str:
    job_id = job_id or jobs.latest_job()
    if not job_id:
        return "There is no job. Start one with 'start_video'."
    first = jobs.status(job_id)
    state0 = first.get("state")
    delivered = (jobs.root() / job_id / "complete.json").exists()
    if state0 in ("done", "error", "cancelled", "busy") or (state0 == "claude_turn" and not delivered):
        return _describe(job_id, first)  # nothing will change until Claude (or the user) acts
    dec0 = _last_decile.get(job_id, -1)
    deadline = time.time() + 45
    st = first
    while time.time() < deadline:
        st = jobs.status(job_id)
        state = st.get("state")
        if state != state0:
            break
        pct = st.get("percent")
        if state == "running" and pct is not None and int(pct) // 10 > dec0:
            break
        time.sleep(1)
    pct = st.get("percent")
    if pct is not None:
        _last_decile[job_id] = int(pct) // 10
    return _describe(job_id, st)


ACTIVE = ("queued", "running", "claude_turn", "review_cuts", "paused")


def _active_job() -> str | None:
    """The newest job that has not ended (the one keeping the app busy)."""
    best = None
    for d in jobs.root().iterdir():
        f = d / "status.json"
        if d.is_dir() and d.name != "inbox" and f.exists():
            if (jobs.read_json(f, {}) or {}).get("state") in ACTIVE:
                if best is None or f.stat().st_mtime > best[0]:
                    best = (f.stat().st_mtime, d.name)
    return best and best[1]


def tool_cancel(job_id: str | None = None) -> str:
    job_id = job_id or _active_job()
    if not job_id or not (jobs.root() / job_id / "status.json").exists():
        return "There is no job to stop."
    st = jobs.status(job_id)
    if st.get("state") not in ACTIVE:
        return f"Job {job_id} is not running (it is {jobs.STATES.get(st.get('state'), st.get('state'))})."
    if not _app_running():
        # Nobody will finish it: the app was closed while the job was open
        Path(jobs.root() / "inbox" / f"{job_id}.json").unlink(missing_ok=True)
        jobs.set_status(job_id, state="cancelled", message="Stoppet fra Claude (appen kørte ikke).")
        return f"Job {job_id} has been stopped. The app was not running, so there was nothing to close."
    jobs.write_json(jobs.root() / job_id / "cancel.json", {"time": time.time()})
    deadline = time.time() + 20
    while time.time() < deadline:
        st = jobs.status(job_id)
        if st.get("state") not in ACTIVE:
            return _describe(job_id, st)
        time.sleep(0.5)
    return (f"I asked the app to stop job {job_id}, but it has not done so yet. Ask the user to press Annullér in "
            "the app.")


def tool_status(job_id: str | None = None) -> str:
    job_id = job_id or jobs.latest_job()
    if not job_id:
        return "There is no job yet."
    return _describe(job_id, jobs.status(job_id))


def _task(job_id: str) -> dict:
    t = jobs.read_json(jobs.root() / job_id / "task.json")
    if not t:
        raise ValueError("The job is not ready for Claude yet. Call 'wait'.")
    return t


def _missing(job_id: str, task: dict) -> dict[str, list[tuple[int, int]]]:
    """Per language code the block ranges (1-based) still missing."""
    n = task["blocks"]
    out = {}
    for code in task["codes"]:
        have = jobs.read_json(jobs.root() / job_id / "parts" / f"{code}.json", {}) or {}
        ranges, start = [], None
        for i in range(1, n + 1):
            if str(i) not in have:
                start = i if start is None else start
            elif start is not None:
                ranges.append((start, i - 1))
                start = None
        if start is not None:
            ranges.append((start, n))
        if ranges:
            out[code] = ranges
    return out


def _fmt_missing(m: dict) -> str:
    return "; ".join(f"{code}: " + ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in r) for code, r in m.items())


def _skill_note(job_id: str, skill) -> str:
    """Record which skill version Claude has (the app shows the update guide if it is old) and say so to Claude."""
    try:
        have = int(skill) if skill is not None else None
    except (TypeError, ValueError):
        have = None
    path = jobs.root() / job_id / "skill.json"
    if not path.exists() or have is not None:
        jobs.write_json(path, {"skill": have, "expected": SKILL_VERSION})
    if have is not None and have >= SKILL_VERSION:
        return ""
    what = f"do not have the {SKILL_NAME} skill" if have is None else f"have an old version of the skill ({have})"
    return (f"NOTE: You {what}; the newest is version {SKILL_VERSION}. Carry on with the task, but tell the user at the "
            f"end that the Claude skill needs updating: in {APP_NAME} under Indstillinger -> 'Gem Claude-skill...', "
            "then upload the file in Claude under Customize -> Skills (delete the old one first, also one called "
            "'videosprog').\n")


def tool_get_text(job_id: str, start: int = 1, count: int = 120, skill: int | None = None) -> str:
    task = _task(job_id)
    note = _skill_note(job_id, skill) if int(start or 1) == 1 or skill is not None else ""
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    start = max(1, int(start or 1))
    count = max(1, min(int(count or 120), 400))
    part = src[start - 1:start - 1 + count]
    names = {"da": "Danish", "sv": "Swedish", "no": "Norwegian", "en": "English"}
    todo = []
    if task["check"]:
        todo.append(f"correct the {names[task['src']]} text (misheard words, especially names and product terms) and "
                    f"hand it back as '{task['src']}'")
    for c in task["targets"]:
        todo.append(f"translate to {names[c]} and hand back as '{c}'")
    if task.get("cut"):
        todo.append("suggest cuts and hand them in with 'suggest_cuts' (done=true at the end)")
    lines = [
        f"Task for job {job_id}: " + "; ".join(todo) + ".",
        f"The text has {task['blocks']} blocks. Here are blocks {start}-{start + len(part) - 1}.",
        "Rules: the same numbers and exactly the same time codes; do not merge or split blocks; at most 2 lines of "
        "about 42 characters; natural spoken language; product and screen names are spelled as in the word list and "
        "not translated.",
        "Still missing: " + _still_missing(job_id, task),
    ]
    if task.get("words"):
        lines.append("Word list (known words): " + ", ".join(task["words"]))
    if task.get("rules"):
        lines.append("Typical mishearings: " + "; ".join(f"{w} => {r}" for w, r in task["rules"]))
    if task.get("cut"):
        lines += CUT_RULES
        lines.append("The video is " + cutting.fmt_clock(task.get("duration", 0)) + " long.")
    lines += ["", format_srt(part, start).rstrip()]
    if start - 1 + count < len(src):
        lines += ["", f"Next: get_text(start={start + len(part)})."]
    return note + "\n".join(lines)


def tool_save_text(job_id: str, language: str, srt: str, words: str = "") -> str:
    task = _task(job_id)
    code = (language or "").strip().lower()
    code = LANG_ALIASES[code].code if code in LANG_ALIASES else code
    if code not in task["codes"]:
        return f"The language '{language}' is not part of the task. Use one of: {', '.join(task['codes'])}."
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    by_time = {(c.start, c.end): i for i, c in enumerate(src, 1)}
    got = parse_srt_text(srt)
    if not got:
        return "There were no SRT blocks in 'srt'. Send the blocks as SRT (number, time code, text)."
    path = jobs.root() / job_id / "parts" / f"{code}.json"
    have = jobs.read_json(path, {}) or {}
    problems, saved = [], 0
    for c in got:
        idx = by_time.get((c.start, c.end))
        if idx is None:
            # tolerate 1 ms rounding
            idx = next((i for (a, b), i in by_time.items() if abs(a - c.start) <= 1 and abs(b - c.end) <= 1), None)
        if idx is None:
            problems.append(f"the time code {c.start / 1000:.3f}-{c.end / 1000:.3f} s is not in the original")
            continue
        if not c.text.strip():
            problems.append(f"block {idx} is empty")
            continue
        have[str(idx)] = c.text
        saved += 1
    jobs.write_json(path, have)
    learned = []
    for line in (words or "").splitlines():
        m = re.match(r"\s*[-*•]?\s*(.+?)\s*(?:=>|->|→)\s*(.+?)\s*$", line)
        if m:
            learned.append([m.group(1), m.group(2)])
    if learned:
        wpath = jobs.root() / job_id / "parts" / "words.json"
        jobs.write_json(wpath, (jobs.read_json(wpath, []) or []) + learned)
    msg = [f"Saved {saved} blocks in '{code}'."]
    if problems:
        msg.append(f"{len(problems)} blocks were not saved: " + "; ".join(problems[:8]) + (" ..." if len(problems) > 8 else ""))
    msg.append(_maybe_complete(job_id, task, src))
    return "\n".join(msg)


def _still_missing(job_id: str, task: dict) -> str:
    parts = []
    missing = _missing(job_id, task)
    if missing:
        parts.append(_fmt_missing(missing))
    if task.get("cut") and not (jobs.read_json(jobs.root() / job_id / "parts" / "cuts.json", {}) or {}).get("done"):
        parts.append("cut suggestions (suggest_cuts with done=true)")
    return "; ".join(parts) or "nothing"


def _maybe_complete(job_id: str, task: dict, src=None) -> str:
    """When every language and the cut suggestions are in, hand everything to the app."""
    left = _still_missing(job_id, task)
    if left != "nothing":
        return f"Still missing: {left}."
    if src is None:
        src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    out = {}
    for c in task["codes"]:
        parts = jobs.read_json(jobs.root() / job_id / "parts" / f"{c}.json", {}) or {}
        out[c] = [[s.start, s.end, parts[str(i)]] for i, s in enumerate(src, 1)]
    allwords = jobs.read_json(jobs.root() / job_id / "parts" / "words.json", []) or []
    k = jobs.read_json(jobs.root() / job_id / "parts" / "cuts.json", {}) or {}
    jobs.write_json(jobs.root() / job_id / "complete.json", {"codes": out, "words": allwords, "cuts": k.get("lines", [])})
    if task.get("cut"):
        return ("Everything is in. The app now shows the cuts to the user on a timeline. Call 'wait': when it answers "
                "'review_cuts', tell the user briefly what will be cut.")
    return "Every language is complete. The app now carries on by itself (dub/burn-in). Call 'wait' until it is done."


def tool_suggest_cuts(job_id: str, cuts: str = "", done: bool = False) -> str:
    task = _task(job_id)
    if not task.get("cut"):
        return "This job is not to be cut. Only use 'save_text'."
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    lines = [l.strip() for l in (cuts or "").splitlines() if l.strip()]
    parsed, bad = cutting.parse_cut_lines(lines, src, task.get("duration") or (src[-1].end if src else 0))
    path = jobs.root() / job_id / "parts" / "cuts.json"
    k = jobs.read_json(path, {}) or {}
    good = [l for l in lines if l not in bad]
    k["lines"] = (k.get("lines") or []) + good
    k["done"] = bool(done) or bool(k.get("done"))
    jobs.write_json(path, k)
    msg = [f"Saved {len(good)} cut suggestions ({len(k['lines'])} in all)."]
    for c in parsed:
        msg.append(f"  {cutting.fmt_clock(c.start)}-{cutting.fmt_clock(c.end)} {c.kind}: {c.reason}")
    if bad:
        msg.append("Could not read these lines (use '<block>-<block> <kind> <reason>'): " + " | ".join(bad))
    msg.append(_maybe_complete(job_id, task, src))
    return "\n".join(msg)


def tool_approve_cuts(job_id: str, keep: list | None = None, add: str = "", wait_for_user: bool = False) -> str:
    st = jobs.status(job_id)
    if st.get("state") != "review_cuts":
        return _describe(job_id, st) + "\nThe cuts can only be changed while the user reviews them (review_cuts)."
    decision = jobs.root() / job_id / "cut_decision.json"
    jobs.write_json(decision, {
        "keep": [int(x) for x in (keep or [])], "add": [l for l in (add or "").splitlines() if l.strip()],
        "approve": not wait_for_user, "created": time.time()})
    deadline = time.time() + 15
    while time.time() < deadline and decision.exists():
        time.sleep(0.5)
    if decision.exists():
        return "The app has not picked it up yet (is it closed?). Call 'status' in a moment."
    data = jobs.read_json(jobs.root() / job_id / "cuts.json") or {}
    head = (f"Message from the app (Danish): {data['decision_message']}" if data.get("decision_message")
            else "The app has updated the cuts.")
    if wait_for_user:
        return head + "\n" + "\n".join(_cut_list(job_id)) + "\nThe user approves in the app (the button 'Klip videoen'). Call 'wait'."
    return head + "\nThe app is now cutting the video. Call 'wait' until it is done."


def tool_find_videos(folder: str | None = None) -> str:
    dirs = [Path(folder)] if folder else [Path.home() / "Videos", downloads_dir(), Path.home() / "Desktop",
                                          Path.home() / "OneDrive - EG A S" / "Recordings"]
    ours = re.compile(r" - (med \w+ undertekster|\w+ dub( med \w+ undertekster)?)$")
    found = []
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            for f in d.rglob("*"):
                if f.suffix.lower() in VIDEO_EXT and f.is_file() and not ours.search(f.stem):
                    found.append(f)
        except OSError:
            continue
    found = sorted(set(found), key=lambda f: f.stat().st_mtime, reverse=True)[:15]
    if not found:
        return "No videos found. Ask the user for the full path."
    return "\n".join(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))}  {f}" for f in found)


HANDLERS = {"start_video": tool_start_video, "wait": tool_wait, "status": tool_status,
            "get_text": tool_get_text, "save_text": tool_save_text, "find_videos": tool_find_videos,
            "suggest_cuts": tool_suggest_cuts, "approve_cuts": tool_approve_cuts, "cancel": tool_cancel}

# The exe this server was started from. Claude keeps this process running across an update, so the old code would
# serve the new app; every call then says to restart Claude. Installed versions live in their own folders with
# current.txt next to them (installer.py); a changed exe covers a copy that was overwritten in place (and the
# flat layout up to v1.9.9, whose server read the new exe at old offsets: zlib error -3).
_EXE = Path(sys.executable) if getattr(sys, "frozen", False) else None


def _exe_stamp():
    try:
        st = os.stat(_EXE)
        return st.st_size, st.st_mtime_ns
    except (OSError, TypeError):
        return None


_EXE_STAMP = _exe_stamp() if _EXE else None


def _current_version() -> str | None:
    """The installed current version, when this exe is one of the version folders."""
    try:
        return (_EXE.parent.parent / "current.txt").read_text(encoding="utf-8").strip() or None
    except (OSError, AttributeError):
        return None


def _outdated() -> str | None:
    if not _EXE:
        return None
    cur = _current_version()
    if cur and cur != _EXE.parent.name:
        new = " to version " + cur
    elif _exe_stamp() != _EXE_STAMP:
        try:
            new = " to version " + (_EXE.parent / "version.txt").read_text(encoding="utf-8").strip()
        except OSError:
            new = ""
    else:
        return None
    return (f"{APP_NAME} has been updated{new} while Claude was running, so Claude still uses the old version "
            f"({APP_VERSION}) and cannot drive the app. Ask the user to quit Claude completely (right-click the Claude "
            "icon by the clock -> Quit) and open it again, then ask for the same thing again in a new chat.")

INSTRUCTIONS = (f"{APP_NAME} cuts, subtitles and dubs videos locally on the user's pc. Use start_video (cut=true if "
                "the video is to be cut first), follow the job with wait (tell the user about every 10 %), and on your "
                "turn (claude_turn) fetch the text with get_text in chunks, correct/translate it with the "
                f"{SKILL_NAME} skill, hand it back with save_text, and cut suggestions with suggest_cuts. While the "
                "user reviews the cuts (review_cuts), you can change them with approve_cuts. If the user wants to "
                "stop, or a job hangs, use cancel (ask first). The app checks everything and does the rest. Answer "
                "the user briefly, in Danish.")

# --- JSON-RPC over stdio -------------------------------------------------------------------

_out_lock = threading.Lock()


def _send(msg: dict):
    data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
    with _out_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def _handle(req: dict):
    method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}
    try:
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": SKILL_NAME, "version": APP_VERSION},
                      "instructions": INSTRUCTIONS}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            log.info("MCP %s %s", name, {k: (v if len(str(v)) < 200 else f"<{len(str(v))} chars>") for k, v in args.items()})
            try:
                stale = _outdated()
                if stale:
                    log.warning("MCP server %s is outdated: the exe has been replaced", APP_VERSION)
                    result = {"content": [{"type": "text", "text": stale}], "isError": True}
                else:
                    result = {"content": [{"type": "text", "text": HANDLERS[name](**args)}]}
            except Exception as e:
                log.error("MCP tool %s failed: %s", name, traceback.format_exc())
                result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
        elif method == "ping":
            result = {}
        elif rid is None:
            return  # a notification (e.g. notifications/initialized)
        else:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown method {method}"}})
            return
        if rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "result": result})
    except Exception as e:
        log.error("MCP request failed: %s", traceback.format_exc())
        if rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})


def main():
    log.info("MCP server start (%s)", APP_VERSION)
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Long calls (wait) run in their own thread, so pings and other calls are answered meanwhile.
        threading.Thread(target=_handle, args=(req,), daemon=True).start()
    log.info("MCP server stop")
