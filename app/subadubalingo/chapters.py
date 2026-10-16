"""Chapters and a short description of the video, made by Claude in the same round as the subtitles.

Claude reads the whole text anyway, so it also returns chapters (a start time and a title) and a description of a
few sentences, in the language of the new video. In the answer file they are the optional sections
`=== chapters ===` (lines "0:00 Title", times from the subtitles' time codes in the original video) and
`=== summary ===`; through the MCP tools they come with `save_chapters`. Answers without them (skill version 5 and
earlier) are still fine: there are just no chapters.

The app saves them as "<video> - kapitler.txt" (named in the UI language, recognised in any) next to the video, laid
out as a YouTube description: the description, a blank line, then the chapters ("0:00 Intro"). When the video is cut,
the chapter times are moved to the cut video. The done view shows them with "Kopiér kapitler" and "Kopiér
beskrivelse" buttons.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import log, output_path
from .i18n import CODES, current, t

def _icon() -> str:
    """The files list's icon for the chapters file: the design's "chapters" icon (T8), or the text file icon in an
    install whose assets do not have it yet."""
    try:
        from .theme import assets_dir
        return "chapters" if (assets_dir() / "ui" / "a-light" / "icons" / "chapters.png").exists() else "file-subtitles"
    except Exception:
        return "file-subtitles"


ICON = _icon()

# YouTube's rules for chapters in a description: the first at 0:00, at least three, each at least 10 s long
MIN_CHAPTER_MS = 10_000
MIN_CHAPTERS = 3


@dataclass
class Chapter:
    start: int     # ms
    title: str


@dataclass
class Outline:
    """What Claude says about the video as a whole: its chapters and a short description."""
    chapters: list[Chapter] = field(default_factory=list)
    summary: str = ""

    def __bool__(self):
        return bool(self.chapters or self.summary)

    def to_json(self) -> dict:
        return {"chapters": [[c.start, c.title] for c in self.chapters], "summary": self.summary}

    @staticmethod
    def from_json(d: dict | None) -> "Outline":
        d = d or {}
        return Outline([Chapter(int(a), str(b)) for a, b in d.get("chapters") or []], str(d.get("summary") or ""))


# --- Reading Claude's lines ---------------------------------------------------------------------

# "0:00 Intro", "00:01:02,000 - Demo", "- 1:02:03: Spørgsmål", "[12:30] Pause", "12:30 | Afslutning"
TIME_RE = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?"
LINE_RE = re.compile(rf"^\s*(?:[-*•]|\d+[.)])?\s*[\[(]?(?P<time>{TIME_RE})[\])]?\s*(?:[-–—:|]\s*)?(?P<title>.*?)\s*$")


def parse_clock(text: str) -> int:
    parts = text.replace(",", ".").split(":")
    secs = float(parts[-1]) + int(parts[-2]) * 60 + (int(parts[-3]) * 3600 if len(parts) > 2 else 0)
    return int(round(secs * 1000))


def parse_chapter_lines(lines: list[str]) -> tuple[list[Chapter], list[str]]:
    """Claude's chapter lines; returns the chapters (in the order given) and the lines that could not be read."""
    out, bad = [], []
    for raw in lines:
        line = raw.strip()
        if not line or line.lower() in ("none", "(none)", "ingen", "(ingen)", "-"):
            continue
        m = LINE_RE.match(line)
        title = m.group("title").strip().strip('"').strip() if m else ""
        if not m or not title:
            bad.append(line)
            continue
        out.append(Chapter(parse_clock(m.group("time")), title))
    return out, bad


def parse_summary(lines: list[str]) -> str:
    """The description: its paragraphs as they are, without leading/trailing blank lines or a code fence."""
    text = "\n".join(l.rstrip() for l in lines if not l.strip().startswith("```")).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def tidy(chapters: list[Chapter], duration: int | None = None) -> list[Chapter]:
    """Chapters as YouTube takes them: sorted, inside the video, the first at 0:00, each at least 10 s long (a
    chapter that starts too soon after the one before it is dropped). Fewer than three: none (YouTube ignores
    them then, and so short a video needs none)."""
    out: list[Chapter] = []
    for c in sorted(chapters, key=lambda c: c.start):
        if duration and c.start >= duration - MIN_CHAPTER_MS // 2:
            continue
        if out and c.start - out[-1].start < MIN_CHAPTER_MS:
            continue
        out.append(Chapter(max(0, c.start), c.title))
    if out:
        out[0] = Chapter(0, out[0].title)
        if len(out) > 1 and out[1].start < MIN_CHAPTER_MS:
            out.pop(1)
    if duration and len(out) > 1 and duration - out[-1].start < MIN_CHAPTER_MS:
        out.pop()
    return out if len(out) >= MIN_CHAPTERS else []


