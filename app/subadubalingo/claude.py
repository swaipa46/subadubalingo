"""The Claude step: the user copies a prompt into a Claude chat and pastes the answer back.

One round per part of the video: Claude corrects the subtitles in the spoken language (product names and
other known words), translates them to the languages the new video needs, and lists words it corrected.
Those words are saved to corrections.txt, so the next transcription gets them right and the next prompt
carries them along. The `subadubalingo` skill holds the same rules and the word list for claude.ai;
the prompt works on its own too, in case the skill is not installed or not picked up.

The protocol (headers, section markers, cut kinds) and the prompts are English, the domain language
(docs/glossary.md); Claude answers the user in Danish. Answers in the Danish protocol of skill version 3 and
earlier (=== ordliste ===, === klip ===, === slut ===, VIDEOSPROG-SVAR) are still read.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from pathlib import Path

from .common import APP_NAME, Cue, Language, LANGUAGES, format_srt, log, parse_srt_text
from . import engine

SKILL_NAME = "subadubalingo"
OLD_SKILL_NAMES = ("videosprog",)   # the skill under the app's earlier name; the guide says to delete it
# Bump when SKILL_MD (or what the program expects from Claude) changes. The program knows which skill version it
# ships with: after an update it guides the user to upload the new skill, and Claude reports the version it has
# (answer file header "skill=N", or hent_tekst(skill=N)), so a forgotten update is caught too.
# 1 = before cutting (v1.8), 2 = cutting (v1.9.0-1.9.2), 3 = version stamp, 4 = SubaDubaLingo, English protocol.
SKILL_VERSION = 4
LANG_BY_CODE = {l.code: l for l in LANGUAGES}

# --- Parts ------------------------------------------------------------------------------

def part_size(n_languages: int) -> int:
    """Blocks per round, so Claude's answer (one SRT per language) is not cut off."""
    return 120 if n_languages <= 2 else 90


def split_parts(cues: list[Cue], n_languages: int) -> list[list[Cue]]:
    size = part_size(n_languages)
    n = max(1, math.ceil(len(cues) / size))
    size = math.ceil(len(cues) / n)
    return [cues[i:i + size] for i in range(0, len(cues), size)]

# --- Word list (shared with corrections.txt) ------------------------------------------------

def word_rules() -> list[tuple[str, str]]:
    """(wrong, right) pairs from corrections.txt."""
    try:
        lines = engine.corrections_file().read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            wrong, right = (x.strip() for x in line.split("=", 1))
            if wrong and right:
                out.append((wrong, right))
    return out


def known_words() -> list[str]:
    return sorted({r for _, r in word_rules()}, key=str.lower)


