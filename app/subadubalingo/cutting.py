"""Cutting a video before subtitles and dub: what to cut (silent pauses found by the program, and Claude's
suggestions by subtitle block), cutting it out with ffmpeg in one pass, and moving the subtitles' time codes so
they fit the cut video.

A cut is a time range in the original video. Claude suggests cuts by block number ("12-15 offtopic Snak om
vejret"), because it reads the text, not the audio; the range runs from just before the first block to the end
of the last. The user adjusts them on a timeline before anything is cut.
"""
from __future__ import annotations

import bisect
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .common import FFMPEG, Cue, Runner, ToolError, log
from .i18n import t

def kind_label(kind: str) -> str:
    """A cut kind's name shown to the user, in the UI language."""
    return t(f"cutting.kind.{kind}")


class _Kinds(dict):
    """kind -> (label, strong colour, light fill). The label is looked up when read, so it follows the UI language
    (new code: use kind_label(kind))."""

    def __getitem__(self, kind):
        return (kind_label(kind),) + super().__getitem__(kind)

    def get(self, kind, default=None):
        return self[kind] if kind in self else default

    def values(self):
        return [self[k] for k in self]

    def items(self):
        return [(k, self[k]) for k in self]


# Cut kinds (the domain's words; Claude writes them in its suggestions). Stored: (strong colour, light fill);
# KINDS[kind] gives (label, strong, light).
KINDS = _Kinds({
    "start": ("#5c2e91", "#efe6f8"),
    "pause": ("#486991", "#e3ebf4"),
    "end": ("#5c2e91", "#efe6f8"),
    "inappropriate": ("#c42b1c", "#fde7e9"),
    "offtopic": ("#9d5d00", "#fff4ce"),
    "unclear": ("#00666d", "#ddf1ef"),
    "manual": ("#0f6cbd", "#e1effa"),
})
# Also the Danish words the skill used before v2.0 (skill version 3), and what Claude writes when it improvises
KIND_ALIASES = {
    "start": "start", "before": "start", "intro": "start", "waiting": "start",
    "før": "start", "foer": "start", "inden": "start", "venter": "start",
    "pause": "pause", "break": "pause", "interruption": "pause", "silence": "pause",
    "afbrydelse": "pause", "stille": "pause", "stilhed": "pause",
    "end": "end", "after": "end", "outro": "end", "slut": "end", "efter": "end", "afslutning": "end",
    "inappropriate": "inappropriate", "private": "inappropriate", "sensitive": "inappropriate",
    "upassende": "inappropriate", "privat": "inappropriate", "følsomt": "inappropriate", "foelsomt": "inappropriate",
    "offtopic": "offtopic", "off-topic": "offtopic", "chatter": "offtopic",
    "fjol": "offtopic", "udenfor": "offtopic", "snak": "offtopic",
    "unclear": "unclear", "inaudible": "unclear", "unintelligible": "unclear",
    "uklart": "unclear", "uforståeligt": "unclear", "utydeligt": "unclear", "uforstaaeligt": "unclear",
    "manual": "manual", "other": "manual", "manuel": "manual", "andet": "manual",
}

MIN_PAUSE_MS = 20_000   # silence this long (between blocks) is suggested as a pause
PAUSE_MARGIN_MS = 750   # kept on each side of a pause, so the cut does not clip a breath or a word
MIN_CUT_MS = 300


@dataclass
class Cut:
    start: int            # ms in the original video
    end: int
    kind: str
    reason: str = ""
    on: bool = True
    source: str = "claude"   # "auto" (silence), "claude", "user"
    blocks: tuple[int, int] | None = None  # 1-based block range Claude named, if any

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_json(self) -> dict:
        return {"start": self.start, "end": self.end, "kind": self.kind, "reason": self.reason, "on": self.on,
                "source": self.source, "blocks": list(self.blocks) if self.blocks else None}

    @staticmethod
    def from_json(d: dict) -> "Cut":
        return Cut(int(d["start"]), int(d["end"]), KIND_ALIASES.get(d.get("kind", ""), "manual"), d.get("reason", ""), bool(d.get("on", True)),
                   d.get("source", "claude"), tuple(d["blocks"]) if d.get("blocks") else None)


def fmt_clock(ms: int) -> str:
    """12:34 or 1:02:03."""
    s = max(0, int(round(ms / 1000)))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def fmt_length(ms: int) -> str:
    s = max(0, int(round(ms / 1000)))
    if s < 60:
        return f"{s} s"
    return fmt_clock(ms)


# --- From blocks to times ------------------------------------------------------------------

