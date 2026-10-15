"""MCP server (stdio, JSON-RPC) so Claude can drive the app from a chat:

    start_video   start a video job (starts the app if it is not running)
    vent          wait for news on a job (every 10 %, Claude's turn, pause, error, done; at most ~45 s)
    status        the current state of a job
    hent_tekst    the subtitles Claude must correct/translate, a range of blocks at a time
    gem_tekst     hand back corrected/translated blocks; checked at once, the app carries on when all are in
    find_videoer  recent videos in the usual folders
    foreslaa_klip Claude's cut suggestions (by block number), when the video is to be cut first
    godkend_klip  the user's decision on the cuts, told to Claude in the chat (keep some, add some, cut now)
    annuller      stop a job (the user asked in the chat, or it hangs and blocks the app)

The job lives in the app, so if the chat stops, "status" picks it up again. Started as
"Videosprog.exe --mcp" by Claude desktop (claude_desktop_config.json), no extra dependencies.
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
from .claude import CUT_RULES, SKILL_VERSION
from .common import APP_NAME, APP_VERSION, LANGUAGES, downloads_dir, format_srt, log, parse_srt_text

LANG_ALIASES = {}
for _l in LANGUAGES:
    for alias in (_l.name, _l.code, _l.plural, _l.singular, {"da": "danish", "sv": "swedish", "no": "norwegian",
                                                            "en": "english"}[_l.code]):
        LANG_ALIASES[alias.lower()] = _l
LANG_ALIASES["nb"] = LANG_ALIASES["no"]
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv"}

# --- Tool definitions ---------------------------------------------------------------

TOOLS = [
    {"name": "start_video",
     "description": ("Start a job in the Windows app 'Videosprog' (formerly 'Video undertekster'): subtitles and/or a dub of a local video. "
                     "The app does the slow work (listening through the video can take up to an hour on a laptop) and "
                     "starts by itself if it is not running. Then call 'vent' repeatedly and tell the user the progress. "
                     "When the state becomes 'claude_turn', fetch the text with 'hent_tekst', correct/translate it and "
                     "hand it back with 'gem_tekst'."),
     "inputSchema": {"type": "object", "properties": {
         "video": {"type": "string", "description": "Full path to the video file, e.g. C:\\Users\\me\\Videos\\webinar.mp4"},
         "tale": {"type": "string", "description": "Speech in the new video: 'original' (keep it) or a language to dub to: dansk, svensk, norsk, engelsk"},
         "undertekster": {"type": "string", "description": "Subtitles in the new video: 'ingen' or dansk, svensk, norsk, engelsk"},
         "talesprog": {"type": "string", "description": "The language spoken in the video (default dansk)"},
         "lyt_igen": {"type": "boolean", "description": "Make the spoken-language text again even if it exists (default false)"},
         "klip": {"type": "boolean", "description": ("Cut the video first (default false): the app finds long silent "
                  "pauses, you suggest what to cut (waiting before the meeting starts, breaks, the end after the "
                  "meeting, inappropriate, off-topic or unintelligible parts), the user reviews the cuts on a "
                  "timeline, and subtitles/dub are made for the cut video. Only cutting: tale 'original', "
                  "undertekster 'ingen'.")}},
         "required": ["video", "tale", "undertekster"]}},
    {"name": "vent",
     "description": ("Wait for news on a job: returns at every 10 % of progress, when it is Claude's turn, on pause, "
                     "error or when done, or after at most ~45 seconds. Call it again until the job is done. Tell the "
                     "user briefly about each 10 %."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string", "description": "The job id from start_video (default: the latest job)"}}}},
    {"name": "status",
     "description": "The current state of a job (default the latest). Use it to pick up a job again, e.g. in a new chat.",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}}},
    {"name": "hent_tekst",
     "description": ("When a job is in 'claude_turn': get the task (what to correct/translate, word list, rules) and the "
                     "subtitles as SRT, a range of blocks at a time (about 100-150 blocks per call works well)."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "fra": {"type": "integer", "description": "First block number (default 1)"},
         "antal": {"type": "integer", "description": "Number of blocks (default 120)"},
         "skill": {"type": "integer", "description": ("The 'Skill-version' number from the videosprog skill. Leave "
                   "it out if you do not have the skill.")}}, "required": ["job_id"]}},
    {"name": "gem_tekst",
     "description": ("Hand back blocks for one language as SRT with the SAME numbers and time codes as from hent_tekst "
                     "(corrected text in the spoken language, or a translation). Checked at once; the answer says what "
                     "is wrong or still missing. When every language is complete, the app carries on (dub/burn-in) by "
                     "itself; then call 'vent' until done. Put corrected misheard words in 'ordliste' as lines "
                     "'wrong => right' so the app learns them."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "sprog": {"type": "string", "description": "Language code of the blocks: da, sv, no or en"},
         "srt": {"type": "string", "description": "The blocks as SRT"},
         "ordliste": {"type": "string", "description": "Optional: corrected words, one per line 'forkert => rigtigt'"}},
         "required": ["job_id", "sprog", "srt"]}},
    {"name": "foreslaa_klip",
     "description": ("When the job's task includes cutting: hand in your cut suggestions, one per line "
                     "'<first block>-<last block> <category> <short reason>' (categories: start, pause, slut, "
                     "upassende, offtopic, uklart), e.g. '1-14 start Folk kommer ind og tester lyd'. Block numbers "
                     "are those from hent_tekst. Can be called several times (lines are added); set faerdig=true "
                     "when you have read the whole text, also when there is nothing to cut."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "klip": {"type": "string", "description": "Cut suggestions, one per line (may be empty)"},
         "faerdig": {"type": "boolean", "description": "true when all suggestions are in"}},
         "required": ["job_id"]}},
    {"name": "godkend_klip",
     "description": ("While the user reviews the cuts in the app (state 'review_cuts'): apply what the user told you "
                     "in the chat. 'behold' = numbers of cuts NOT to make (the content stays in), 'tilfoej' = extra "
                     "cuts, one per line, by time 'mm:ss-mm:ss <category> <reason>' or by block. Unless "
                     "vent_paa_brugeren is true, the app then cuts the video at once and carries on, so ask the "
                     "user first whether the cuts are right."),
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "behold": {"type": "array", "items": {"type": "integer"}, "description": "Numbers of cuts to keep in the video"},
         "tilfoej": {"type": "string", "description": "Extra cuts, one per line"},
         "vent_paa_brugeren": {"type": "boolean", "description": "true: only update the list; the user approves in the app"}},
         "required": ["job_id"]}},
    {"name": "annuller",
     "description": ("Stop a job in the app (default the one the app is working on). Use it when the user asks to "
                     "stop, or when a job hangs and the app says it is busy with another video; ask the user first."),
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}}},
    {"name": "find_videoer",
     "description": "List recent video files in Videos, Downloads and Desktop (or a given folder), newest first.",
     "inputSchema": {"type": "object", "properties": {"mappe": {"type": "string"}}}},
]

# --- Helpers -------------------------------------------------------------------------

def _lang(value: str | None, allow_none: tuple[str, ...] = ()):
    v = (value or "").strip().lower()
    if v in allow_none or not v:
        return None
    if v in LANG_ALIASES:
        return LANG_ALIASES[v]
    raise ValueError(f"Ukendt sprog: {value!r}. Brug dansk, svensk, norsk eller engelsk.")


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
        lines.append(f"Resultat: {st['result']}")
    if state in ("running", "paused") and st.get("percent") is not None:
        lines.append(f"Fremdrift: {st['percent']} % - {st.get('step', '')} {st.get('eta', '')}".rstrip())
    if st.get("message"):
        lines.append(st["message"])
    if state == "review_cuts":
        lines += _cut_list(job_id)
    hint = {
        "queued": "Kald 'vent' igen.",
        "running": "Kald 'vent' igen.",
        "paused": "Brugeren har sat jobbet på pause i appen. Kald 'vent' igen, når hun trykker Fortsæt.",
        "claude_turn": "Det er din tur: kald 'hent_tekst' fra blok 1, ret/oversæt, og aflever med 'gem_tekst'.",
        "review_cuts": ("Brugeren gennemser klippene i appen. Fortæl kort, hvad der klippes, og spørg, om det er i "
                        "orden. Retter brugeren noget i chatten, så brug 'godkend_klip'. Godkender brugeren i appen, "
                        "fortsætter den selv: kald 'vent'."),
        "done": "Fortæl brugeren, at videoen er færdig og kan afspilles i appen.",
        "error": "Fortæl brugeren om fejlen. Logfilen kan åbnes fra appens Indstillinger.",
        "cancelled": "Jobbet er stoppet.",
        "busy": ("Appen er i gang med en anden video. Prøv igen, når den er færdig. Hænger den anden video, så "
                 "spørg brugeren, om du skal stoppe den med 'annuller'."),
    }.get(state)
    if hint:
        lines.append(hint)
    return "\n".join(lines)

def _cut_list(job_id: str) -> list[str]:
    data = jobs.read_json(jobs.root() / job_id / "cuts.json") or {}
    cuts = data.get("cuts") or []
    if not cuts:
        return ["Ingen klip på listen."]
    out = ["Klip (nummer: tid, længde, hvorfor):"]
    for i, c in enumerate(cuts, 1):
        kind = cutting.KINDS.get(c.get("kind"), ("Klip",))[0]
        state = "" if c.get("on", True) else "  [beholdes - klippes ikke]"
        what = f"{kind}: {c.get('reason')}" if c.get("reason") else kind
        text = f' - "{c["text"]}"' if c.get("text") else ""
        out.append(f"{i}. {cutting.fmt_clock(c['start'])}-{cutting.fmt_clock(c['end'])} "
                   f"({cutting.fmt_length(c['end'] - c['start'])}) {what}{state}{text}")
    if data.get("summary"):
        out.append(data["summary"])
    return out

# --- Tools ---------------------------------------------------------------------------

_last_decile: dict[str, int] = {}


def tool_start_video(video: str, tale: str, undertekster: str, talesprog: str = "dansk", lyt_igen: bool = False,
                     klip: bool = False) -> str:
    p = Path(video.strip().strip('"'))
    if not p.is_file():
        return f"Videoen findes ikke: {p}. Bed brugeren om den fulde sti, eller brug 'find_videoer'."
    lang = _lang(talesprog) or LANG_ALIASES["dansk"]
    speech = _lang(tale, ("original", "originalt", "behold", "samme", "none"))
    subs = _lang(undertekster, ("ingen", "none", "nej", "no subtitles"))
    if speech == lang:
        speech = None
    if not klip and not speech and not subs and not lyt_igen:
        return "Vælg mindst én ting: ny tale, undertekster eller klip (klip=true)."
    job_id = jobs.new_request(str(p), lang.name, speech and speech.name, subs and subs.name, bool(lyt_igen), bool(klip))
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
    head = f"Jeg har startet {APP_NAME}. " if started else ""
    if st.get("state") in (None, "queued"):
        return head + (f"Job {job_id} er sendt til appen, men appen har ikke taget det endnu (måske venter den på "
                       "brugeren, fx et spørgsmål eller filer der skal hentes). Kald 'vent'.")
    return head + _describe(job_id, st)


def tool_vent(job_id: str | None = None) -> str:
    job_id = job_id or jobs.latest_job()
    if not job_id:
        return "Der er ingen job. Start et med 'start_video'."
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


def tool_annuller(job_id: str | None = None) -> str:
    job_id = job_id or _active_job()
    if not job_id or not (jobs.root() / job_id / "status.json").exists():
        return "Der er intet job at stoppe."
    st = jobs.status(job_id)
    if st.get("state") not in ACTIVE:
        return f"Job {job_id} kører ikke (det er {jobs.STATES.get(st.get('state'), st.get('state'))})."
    if not _app_running():
        # Nobody will finish it: the app was closed while the job was open
        Path(jobs.root() / "inbox" / f"{job_id}.json").unlink(missing_ok=True)
        jobs.set_status(job_id, state="cancelled", message="Stoppet fra Claude (appen kørte ikke).")
        return f"Job {job_id} er stoppet. Appen kørte ikke, så der var intet at lukke."
    jobs.write_json(jobs.root() / job_id / "cancel.json", {"time": time.time()})
    deadline = time.time() + 20
    while time.time() < deadline:
        st = jobs.status(job_id)
        if st.get("state") not in ACTIVE:
            return _describe(job_id, st)
        time.sleep(0.5)
    return (f"Jeg har bedt appen stoppe job {job_id}, men den har ikke gjort det endnu. Bed brugeren trykke "
            "Annullér i appen.")


def tool_status(job_id: str | None = None) -> str:
    job_id = job_id or jobs.latest_job()
    if not job_id:
        return "Der er ingen job endnu."
    return _describe(job_id, jobs.status(job_id))


def _task(job_id: str) -> dict:
    t = jobs.read_json(jobs.root() / job_id / "task.json")
    if not t:
        raise ValueError("Jobbet er ikke klar til Claude endnu. Kald 'vent'.")
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
    if not m:
        return "intet"
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
    what = "har ikke skill'en videosprog" if have is None else f"har en gammel udgave af skill'en (version {have})"
    return (f"OBS: Du {what}; den nyeste er version {SKILL_VERSION}. Fortsæt opgaven, men sig til brugeren til sidst, "
            "at Claude-skill'en skal opdateres: i Videosprog under Indstillinger -> 'Gem Claude-skill...', og så "
            "upload filen i Claude under Customize -> Skills (slet den gamle først).\n")


def tool_hent_tekst(job_id: str, fra: int = 1, antal: int = 120, skill: int | None = None) -> str:
    task = _task(job_id)
    note = _skill_note(job_id, skill) if int(fra or 1) == 1 or skill is not None else ""
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    fra = max(1, int(fra or 1))
    antal = max(1, min(int(antal or 120), 400))
    part = src[fra - 1:fra - 1 + antal]
    names = {l.code: l.name.lower() for l in LANGUAGES}
    todo = []
    if task["check"]:
        todo.append(f"ret teksten på {names[task['src']]} (fejlhørte ord, især navne og fagord) og aflever den som '{task['src']}'")
    for c in task["targets"]:
        todo.append(f"oversæt til {names[c]} og aflever som '{c}'")
    if task.get("cut"):
        todo.append("foreslå klip og aflever dem med 'foreslaa_klip' (faerdig=true til sidst)")
    lines = [
        f"Opgave for job {job_id}: " + "; ".join(todo) + ".",
        f"Teksten har {task['blocks']} blokke. Her er blok {fra}-{fra + len(part) - 1}.",
        "Regler: samme numre og præcis samme tidskoder; slå ikke blokke sammen og del dem ikke; højst 2 linjer á "
        "ca. 42 tegn; naturligt talesprog; produktnavne og skærmbilleder staves som i ordlisten og oversættes ikke.",
        "Mangler stadig: " + _still_missing(job_id, task),
    ]
    if task.get("words"):
        lines.append("Ordliste (kendte ord): " + ", ".join(task["words"]))
    if task.get("rules"):
        lines.append("Typiske fejlhøringer: " + "; ".join(f"{w} => {r}" for w, r in task["rules"]))
    if task.get("cut"):
        lines += CUT_RULES
        lines.append("Videoen er " + cutting.fmt_clock(task.get("duration", 0)) + " lang.")
    lines += ["", format_srt(part, fra).rstrip()]
    if fra - 1 + antal < len(src):
        lines += ["", f"Næste: hent_tekst(fra={fra + len(part)})."]
    return note + "\n".join(lines)


def tool_gem_tekst(job_id: str, sprog: str, srt: str, ordliste: str = "") -> str:
    task = _task(job_id)
    code = (sprog or "").strip().lower()
    code = LANG_ALIASES[code].code if code in LANG_ALIASES else code
    if code not in task["codes"]:
        return f"Sproget '{sprog}' indgår ikke i opgaven. Brug et af: {', '.join(task['codes'])}."
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    by_time = {(c.start, c.end): i for i, c in enumerate(src, 1)}
    got = parse_srt_text(srt)
    if not got:
        return "Der var ingen SRT-blokke i 'srt'. Send blokkene som SRT (nummer, tidskode, tekst)."
    path = jobs.root() / job_id / "parts" / f"{code}.json"
    have = jobs.read_json(path, {}) or {}
    problems, saved = [], 0
    for c in got:
        idx = by_time.get((c.start, c.end))
        if idx is None:
            # tolerate 1 ms rounding
            idx = next((i for (a, b), i in by_time.items() if abs(a - c.start) <= 1 and abs(b - c.end) <= 1), None)
        if idx is None:
            problems.append(f"tidskoden {c.start / 1000:.3f}-{c.end / 1000:.3f} s findes ikke i originalen")
            continue
        if not c.text.strip():
            problems.append(f"blok {idx} er tom")
            continue
        have[str(idx)] = c.text
        saved += 1
    jobs.write_json(path, have)
    words = []
    for line in (ordliste or "").splitlines():
        m = re.match(r"\s*[-*•]?\s*(.+?)\s*(?:=>|->|→)\s*(.+?)\s*$", line)
        if m:
            words.append([m.group(1), m.group(2)])
    if words:
        wpath = jobs.root() / job_id / "parts" / "words.json"
        jobs.write_json(wpath, (jobs.read_json(wpath, []) or []) + words)
    msg = [f"Gemt {saved} blokke på '{code}'."]
    if problems:
        msg.append(f"{len(problems)} blokke blev ikke gemt: " + "; ".join(problems[:8]) + (" ..." if len(problems) > 8 else ""))
    msg.append(_maybe_complete(job_id, task, src))
    return "\n".join(msg)


def _still_missing(job_id: str, task: dict) -> str:
    parts = []
    missing = _missing(job_id, task)
    if missing:
        parts.append(_fmt_missing(missing))
    if task.get("cut") and not (jobs.read_json(jobs.root() / job_id / "parts" / "klip.json", {}) or {}).get("done"):
        parts.append("klipforslag (foreslaa_klip med faerdig=true)")
    return "; ".join(parts) or "intet"


def _maybe_complete(job_id: str, task: dict, src=None) -> str:
    """When every language and the cut suggestions are in, hand everything to the app."""
    left = _still_missing(job_id, task)
    if left != "intet":
        return f"Mangler stadig: {left}."
    if src is None:
        src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    out = {}
    for c in task["codes"]:
        parts = jobs.read_json(jobs.root() / job_id / "parts" / f"{c}.json", {}) or {}
        out[c] = [[s.start, s.end, parts[str(i)]] for i, s in enumerate(src, 1)]
    allwords = jobs.read_json(jobs.root() / job_id / "parts" / "words.json", []) or []
    k = jobs.read_json(jobs.root() / job_id / "parts" / "klip.json", {}) or {}
    jobs.write_json(jobs.root() / job_id / "complete.json", {"codes": out, "words": allwords, "cuts": k.get("lines", [])})
    if task.get("cut"):
        return ("Alt er afleveret. Appen viser nu klippene for brugeren på en tidslinje. Kald 'vent': når den svarer "
                "'review_cuts', så fortæl brugeren kort, hvad der klippes.")
    return "Alle sprog er komplette. Appen fortsætter nu selv (indtaling/indbrænding). Kald 'vent', til den er færdig."


def tool_foreslaa_klip(job_id: str, klip: str = "", faerdig: bool = False) -> str:
    task = _task(job_id)
    if not task.get("cut"):
        return "Dette job skal ikke klippes. Brug kun 'gem_tekst'."
    src = jobs.cues_from_json(jobs.read_json(jobs.root() / job_id / "source.json", []))
    lines = [l.strip() for l in (klip or "").splitlines() if l.strip()]
    cuts, bad = cutting.parse_cut_lines(lines, src, task.get("duration") or (src[-1].end if src else 0))
    path = jobs.root() / job_id / "parts" / "klip.json"
    k = jobs.read_json(path, {}) or {}
    good = [l for l in lines if l not in bad]
    k["lines"] = (k.get("lines") or []) + good
    k["done"] = bool(faerdig) or bool(k.get("done"))
    jobs.write_json(path, k)
    msg = [f"Gemt {len(good)} klipforslag ({len(k['lines'])} i alt)."]
    for c in cuts:
        msg.append(f"  {cutting.fmt_clock(c.start)}-{cutting.fmt_clock(c.end)} {c.kind}: {c.reason}")
    if bad:
        msg.append("Kunne ikke læse disse linjer (brug '<blok>-<blok> <kategori> <grund>'): " + " | ".join(bad))
    msg.append(_maybe_complete(job_id, task, src))
    return "\n".join(msg)


def tool_godkend_klip(job_id: str, behold: list | None = None, tilfoej: str = "", vent_paa_brugeren: bool = False) -> str:
    st = jobs.status(job_id)
    if st.get("state") != "review_cuts":
        return _describe(job_id, st) + "\nKlippene kan kun ændres, mens brugeren gennemser dem (review_cuts)."
    decision = jobs.root() / job_id / "cut_decision.json"
    jobs.write_json(decision, {
        "keep": [int(x) for x in (behold or [])], "add": [l for l in (tilfoej or "").splitlines() if l.strip()],
        "approve": not vent_paa_brugeren, "created": time.time()})
    deadline = time.time() + 15
    while time.time() < deadline and decision.exists():
        time.sleep(0.5)
    if decision.exists():
        return "Appen har ikke taget imod det endnu (er den lukket?). Kald 'status' om lidt."
    data = jobs.read_json(jobs.root() / job_id / "cuts.json") or {}
    head = data.get("decision_message") or "Appen har opdateret klippene."
    if vent_paa_brugeren:
        return head + "\n" + "\n".join(_cut_list(job_id)) + "\nBrugeren godkender i appen (knappen 'Klip videoen'). Kald 'vent'."
    return head + "\nAppen klipper nu videoen. Kald 'vent', til den er færdig."


def tool_find_videoer(mappe: str | None = None) -> str:
    dirs = [Path(mappe)] if mappe else [Path.home() / "Videos", downloads_dir(), Path.home() / "Desktop",
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
        return "Fandt ingen videoer. Bed brugeren om den fulde sti."
    return "\n".join(f"{time.strftime('%d-%m-%Y %H:%M', time.localtime(f.stat().st_mtime))}  {f}" for f in found)


HANDLERS = {"start_video": tool_start_video, "vent": tool_vent, "status": tool_status,
            "hent_tekst": tool_hent_tekst, "gem_tekst": tool_gem_tekst, "find_videoer": tool_find_videoer,
            "foreslaa_klip": tool_foreslaa_klip, "godkend_klip": tool_godkend_klip, "annuller": tool_annuller}

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
        new = " til version " + cur
    elif _exe_stamp() != _EXE_STAMP:
        try:
            new = " til version " + (_EXE.parent / "version.txt").read_text(encoding="utf-8").strip()
        except OSError:
            new = ""
    else:
        return None
    return (f"{APP_NAME} er blevet opdateret{new}, mens Claude kørte, så Claude bruger stadig den gamle "
            f"udgave ({APP_VERSION}) og kan ikke styre appen. Bed brugeren lukke Claude helt (højreklik på "
            "Claude-ikonet ved uret -> Quit) og åbne Claude igen. Bed så om det samme igen i en ny chat.")

INSTRUCTIONS = ("Videosprog klipper, laver undertekster og dub af videoer lokalt på brugerens pc. Brug start_video "
                "(klip=true, hvis videoen skal klippes først), følg jobbet med vent (fortæl brugeren om hver 10 %), og "
                "når det er din tur (claude_turn), så hent teksten med hent_tekst i bidder, ret/oversæt med skill'en "
                "videosprog, aflever med gem_tekst og evt. klipforslag med foreslaa_klip. Mens brugeren gennemser "
                "klippene (review_cuts), kan du rette dem med godkend_klip. Vil brugeren stoppe, eller hænger et job, så "
                "brug annuller (spørg først). Appen validerer og laver resten. Svar brugeren på dansk og kort.")

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
                      "serverInfo": {"name": "videosprog", "version": APP_VERSION},
                      "instructions": INSTRUCTIONS}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            log.info("MCP %s %s", name, {k: (v if len(str(v)) < 200 else f"<{len(str(v))} tegn>") for k, v in args.items()})
            try:
                stale = _outdated()
                if stale:
                    log.warning("MCP server %s is outdated: the exe has been replaced", APP_VERSION)
                    result = {"content": [{"type": "text", "text": stale}], "isError": True}
                else:
                    result = {"content": [{"type": "text", "text": HANDLERS[name](**args)}]}
            except Exception as e:
                log.error("MCP tool %s failed: %s", name, traceback.format_exc())
                result = {"content": [{"type": "text", "text": f"Fejl: {e}"}], "isError": True}
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
        # Long calls (vent) run in their own thread, so pings and other calls are answered meanwhile.
        threading.Thread(target=_handle, args=(req,), daemon=True).start()
    log.info("MCP server stop")
