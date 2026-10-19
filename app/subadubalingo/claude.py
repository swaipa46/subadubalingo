"""The assistant's turn in an AI chat: the user copies a prompt into any AI chat (ChatGPT, Gemini, Claude, ...)
and copies the answer back. (An assistant with MCP does the same work through mcp_server.py instead.)

One round per part of the video: the assistant corrects the subtitles in the spoken language (product names and
other known words), translates them to the languages the new video needs, and lists words it corrected.
Those words are saved to corrections.txt, so the next transcription gets them right and the next prompt
carries them along. The prompt is self-contained: the rules, the word list and the answer format are all in it,
so it works in any chat model.

The section markers and cut kinds are English, the domain language (docs/glossary.md). The answer parser is
tolerant: besides the asked-for "=== xx ===" markers it reads markdown headings and bold labels, a code block per
language, English or Scandinavian language names, the Danish section names of older versions (=== ordliste ===,
=== klip ===) and an answer without the trailing words section.
"""
from __future__ import annotations

import math
import re

from .common import APP_NAME, Cue, Language, LANGUAGES, format_srt, log, parse_srt_text
from . import chapters as chapters_mod
from . import engine
from .i18n import current, t

LANG_BY_CODE = {l.code: l for l in LANGUAGES}

# --- Parts ------------------------------------------------------------------------------

def part_size(n_languages: int) -> int:
    """Blocks per round, so the chat's answer (one SRT per language) is not cut off."""
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
# The chat answers in ONE code block, with a marker line before each language:
#     === da ===      (corrected text in the spoken language)
#     === sv ===      (translations)
#     === words ===   (wrong => right)
# One block means one copy, whichever copy button the user uses (the block's or the whole answer's). The prompt names
# no product and no skill: it must work in any chat (ChatGPT, Gemini, Claude, Copilot, ...).

NAMES_EN = {"da": "Danish", "sv": "Swedish", "no": "Norwegian", "en": "English"}
CUT_TASK = "suggest what to cut out"
CHAPTERS_TASK = "make chapters and a short summary of the video"
# An example cut reason per UI language, so the example in the prompt is in the language the assistant is to write in
CUT_EXAMPLE = {"da": "Folk kommer ind og tester lyd", "sv": "Folk ansluter och testar ljudet",
               "no": "Folk kommer inn og tester lyden", "en": "People join and test the sound"}


def user_language() -> str:
    """The user's language (the app's UI language) in English, for telling the assistant what to talk to the user in."""
    return NAMES_EN.get(current(), "English")


def cut_example() -> str:
    return CUT_EXAMPLE.get(current(), CUT_EXAMPLE["en"])


def cut_rules() -> list[str]:
    return [
        "Cuts: suggest blocks to cut out of the video (a recording of a Teams meeting or webinar). Kinds:",
        "  start = before the meeting really begins (people joining, sound checks, \"can you hear me\"),",
        "  pause = interruptions and waiting along the way, end = after the meeting is over (chatter, \"is it off?\"),",
        "  inappropriate = what should not be kept (private matters, customers or colleagues mentioned in a "
        "vulnerable context, crude language), offtopic = fooling around and talk off the subject, unclear = what "
        "cannot be understood, not even in context,",
        "  filler = blocks with nothing but filler: \"øh\", \"øhm\", false starts, a repeated word or phrase "
        "(\"vi skal, vi skal\"), a long hesitation. Only blocks that hold nothing else; never a block that also says "
        "something. The reason is the filler itself in quotes (\"øh, øhm\"), or for a repetition the word for "
        f"repetition in {user_language()}, a colon and the words (e.g. Gentagelse: \"vi vi skal\").",
        "  One line per cut: <first block>-<last block> <kind> <short reason>, e.g. "
        f"\"1-14 start {cut_example()}\". Cut whole blocks, and be careful: only cut what clearly does not belong. "
        "The app finds long silent pauses itself. The user reviews every cut before anything is cut. Write the reason "
        f"in {user_language()} (the user reads it).",
    ]


def chapter_rules(lang: Language) -> list[str]:
    """What the prompts ask for chapters and the summary, written in lang (the language of the new video)."""
    return [
        "Chapters: where the subject changes, one line per chapter: <start time> <short title>, e.g. \"0:00 Welcome\". "
        "The start time is the time code of the block the chapter begins with (m:ss, or h:mm:ss from one hour), as "
        "in the original; the app moves the times if the video is cut. The first chapter at 0:00, at least 3 "
        "chapters of at least 10 seconds each, titles of 2-6 words. No lines if the video is too short for that.",
        "Summary: a short description of the video in 2-4 sentences (what it is about, what the viewer gets out of "
        "it), for e.g. YouTube; no chapter list in it.",
        f"Write the chapter titles and the summary in {NAMES_EN[lang.code]}.",
    ]