def block_range(cues: list[Cue], a: int, b: int, duration: int, kind: str) -> tuple[int, int]:
    """Time range for blocks a..b (1-based): from just before block a starts to the end of block b. A cut that
    takes the first or last block runs to the start or end of the video."""
    n = len(cues)
    a, b = max(1, min(a, n)), max(1, min(b, n))
    if a > b:
        a, b = b, a
    first, last = cues[a - 1], cues[b - 1]
    start = 0 if a == 1 else max(cues[a - 2].end, first.start - 200)
    end = duration if b == n else min(last.end, cues[b].start - 50)
    return start, max(end, start + MIN_CUT_MS)


CLOCK_RE = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?"
LINE_RE = re.compile(
    rf"^\s*[-*•]?\s*(?:(?:blok(?:ke)?|blocks?)\s*)?(?P<range>{CLOCK_RE}\s*(?:-|–|til|to)\s*{CLOCK_RE}|\d+\s*(?:(?:-|–|til|to)\s*\d+)?)"
    r"\s*[:|,]?\s*(?P<kind>[A-Za-zæøåÆØÅ-]+)?\s*[:|,–-]?\s*(?P<reason>.*)$", re.I)


def parse_clock(text: str) -> int:
    parts = text.replace(",", ".").split(":")
    secs = float(parts[-1]) + int(parts[-2]) * 60 + (int(parts[-3]) * 3600 if len(parts) > 2 else 0)
    return int(round(secs * 1000))


def parse_cut_lines(lines: list[str], cues: list[Cue], duration: int, source: str = "claude"
                    ) -> tuple[list[Cut], list[str]]:
    """Claude's cut lines: "12-15 offtopic Snak om vejret", "1-8 start Folk kommer ind", "40 uklart ...",
    or with times: "14:20-15:05 upassende ...". Returns the cuts and lines that could not be read."""
    cuts, bad = [], []
    for raw in lines:
        line = raw.strip()
        if not line or line.lower() in ("none", "(none)", "ingen", "intet", "(ingen)", "-"):
            continue
        m = LINE_RE.match(line)
        if not m:
            bad.append(line)
            continue
        rng = m.group("range")
        word = (m.group("kind") or "").lower()
        kind = KIND_ALIASES.get(word)
        reason = m.group("reason").strip()
        if kind is None:
            # No known category: the word belongs to the reason
            reason = f"{m.group('kind') or ''} {reason}".strip()
            kind = "manual" if source == "user" else "offtopic"
        if ":" in rng:
            a_txt, b_txt = re.split(r"\s*(?:-|–|til|to)\s*", rng, maxsplit=1)
            start, end = sorted((parse_clock(a_txt), parse_clock(b_txt)))
            blocks = None
        else:
            nums = [int(x) for x in re.findall(r"\d+", rng)]
            a, b = nums[0], nums[-1]
            if not cues or not (1 <= min(a, b) <= len(cues)):
                bad.append(line)
                continue
            start, end = block_range(cues, a, b, duration, kind)
            blocks = (min(a, b), max(a, b))
        start, end = max(0, start), min(duration, end)
        if end - start < MIN_CUT_MS:
            bad.append(line)
            continue
        cuts.append(Cut(start, end, kind, reason, True, source, blocks))
    return cuts, bad


# --- Silence -------------------------------------------------------------------------------

ENVELOPE_MS = 250