def from_answer(sections: dict[str, list[str]], duration: int | None = None, raw: bool = False) -> Outline:
    """The outline from an answer's sections (claude._sections): "chapters" and "summary". raw: the chapters as
    given, not tidied (one part of several, see merge_parts)."""
    chapters, bad = parse_chapter_lines(sections.get("chapters", []))
    if bad:
        log.info("chapter lines not read: %s", bad)
    return Outline(chapters if raw else tidy(chapters, duration), parse_summary(sections.get("summary", [])))


def remap(outline: Outline, ranges: list[tuple[int, int]], duration: int | None = None) -> Outline:
    """The outline for the video with `ranges` cut out: chapter times move back, and a chapter whose start was cut
    away starts where the cut was."""
    from .cutting import map_time
    if not ranges:
        return outline
    moved = [Chapter(map_time(c.start, ranges), c.title) for c in outline.chapters]
    return Outline(tidy(moved, duration), outline.summary)

# --- Writing -------------------------------------------------------------------------------------

def fmt_time(ms: int, hours: bool) -> str:
    """0:00, 12:34 or 1:02:03 (YouTube needs hours on every line once the video is an hour or longer)."""
    s = max(0, int(ms // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if hours else f"{m}:{s:02}"


def chapters_text(outline: Outline) -> str:
    """The chapters, one per line ("0:00 Intro"), as YouTube reads them in a description."""
    hours = bool(outline.chapters) and outline.chapters[-1].start >= 3_600_000
    return "\n".join(f"{fmt_time(c.start, hours)} {c.title}" for c in outline.chapters)


def file_text(outline: Outline) -> str:
    """The file's content: ready to paste as a YouTube description (description, blank line, chapters)."""
    parts = [p for p in (outline.summary.strip(), chapters_text(outline)) if p]
    return "\n\n".join(parts) + "\n"


def chapters_name(video: Path, lang: str | None = None) -> Path:
    """Where the chapters are written: "<video> - kapitler.txt" in the UI language."""
    return output_path(video, t("file.chapters", lang))


def chapters_names(video: Path) -> list[Path]:
    """Every name the chapters file may have: in the current UI language first, then the others."""
    codes = [current()] + [c for c in CODES if c != current()]
    return list(dict.fromkeys(chapters_name(video, c) for c in codes))


def find_chapters(video: Path) -> Path | None:
    return next((p for p in chapters_names(video) if p.exists()), None)


def is_chapters_file(path: Path) -> bool:
    """A chapters file the app wrote, named in any UI language."""
    name = path.name.lower()
    return any(name.endswith(" - " + t("file.chapters", c).lower()) for c in CODES)


def save(video: Path, outline: Outline) -> Path | None:
    """Write the chapters file next to the video (the original video's name, the result video's times). Replaces one
    made earlier in another UI language, so there is only ever one. Returns it, or None when there is nothing."""
    if not outline:
        return None
    out = chapters_name(video)
    for old in chapters_names(video):
        if old != out and old.exists():
            try:
                old.unlink()
            except OSError:
                pass
    out.write_text(file_text(outline), encoding="utf-8")
    log.info("chapters: %d, summary %d chars -> %s", len(outline.chapters), len(outline.summary), out.name)
    return out


def language_for(job: dict):
    """The language to write the chapters and the summary in: the new video's (its dub, else its subtitles, else the
    spoken language)."""
    return job.get("speech") or job.get("subs") or job.get("lang")


def merge_parts(outlines: list[Outline], duration: int | None = None) -> Outline:
    """Parts mode: every part's chapters together, and the last summary given (asked for with the last part)."""
    found = [c for o in outlines for c in o.chapters]
    summary = next((o.summary for o in reversed(outlines) if o.summary), "")
    return Outline(tidy(found, duration), summary)