def _tasks(src: Language, proofread: bool, targets: list[Language], cut: bool,
           chapters: Language | None = None) -> list[str]:
    tasks = []
    if proofread:
        tasks.append(f"correct the {NAMES_EN[src.code]} subtitles (misheard words, especially names and product terms)")
    if targets:
        tasks.append("translate them to " + " and ".join(NAMES_EN[t.code] for t in targets))
    if cut:
        tasks.append(CUT_TASK)
    if chapters:
        tasks.append(CHAPTERS_TASK)
    return tasks


def prompt(src: Language, proofread: bool, targets: list[Language], part: list[Cue], first_number: int,
           idx: int, n_parts: int, cut: bool = False, chapters: Language | None = None) -> str:
    """One part's prompt for the chat (parts mode). chapters: the language to write chapters and the summary in
    (None: not asked for). Every part asks for its own chapters; the summary is asked for in the last part."""
    order = ([src.code] if proofread else []) + [t.code for t in targets]
    part_note = (f"Part {idx + 1} of {n_parts} (blocks {first_number}-{first_number + len(part) - 1}). "
                 if n_parts > 1 else "")
    words = known_words()
    rules = word_rules()
    lines = [
        f"Please help with the subtitles of a video, made with speech recognition by the app {APP_NAME}. "
        f"{part_note}Task: " + ", and ".join(_tasks(src, proofread, targets, cut, chapters)) + ".",
        "",
        "Rules:",
        "- Keep the numbering and time codes exactly. The same number of blocks; do not merge or split blocks.",
        "- At most 2 lines per block and about 42 characters per line. Natural spoken language, as one would say it.",
    ] + (["- Only correct what was misheard (especially names and product terms); do not change the content."]
         if proofread else []) + [
        "- Product and screen names are spelled as in the word list and not translated.",
        "- Answer directly in the chat: no files, no tools, no code to run. Put EVERYTHING in ONE code block (```). "
        "Before each language a line with === and the language code, in this order: "
        + ", ".join(f"=== {c} ===" for c in order) + ".",
        "- Write out every block of every language in full; never shorten the answer with \"...\".",
    ]
    if cut:
        lines += ["- Then, in the same code block, a line === cuts === with your cut suggestions for this part (no "
                  "lines if there are none). The numbers are the block numbers above."]
        lines += ["  " + r for r in cut_rules()]
    last = idx + 1 >= n_parts
    if chapters:
        lines += ["- Then, in the same code block, a line === chapters === with the chapters that begin in this part"
                  + (", then a line === summary === with the summary of the whole video (every part you have seen)."
                     if last else " (the summary comes with the last part).")]
        lines += ["  " + r for r in chapter_rules(chapters)]
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
    ] + (["=== cuts ===", f"1-3 start {cut_example()}", ""] if cut else []) + (
        ["=== chapters ===", "0:00 Intro", ""] + (["=== summary ===", "A short summary of the video.", ""] if last else [])
        if chapters else []) + [
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

# Language names an answer may use for a section: the codes, and the names in English and the app's languages
# (a chat model often writes "### Swedish" or "**Svensk (sv)**" instead of "=== sv ===").
_NAMES = {"da": ["da", "dansk", "danske", "danish", "danska"],
          "sv": ["sv", "se", "svensk", "svenske", "svenska", "swedish"],
          "no": ["no", "nb", "norsk", "norske", "norwegian", "norska", "bokmål", "bokmal"],
          "en": ["en", "engelsk", "engelske", "english", "engelska"]}
_LANG = {alias: code for code, names in _NAMES.items() for alias in names}
_LABEL = dict(_LANG)
# Section names, also the Danish ones of older versions and what chat models write in their headings
_SECTION_WORDS = {"words": "words", "corrections": "words", "ordliste": "words", "rettelser": "words", "ord": "words",
                  "wordlist": "words", "glossary": "words", "mishearings": "words", "misheard": "words",
                  "cuts": "cuts", "cut": "cuts", "klip": "cuts", "klipp": "cuts", "end": "end", "slut": "end",
                  "chapters": "chapters", "kapitler": "chapters", "kapitel": "chapters", "chapter": "chapters",
                  "kapitlar": "chapters", "summary": "summary", "description": "summary", "beskrivelse": "summary",
                  "beskrivning": "summary", "sammendrag": "summary", "sammanfattning": "summary",
                  "resume": "summary", "resumé": "summary"}
_LABEL.update(_SECTION_WORDS)
SECTIONS = ("words", "end", "cuts", "chapters", "summary")   # the sections that are not a language
# A marker line: "=== sv ===", "### Svensk", "**srt-sv**", "Svensk:" ... (alone on its line)
MARKER_RE = re.compile(r"^[ \t>#*=_-]*(?:srt[-_ ])?([A-Za-zæøåÆØÅ]+)[ \t]*(?:\([^)]*\))?[ \t:*=#_-]*$")
# The format the prompt asks for ("=== sv ==="): a marker wherever it stands.
STRICT_MARKER_RE = re.compile(r"^={2,}\s*(?:srt[-_ ]?)?([A-Za-zæøåÆØÅ]+)\s*={2,}$")
FENCE_RE = re.compile(r"^```[ \t]*([^\n`]*)$")
# A heading of several words: a markdown heading, a bold line or a short line ending in a colon
# ("## Corrected Danish text", "**Swedish translation:**", "3. Corrected words:")
HEADING_RE = re.compile(r"^(?:#{1,6}\s+.+|\*\*.+\*\*:?|__.+__:?|[^:]{1,60}:)$")
_WORD_RE = re.compile(r"[A-Za-zæøåÆØÅäöÄÖ]+")


def _label(line: str) -> str | None:
    m = MARKER_RE.match(line.strip())
    if not m or not line.strip():
        return None
    return _LABEL.get(m.group(1).lower())


def _heading_label(line: str) -> str | None:
    """The section a heading of several words names, or None. A section word wins over a language ("Corrected
    words (Danish)" is the words); one language must be named unambiguously (a code in parentheses decides)."""
    s = line.strip()
    if not s or "-->" in s or len(s) > 80 or not HEADING_RE.match(s):
        return None
    words = [w.lower() for w in _WORD_RE.findall(s)]
    if not words or len(words) > 8:
        return None
    for w in words:
        if w in _SECTION_WORDS:
            return _SECTION_WORDS[w]
    code = re.search(r"\((da|sv|no|nb|en)\)", s, re.I)
    if code:
        return _LANG[code.group(1).lower()]
    # Only full language names here: two-letter codes ("no", "en", "da") are ordinary words in a heading
    langs = {_LANG[w] for w in words if w in _LANG and len(w) > 2}
    return langs.pop() if len(langs) == 1 else None


def _sections(text: str) -> dict[str, list[str]]:
    """The answer's lines per section: language codes, "words", "cuts", "chapters", "summary", "end"."""
    sections: dict[str, list[str]] = {}
    current = None
    in_fence = False
    # A marker must start a paragraph, so a subtitle line that just says "Norsk" is not taken for one.
    para_start = True
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        f = FENCE_RE.match(raw.strip())
        if f:
            if in_fence and not f.group(1).strip():
                # A closing fence ends the section: chat text after it ("Let me know if ...") is not subtitles.
                # The one-block format keeps working, since its markers all stand inside the block.
                in_fence = False
                current = None
            else:
                in_fence = True
                lab = _LABEL.get(re.sub(r"^srt[-_ ]?", "", f.group(1).strip().lower()))
                if lab:
                    current = lab
                    sections.setdefault(lab, [])
            para_start = True
            continue
        strict = STRICT_MARKER_RE.match(raw.strip())
        lab = None
        if strict:
            lab = _LABEL.get(strict.group(1).lower())
        elif para_start:
            lab = _label(raw) or _heading_label(raw)
        para_start = not raw.strip()
        if lab:
            current = lab
            sections.setdefault(lab, [])
            para_start = True
            continue
        if current:
            sections.setdefault(current, []).append(raw)
    return sections


def _fenced_blocks(text: str) -> list[str]:
    """The contents of the answer's code blocks, in order."""
    return re.findall(r"^```[^\n`]*\n(.*?)^```", text.replace("\r\n", "\n"), re.M | re.S)


def parse_answer(text: str) -> tuple[dict[str, list[Cue]], list[tuple[str, str]]]:
    """Per language code the SRT blocks, and the corrected words. Reads the one-block format, separate
    labelled code blocks (```srt-sv), and headings or bold labels before blocks; a closing fence ends a section."""
    sections = _sections(text)
    by_lang = {}
    for code, lines in sections.items():
        if code in SECTIONS:
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


def answer_outline(text: str, duration: int | None = None, raw: bool = False) -> chapters_mod.Outline:
    """The chapters and summary in the answer (empty when it has none: older answers, short videos). raw: one part's
    chapters as given (parts mode joins them with chapters.merge_parts)."""
    return chapters_mod.from_answer(_sections(text), duration, raw)


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
    missing = [c for c in codes if c not in have]
    blocks = [c for c in (parse_srt_text(b) for b in _fenced_blocks(text)) if c] if not by_lang else []
    if len(blocks) > 1 and missing:
        # One unlabelled code block per language, in the asked order (a chat that ignored the markers)
        by_lang = dict(zip(missing, blocks))
    elif not by_lang:
        # A bare SRT (copied from one of several code blocks, without its label): take it as the first
        # language still missing, or Swedish when it is clearly Swedish (ä/ö). The status shows which.
        bare = parse_srt_text(text)
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
            problems.append(t("claude.language_problem", language=LANG_BY_CODE[code].name, problem=msg))
        else:
            have[code] = cues
    if problems:
        return have, words, problems[0]
    missing = [LANG_BY_CODE[c].singular for c in codes if c not in have]
    if not by_lang:
        return have, words, t("claude.no_answer")
    if missing:
        return have, words, t("claude.still_missing", languages=", ".join(missing))
    return have, words, None