def envelope(wav: Path) -> np.ndarray:
    """Loudness in dBFS per 250 ms of a 16 kHz mono WAV (for finding pauses and drawing the timeline)."""
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    hop = int(sr * ENVELOPE_MS / 1000)
    n = len(data) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = data[:n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return (20 * np.log10(rms)).astype(np.float32)


def find_pauses(cues: list[Cue], duration: int, env: np.ndarray | None, min_ms: int = MIN_PAUSE_MS) -> list[Cut]:
    """Long stretches without speech: before the first block, between blocks and after the last. With the
    loudness envelope, a stretch only counts when it is mostly quiet (no talk Whisper missed, no music)."""
    out = []
    quiet = None
    if env is not None and len(env):
        floor = float(np.percentile(env, 10))
        quiet = env < max(floor + 12, -60)
    gaps = []
    prev_end = 0
    for c in cues:
        gaps.append((prev_end, c.start, "start" if prev_end == 0 and not gaps else "pause"))
        prev_end = max(prev_end, c.end)
    gaps.append((prev_end, duration, "end" if cues else "pause"))
    for a, b, kind in gaps:
        if b - a < min_ms:
            continue
        if quiet is not None:
            i0, i1 = a // ENVELOPE_MS, max(a // ENVELOPE_MS + 1, b // ENVELOPE_MS)
            seg = quiet[i0:i1]
            if len(seg) and seg.mean() < 0.8:
                continue
        start = 0 if kind == "start" else a + PAUSE_MARGIN_MS
        end = duration if kind == "end" else b - PAUSE_MARGIN_MS
        what = (t("cutting.pause.start", length=fmt_length(end)) if kind == "start" else
                t("cutting.pause.end", length=fmt_length(end - start)) if kind == "end" else
                t("cutting.pause.middle", length=fmt_length(b - a)))
        out.append(Cut(start, end, kind, what, True, "auto"))
    return out


# --- Combining ------------------------------------------------------------------------------

def merge(cuts: list[Cut], join_ms: int = 400) -> list[Cut]:
    """Overlapping (or nearly touching) cuts become one. Used on the cuts that are switched on."""
    out: list[Cut] = []
    for c in sorted(cuts, key=lambda c: c.start):
        if out and c.start <= out[-1].end + join_ms:
            last = out[-1]
            reason = last.reason if c.reason in last.reason else "; ".join(x for x in (last.reason, c.reason) if x)
            out[-1] = Cut(last.start, max(last.end, c.end), last.kind, reason, True, last.source)
        else:
            out.append(Cut(c.start, c.end, c.kind, c.reason, True, c.source, c.blocks))
    return out


def combine_suggestions(auto: list[Cut], claude: list[Cut]) -> list[Cut]:
    """The list the user reviews: Claude's suggestions plus the silent pauses Claude did not already cover."""
    out = list(claude)
    for p in auto:
        covered = sum(max(0, min(p.end, c.end) - max(p.start, c.start)) for c in claude)
        if covered < 0.5 * p.length:
            out.append(p)
    return sorted(out, key=lambda c: c.start)


def active(cuts: list[Cut], duration: int) -> list[tuple[int, int]]:
    """The ranges that will be removed, merged and within the video."""
    ranges = []
    for c in merge([c for c in cuts if c.on]):
        a, b = max(0, c.start), min(duration, c.end)
        if b - a >= MIN_CUT_MS:
            ranges.append((a, b))
    return ranges


def removed_ms(ranges: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in ranges)


# --- Moving time codes ---------------------------------------------------------------------

def map_time(t: int, ranges: list[tuple[int, int]]) -> int:
    """A time in the original video -> the time in the cut video (inside a cut: where the cut was)."""
    shift = 0
    for a, b in ranges:
        if t >= b:
            shift += b - a
        elif t > a:
            return a - shift
        else:
            break
    return t - shift


def remap_cues(cues: list[Cue], ranges: list[tuple[int, int]]) -> list[Cue]:
    """Subtitles for the cut video. A block more than half inside a cut goes; the rest move (and are trimmed)."""
    out = []
    for c in cues:
        inside = sum(max(0, min(c.end, b) - max(c.start, a)) for a, b in ranges)
        if inside * 2 > max(1, c.end - c.start):
            continue
        s, e = map_time(c.start, ranges), map_time(c.end, ranges)
        if e - s < 300:
            continue
        if out and s < out[-1].end:
            s = out[-1].end
        if e - s >= 300:
            out.append(Cue(s, e, c.text))
    return out


def blocks_in(cues: list[Cue], start: int, end: int) -> list[int]:
    """1-based numbers of the blocks that overlap a time range."""
    starts = [c.start for c in cues]
    i = max(0, bisect.bisect_right(starts, start) - 1)
    out = []
    while i < len(cues) and cues[i].start < end:
        if cues[i].end > start:
            out.append(i + 1)
        i += 1
    return out


def text_at(cues: list[Cue], t: int) -> str:
    starts = [c.start for c in cues]
    i = bisect.bisect_right(starts, t) - 1
    if 0 <= i < len(cues) and cues[i].start <= t <= cues[i].end:
        return cues[i].text.replace("\n", " ")
    return ""


def excerpt(cues: list[Cue], start: int, end: int, chars: int = 90) -> str:
    text = " ".join(cues[i - 1].text.replace("\n", " ") for i in blocks_in(cues, start, end))
    return text if len(text) <= chars else text[:chars].rsplit(" ", 1)[0] + " ..."


# --- ffmpeg -------------------------------------------------------------------------------

def _filters(ranges: list[tuple[int, int]], fmt: str, has_audio: bool) -> str:
    secs = [(a / 1000, b / 1000) for a, b in ranges]
    inside = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in secs)
    # Kept frames move back by the length of every cut before them. This keeps the original frame timing
    # (Teams recordings may have a variable frame rate), unlike renumbering the frames.
    shift = "+".join(f"{b - a:.3f}*gte(T,{b:.3f})" for a, b in secs)
    v = f"[0:v:0]select='not({inside})',setpts='PTS-STARTPTS-({shift})/TB',format={fmt}[v]"
    if not has_audio:
        return v
    # Small audio frames, so a cut is precise to a few ms, and a 25 ms fade on each side of a join (no click).
    edges = [x for a, b in secs for x in (a, b)]
    dist = f"abs(t-{edges[0]:.3f})"
    for x in edges[1:]:
        dist = f"min({dist},abs(t-{x:.3f}))"
    a = (f"[0:a:0]aresample=48000,asetnsamples=n=240:p=0,aselect='not({inside})',"
         f"volume='min(1,({dist})/0.025)':eval=frame,asetpts='PTS-STARTPTS-({shift})/TB',"
         f"aformat=channel_layouts=stereo[a]")
    return v + ";\n" + a