def add_words(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Append new (wrong, right) pairs to corrections.txt; returns the ones that were new."""
    have = {w.lower() for w, _ in word_rules()}
    new = [(w, r) for w, r in pairs if w.lower() not in have and w.lower() != r.lower()]
    if new:
        f = engine.corrections_file()
        text = f.read_text(encoding="utf-8-sig")
        if not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{w} = {r}\n" for w, r in new)
        f.write_text(text, encoding="utf-8")
        log.info("learned words: %s", new)
    return new

# --- Prompt ---------------------------------------------------------------------------------
#
# Claude answers in ONE code block, with a marker line before each language:
#     === da ===      (corrected text in the spoken language)
#     === sv ===      (translations)
#     === words ===   (wrong => right)
# One block means one copy, whichever copy button the user uses (the block's or the whole answer's).

NAMES_EN = {"da": "Danish", "sv": "Swedish", "no": "Norwegian", "en": "English"}
CUT_TASK = "suggest what to cut out"
CUT_RULES = [
    "Cuts: suggest blocks to cut out of the video (a recording of a Teams meeting or webinar). Kinds:",
    "  start = before the meeting really begins (people joining, sound checks, \"can you hear me\"),",
    "  pause = interruptions and waiting along the way, end = after the meeting is over (chatter, \"is it off?\"),",
    "  inappropriate = what should not be kept (private matters, customers or colleagues mentioned in a "
    "vulnerable context, crude language), offtopic = fooling around and talk off the subject, unclear = what "
    "cannot be understood, not even in context.",
    "  One line per cut: <first block>-<last block> <kind> <short reason>, e.g. \"1-14 start People join and test "
    "the sound\". Cut whole blocks, and be careful: only cut what clearly does not belong. The app finds long "
    "silent pauses itself. The user reviews every cut before anything is cut. Write the reason in Danish (the user "
    "reads it).",
]


def _tasks(src: Language, proofread: bool, targets: list[Language], cut: bool) -> list[str]:
    tasks = []
    if proofread:
        tasks.append(f"correct the {NAMES_EN[src.code]} subtitles (misheard words, especially names and product terms)")
    if targets:
        tasks.append("translate them to " + " and ".join(NAMES_EN[t.code] for t in targets))
    if cut:
        tasks.append(CUT_TASK)
    return tasks


def prompt(src: Language, proofread: bool, targets: list[Language], part: list[Cue], first_number: int,
           idx: int, n_parts: int, cut: bool = False) -> str:
    order = ([src.code] if proofread else []) + [t.code for t in targets]
    part_note = (f"Part {idx + 1} of {n_parts} (blocks {first_number}-{first_number + len(part) - 1}). "
                 if n_parts > 1 else "")
    words = known_words()
    rules = word_rules()
    lines = [
        f"Use the {SKILL_NAME} skill if you have it.",
        "",
        f"Subtitles from the app {APP_NAME}. {part_note}Task: " + ", and ".join(_tasks(src, proofread, targets, cut)) + ".",
        "",
        "Rules:",
        "- Keep the numbering and time codes exactly. The same number of blocks; do not merge or split blocks.",
        "- At most 2 lines per block and about 42 characters per line. Natural spoken language, as one would say it.",
        "- Product and screen names are spelled as in the word list and not translated.",
        "- Answer directly in the chat (no files, no code), with EVERYTHING in ONE code block. Before each language "
        "a line with === and the language code, in this order: " + ", ".join(f"=== {c} ===" for c in order) + ".",
    ]
    if cut:
        lines += ["- Then, in the same code block, a line === cuts === with your cut suggestions for this part (no "
                  "lines if there are none). The numbers are the block numbers above."] + ["  " + r for r in CUT_RULES]
    lines += [
        "- Last in the same code block: a line === words === and then the misheard words you corrected, one per "
        "line: wrong => right. No lines after it if there were none.",
        "- No text before or after the code block.",
        "",
        "Example of the format:",
        "```",
        f"=== {order[0]} ===",
        "1",
        "00:00:01,000 --> 00:00:03,000",
        "Tekst",
        "",
    ] + (["=== cuts ===", "1-3 start Folk kommer ind og tester lyd", ""] if cut else []) + [
        "=== words ===",
        "memberkær => Membercare",
        "```",
    ]
    if words:
        lines += ["", "Word list (known words): " + ", ".join(words)]
    if rules:
        lines += ["Typical mishearings: " + "; ".join(f"{w} => {r}" for w, r in rules[:60])]
    lines += ["", f"The subtitles ({NAMES_EN[src.code]}):", "```", format_srt(part, first_number).rstrip(), "```"]
    return "\n".join(lines) + "\n"

# --- Answer -----------------------------------------------------------------------------------

_NAMES = {"da": ["da", "dansk", "danske"], "sv": ["sv", "svensk", "svenske", "svenska"],
          "no": ["no", "nb", "norsk", "norske"], "en": ["en", "engelsk", "engelske", "english"]}
_LABEL = {alias: code for code, names in _NAMES.items() for alias in names}
# Section names, also the Danish ones of skill version 3 and earlier
_LABEL.update({"words": "words", "corrections": "words", "ordliste": "words", "rettelser": "words", "ord": "words",
               "cuts": "cuts", "klip": "cuts", "end": "end", "slut": "end"})
# A marker line: "=== sv ===", "### Svensk", "**srt-sv**", "Svensk:" ... (alone on its line)
MARKER_RE = re.compile(r"^[ \t>#*=_-]*(?:srt[-_ ])?([A-Za-zæøåÆØÅ]+)[ \t]*(?:\([^)]*\))?[ \t:*=#_-]*$")
# The format the prompt asks for ("=== sv ==="): a marker wherever it stands.
STRICT_MARKER_RE = re.compile(r"^={2,}\s*(?:srt[-_ ]?)?([A-Za-zæøåÆØÅ]+)\s*={2,}$")
FENCE_RE = re.compile(r"^```[ \t]*([^\n`]*)$")


def _label(line: str) -> str | None:
    m = MARKER_RE.match(line.strip())
    if not m or not line.strip():
        return None
    return _LABEL.get(m.group(1).lower())


def _sections(text: str) -> dict[str, list[str]]:
    """The answer's lines per section: language codes, "words", "cuts", "end"."""
    sections: dict[str, list[str]] = {}
    current = None
    # A marker must start a paragraph, so a subtitle line that just says "Norsk" is not taken for one.
    para_start = True
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        f = FENCE_RE.match(raw.strip())
        if f:
            lab = _LABEL.get(re.sub(r"^srt[-_ ]?", "", f.group(1).strip().lower()))
            if lab:
                current = lab
            para_start = True
            continue
        strict = STRICT_MARKER_RE.match(raw.strip())
        lab = _LABEL.get(strict.group(1).lower()) if strict else (_label(raw) if para_start else None)
        para_start = not raw.strip()
        if lab:
            current = lab
            sections.setdefault(lab, [])
            para_start = True
            continue
        if current:
            sections.setdefault(current, []).append(raw)
    return sections


def parse_answer(text: str) -> tuple[dict[str, list[Cue]], list[tuple[str, str]]]:
    """Per language code the SRT blocks, and the corrected words. Reads the one-block format, separate
    labelled code blocks (```srt-sv), and headings before blocks; fence lines are treated as separators."""
    sections = _sections(text)
    by_lang = {}
    for code, lines in sections.items():
        if code in ("words", "end", "cuts"):
            continue
        cues = parse_srt_text("\n".join(lines))
        if cues:
            by_lang[code] = cues
    return by_lang, parse_words(sections.get("words", []))


def answer_cuts(text: str) -> list[str] | None:
    """The lines of the answer's === cuts === section (cut suggestions), or None when it has none."""
    sections = _sections(text)
    if "cuts" not in sections:
        return None
    return [l.strip() for l in sections["cuts"] if l.strip()]


def parse_words(lines: list[str]) -> list[tuple[str, str]]:
    words: list[tuple[str, str]] = []
    for line in lines:
        m = re.match(r"\s*[-*•]?\s*(.+?)\s*(?:=>|->|→|=)\s*(.+?)\s*$", line)
        if m and m.group(1) and m.group(2):
            words.append((m.group(1).strip('"\'`'), m.group(2).strip('"\'`')))
    return words


def check_answer(part: list[Cue], codes: list[str], text: str, have: dict | None = None
                 ) -> tuple[dict[str, list[Cue]], list, str | None]:
    """Adds what the pasted text holds to `have` (earlier pastes for this part). Returns
    (languages so far, new words, None when all `codes` are there and valid, else a message)."""
    have = dict(have or {})
    by_lang, words = parse_answer(text)
    if not by_lang:
        # A bare SRT (copied from one of several code blocks, without its label): take it as the first
        # language still missing, or Swedish when it is clearly Swedish (ä/ö). The status shows which.
        bare = parse_srt_text(text)
        missing = [c for c in codes if c not in have]
        if bare and missing:
            body = " ".join(c.text for c in bare).lower()
            swedish = body.count("ä") + body.count("ö") >= 3
            guess = "sv" if swedish and "sv" in missing else next((c for c in missing if c != "sv"), missing[0])
            by_lang = {guess: bare}
    problems = []
    for code, cues in by_lang.items():
        if code not in codes:
            continue
        msg = engine.compare(part, cues)
        if msg:
            problems.append(f"{LANG_BY_CODE[code].name}: {msg}")
        else:
            have[code] = cues
    if problems:
        return have, words, problems[0]
    missing = [LANG_BY_CODE[c].name.lower() for c in codes if c not in have]
    if not by_lang:
        return have, words, ("Der er ikke noget svar fra Claude i udklipsholderen. Klik på kopiknappen øverst på "
                             "Claudes kodeblok (eller under hele svaret) og prøv igen.")
    if missing:
        return have, words, f"Mangler stadig: {', '.join(missing)}."
    return have, words, None

# --- The work file: the whole text in one file instead of many copy-paste rounds -------------------
#
# The program writes "<video> - til Claude.txt" with a header line carrying a job id, the task and the
# subtitles. The user pastes the file into a Claude chat; the skill (Claude with code execution) writes
# "<video> - fra Claude.txt" in a fixed template, which the user downloads. The program watches the Downloads
# folder for a file whose first line has the same id, checks it and carries on.

TASK_HEADER = "SUBADUBALINGO-TASK"
ANSWER_HEADER = "SUBADUBALINGO-ANSWER"
OLD_ANSWER_HEADERS = ("VIDEOSPROG-SVAR", "VIDEO-UNDERTEKSTER-SVAR")   # files under the app's earlier names


def work_file_text(src: Language, proofread: bool, targets: list[Language], cues: list[Cue], job_id: str,
                   answer_name: str, cut: bool = False) -> str:
    order = ([src.code] if proofread else []) + [t.code for t in targets]
    words = known_words()
    rules = word_rules()
    lines = [
        f"{TASK_HEADER} id={job_id} languages={','.join(order)} blocks={len(cues)}" + (" cut=yes" if cut else ""),
        "",
        f"To Claude: use the {SKILL_NAME} skill. Task: " + ", and ".join(_tasks(src, proofread, targets, cut)) + ".",
        f"Make the answer file \"{answer_name}\" as a file the user can download, following the template in the skill:",
        f"first line \"{ANSWER_HEADER} id={job_id} skill=<the version number from the skill>\", then "
        + ", ".join(f"=== {c} ===" for c in order) + " with all " + str(len(cues)) + " blocks each, "
        + ("then === cuts === with your cut suggestions, " if cut else "")
        + "then === words === and finally === end ===.",
        "Keep the numbering and time codes exactly. Do not write the subtitles in the chat. Talk to the user in Danish.",
    ]
    if cut:
        lines += [""] + CUT_RULES
    if words:
        lines += ["", "Word list (known words): " + ", ".join(words)]
    if rules:
        lines += ["Typical mishearings: " + "; ".join(f"{w} => {r}" for w, r in rules[:80])]
    lines += ["", f"=== {src.code} ===", format_srt(cues).rstrip(), ""]
    return "\n".join(lines) + "\n"


def answer_skill(text: str) -> int | None:
    """The skill version Claude wrote in the answer file's first line ("skill=4"), or None (no skill, or an old one)."""
    first = text.lstrip("\ufeff").split("\n", 1)[0]
    m = re.search(r"\bskill=(\d+)", first)
    return int(m.group(1)) if m else None


def answer_id(text: str) -> str | None:
    """The job id from an answer file's first line, or None if it is not an answer file."""
    first = text.lstrip("﻿").split("\n", 1)[0].strip()
    m = re.match(rf"(?:{'|'.join((ANSWER_HEADER,) + OLD_ANSWER_HEADERS)})\s+id=(\S+)", first)
    return m.group(1) if m else None


def check_file_answer(cues: list[Cue], codes: list[str], text: str, cut: bool = False
                      ) -> tuple[dict[str, list[Cue]], list, str | None]:
    """Check a whole answer file: every language complete, the cut section when asked for, and the end marker."""
    have, words, msg = check_answer(cues, codes, text)
    if msg is None and cut and answer_cuts(text) is None:
        msg = "Svarfilen mangler afsnittet === cuts === med klipforslag (det må gerne være tomt)."
    if msg is None and not re.search(r"^={2,}\s*(?:end|slut)\s*={2,}\s*$", text, re.M | re.I):
        msg = "Svarfilen slutter ikke med === end ===, så den er nok ikke blevet skrevet færdig."
    return have, words, msg


def followup_text(answer_name: str, msg: str, cut: bool = False) -> str:
    """A message the user can paste to Claude when the answer file was incomplete (msg is the app's Danish text)."""
    return (f"The answer file was not right. The app says (in Danish): {msg}\n"
            f"Please make \"{answer_name}\" again, complete and following the template in the {SKILL_NAME} skill "
            f"(every language, every block, {'=== cuts ===, ' if cut else ''}=== end === last). Answer me in Danish.")

# --- The skill ------------------------------------------------------------------------------------

SKILL_MD = """---
name: subadubalingo
description: Corrects and translates subtitles from the Windows app "SubaDubaLingo" (formerly "Videosprog") between Danish, Swedish, Norwegian and English, suggests cuts in recordings of Teams meetings and webinars (waiting, breaks, inappropriate or unclear passages) and learns product terms such as Membercare. Use it when the user wants subtitles or a dub of a video or wants a recording cut (e.g. "lav denne video på svensk med norske undertekster", "klip ventetiden væk"), when the SubaDubaLingo tools (start_video, wait, get_text, save_text) are available, when the user sends a file "... - til Claude.txt" (first line SUBADUBALINGO-TASK or VIDEOSPROG-OPGAVE), or pastes subtitles from SubaDubaLingo.
---

# SubaDubaLingo: subtitles, dubs and cuts

**Skill version: 4.** The app asks for this number to see whether the user has the newest skill: write it in the
answer file's first line (`skill=4`) and pass it to `get_text` (`skill: 4`).

The user makes subtitles and dubs of videos in the app SubaDubaLingo. The app writes the text with speech
recognition; you correct and translate it. The app reads your answer by machine, so follow the format exactly.
**Talk to the user in Danish** (they are not technical); everything for the app follows the format below.

## The task
Do what the task says (language codes: da, sv, no, en):
- **Correct** the text in the spoken language: misheard words, especially names, products and screen names (see
  the word list below and the one in the task). Do not turn spoken language into written language, and do not
  change the content.
- **Translate** to the languages the task names. Natural spoken language, as a Swede/Norwegian would say it.
  Product names and screen names are not translated.
- The same numbering and exactly the same time codes as the original. The same number of blocks; never merge
  blocks and do not split them, not even mid-sentence. At most 2 lines per block, about 42 characters per line.
- **Suggest cuts** when the task asks for it (`cut=yes`, or "suggest what to cut out"). The video is usually a
  recording of a Teams meeting or webinar. Suggest whole blocks, one line per cut:
  `<first block>-<last block> <kind> <short reason>`, e.g. `1-14 start Folk kommer ind og tester lyd` (the reason
  in Danish: the user reads it). Kinds:
  - `start`: before the meeting really begins (people joining, sound checks, "kan I høre mig?", "vi venter lige")
  - `pause`: interruptions and waiting along the way ("to sekunder, min skærm frøs")
  - `end`: after the meeting is over ("tak for i dag" and everything after, "er optagelsen slukket?")
  - `inappropriate`: what should not be kept forever: private matters, customers or colleagues mentioned in a
    vulnerable context, crude language
  - `offtopic`: fooling around and talk that is not about the subject
  - `unclear`: what cannot be understood, not even in context
  Be careful: only cut what clearly does not belong, and never in the middle of an explanation that goes on.
  The app finds long silent pauses itself. The user sees every cut on a timeline and approves them before
  anything is cut. Keep the time codes unchanged; the app moves them after cutting.

## If you have the SubaDubaLingo tools (the best way)
With the tools `start_video`, `wait`, `status`, `get_text`, `save_text`, `suggest_cuts`, `approve_cuts`, `cancel`
and `find_videos`, do it all in the chat:
1. The user writes e.g. "lav denne video på svensk med norske undertekster: C:\\...\\video.mp4". Call
   `start_video` (video, speech, subtitles, maybe spoken_language). If you do not know the path, use
   `find_videos` or ask. If the user wants the video cut first (e.g. "klip ventetiden og pauserne væk", "fjern
   det, der ikke skal med"), set `cut` to true. Only cutting, no new languages: `speech` original and `subtitles`
   none.
2. Call `wait` again and again. Write a short line to the user at every 10 % (e.g. "40 % - lytter videoen
   igennem"). Listening can take an hour; keep going, and if you were interrupted, `status` picks it up again.
3. When the job is in `claude_turn`: fetch the text with `get_text` in chunks (about 120 blocks, and always with
   `skill: 4`), correct and translate by the rules above, and hand each language back with `save_text` (same
   numbers and time codes). The answer says what is missing or wrong; fix it. Pass corrected misheard words in
   `words` (`wrong => right`). If the video is to be cut, read the whole text and hand in your suggestions with
   `suggest_cuts` (block numbers as above; `done` true at the end, also when you have none).
   When the cut suggestions and every language are in, the app shows the cuts to the user on a timeline
   (`review_cuts`). Tell the user briefly what you suggested cutting (time and why). The user can change it in
   the app or tell you: "behold det ved 14:20" -> `approve_cuts` with `keep` = the cut's number; "klip også
   22:10-22:40" -> `approve_cuts` with `add`. `approve_cuts` without `wait_for_user` cuts at once, so ask the user
   whether the cuts are right before you approve.
4. When everything is in, the app does the rest. Call `wait` until "done", and tell the user the name of the new
   video and that it can be played from the app.
If the user wants to stop, or a job hangs and blocks the app, use `cancel` (ask first).

## A. The user sends a file "... - til Claude.txt"
The first line is `SUBADUBALINGO-TASK id=<id> languages=<codes> blocks=<count>` (older versions of the app:
`VIDEOSPROG-OPGAVE id=<id> sprog=<codes> blokke=<count>`, answer those with `VIDEOSPROG-SVAR` and the Danish
section names `=== klip ===`, `=== ordliste ===`, `=== slut ===`).

1. Read the file with code. The subtitles follow the line `=== <spoken language> ===`.
2. Make the answer file with exactly the name the task gives (`... - fra Claude.txt`), as a file the user can
   download. Work in chunks: correct and translate at most about 100 blocks at a time, and append each chunk to
   the file with code until every block is in. Do not write the subtitles in the chat.
3. Template for the answer file:

```
SUBADUBALINGO-ANSWER id=<the same id as in the task> skill=4
=== da ===
1
00:00:01,000 --> 00:00:03,500
Velkommen til webinaret om Membercare.

(... every block ...)
=== sv ===
1
00:00:01,000 --> 00:00:03,500
Välkommen till webbinariet om Membercare.

(... every block ...)
=== cuts ===
1-14 start Folk kommer ind og tester lyd
212-218 offtopic Snak om vejret
=== words ===
memberkær => Membercare
=== end ===
```

   - One section per language in the order `languages=` gives. The spoken language too (corrected) when it is
     in `languages=`.
   - `=== cuts ===` only when the first line has `cut=yes`: your cut suggestions, see "Suggest cuts" above
     (empty if there is nothing to cut).
   - `=== words ===`: the misheard words you corrected, one per line `wrong => right`. The app remembers them
     and corrects them itself next time.
   - `=== end ===` as the last line tells the app the file is complete.
4. Check with code that each section has exactly `blocks=` blocks with the same time codes as the original. Fix
   it if not.
5. Answer in the chat with one short sentence in Danish: the file is ready, and the user should click it and
   download it (Download). The app finds it in the Downloads folder by itself.

## B. The user pastes subtitles directly into the chat (fallback)
The message has a task and a code block with the subtitles, maybe "Part x of y".
- Answer directly in the chat, without files or code, with EVERYTHING in ONE code block: a line
  `=== <code> ===` before each language in the given order, then `=== words ===` with corrected words. No text
  before or after.

## Word list
See [words.md](words.md). These words are spelled exactly like this in every language.
"""

def skill_zip(dest: Path) -> Path:
    """Write the skill (with the current word list) as a zip for Claude > Settings > Skills > Upload."""
    words = known_words()
    rules = word_rules()
    word_list = ["# Word list", "", "Known words (spelled exactly like this in every language):", ""]
    word_list += [f"- {w}" for w in words] or ["- Membercare"]
    word_list += ["", "Typical mishearings by the speech recognition:", ""]
    word_list += [f"- {w} => {r}" for w, r in rules]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{SKILL_NAME}/SKILL.md", SKILL_MD)
        z.writestr(f"{SKILL_NAME}/words.md", "\n".join(word_list) + "\n")
    dest.write_bytes(buf.getvalue())
    return dest