def cut_video(runner: Runner, video: Path, ranges: list[tuple[int, int]], out: Path, duration_ms: int,
              has_audio: bool, prog, workdir: Path):
    """Write the video without `ranges`, in one pass. Quick Sync first (fast on Intel laptops), then libx264.
    The filter graph goes in a file: with many cuts it is longer than Windows allows on a command line."""
    from .engine import AUDIO_AAC, _ffmpeg_progress
    new_ms = duration_ms - removed_ms(ranges)
    tmp_out = out.with_name(out.stem + ".tmp" + out.suffix)
    attempts = [("nv12", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20"]),
                ("yuv420p", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"])]
    for n, (fmt, video_args) in enumerate(attempts):
        graph = workdir / f"cut{n}.txt"
        graph.write_text(_filters(ranges, fmt, has_audio), encoding="utf-8")
        maps = ["-map", "[v]"] + (["-map", "[a]"] + AUDIO_AAC if has_audio else [])
        args = [FFMPEG(), "-hide_banner", "-y", "-i", video, "-/filter_complex", graph.name] + maps + video_args + \
               ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp_out]
        code, _ = runner.run(args, _ffmpeg_progress(prog, new_ms), cwd=workdir)
        if code == 0 and tmp_out.exists():
            tmp_out.replace(out)
            log.info("cut %d ranges (%.1f min) from %s -> %s", len(ranges), removed_ms(ranges) / 60000, video.name, out.name)
            return
        tmp_out.unlink(missing_ok=True)
        if n + 1 < len(attempts):
            log.info("cut: encoder attempt %d failed, trying the next", n + 1)
            prog.update(0.0, t("cutting.encode_retry"))
    raise ToolError(t("cutting.error.cut", file=out.name))


def frame_png(runner: Runner, video: Path, ms: int, out: Path, width: int) -> bool:
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0, ms) / 1000:.3f}", "-i", video,
                          "-frames:v", "1", "-vf", f"scale={width}:-2", out], quiet=True)
    return code == 0 and out.exists()


def preview_transition(runner: Runner, video: Path, start: int, end: int, out: Path, around_ms: int = 4000) -> bool:
    """A short clip: the seconds before the cut, then the seconds after it, as the cut video will show it."""
    a0 = max(0, start - around_ms)
    args = [FFMPEG(), "-hide_banner", "-v", "error", "-y",
            "-ss", f"{a0 / 1000:.3f}", "-t", f"{(start - a0) / 1000:.3f}", "-i", video,
            "-ss", f"{end / 1000:.3f}", "-t", f"{around_ms / 1000:.3f}", "-i", video,
            "-filter_complex", "[0:v:0]scale=1280:-2,setsar=1[v0];[1:v:0]scale=1280:-2,setsar=1[v1];"
                               "[0:a:0]aresample=48000[a0];[1:a:0]aresample=48000[a1];"
                               "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k", out]
    if start - a0 < 100:  # a cut from the very start: only what comes after it
        args = [FFMPEG(), "-hide_banner", "-v", "error", "-y", "-ss", f"{end / 1000:.3f}", "-t", f"{around_ms / 1000:.3f}",
                "-i", video, "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k", out]
    code, _ = runner.run(args, quiet=True)
    return code == 0 and out.exists()


def preview_cut(runner: Runner, video: Path, start: int, end: int, out: Path) -> bool:
    """What will be removed (stream copy, so it is instant even for long cuts; it may start a little early)."""
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-ss", f"{start / 1000:.3f}", "-i", video,
                          "-t", f"{(end - start) / 1000:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero", out],
                         quiet=True)
    return code == 0 and out.exists()
