"""Forbedr lyden: loudness normalisation and light noise reduction of the original speech in the videos the app writes.

The chain is afwtdn (wavelet noise reduction of steady hiss and fan noise; Teams has already removed most noise,
and its pauses are digital silence), a highpass (rumble, desk knocks), one gain to the loudness target and a peak
limiter. The gain comes from a quick first pass (ebur128 on the audio alone, about 0.3 s per minute of video).
loudnorm was measured too: the same loudness, but it works at 192 kHz and the two passes took 6 minutes for a
31-minute webinar, against about half a minute for this chain.

Every filter keeps the samples' times (the limiter compensates its look-ahead), so the picture and the subtitles
stay in sync. The dub's own voice track is never put through it. A video written with the chain is marked
(MARK_TAG), so a cut video that is dubbed or burned later is not cleaned twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import FFMPEG, Runner, log
from .i18n import t

TARGET_I = -16.0     # LUFS: speech on a laptop or phone (EBU R128 broadcast is -23, which sounds weak there)
TARGET_TP = -1.5     # dBFS peak ceiling: room for the AAC encoder
MAX_GAIN = 20.0      # dB: a nearly silent track is not blown up into noise
MIN_LOUDNESS = -60.0 # LUFS: below this there is no speech to normalise

# Wavelet noise reduction with the noise level tracked through the file, then rumble below speech (in this order:
# the other way round takes off half as much noise). Measured on the test call with white noise added at -53 dB:
# pauses 8 dB quieter, the speech unchanged. afftdn took 4 dB off but delays the sound by 25 ms (its FFT window),
# which would move it against the picture; anlmdn did nothing at speech settings and is 10 times slower.
PRE_FILTERS = "afwtdn=adaptive=1,highpass=f=80"

# The written file's comment says the sound went through the chain.
MARK_TAG = "SubaDubaLingo: improved sound"
MARK_ARGS = ["-metadata", f"comment={MARK_TAG}"]


@dataclass
class R128:
    i: float      # integrated loudness, LUFS
    lra: float    # loudness range, LU
    peak: float   # true peak, dBFS (-70 when not measured)


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return -70.0
    return f if f == f and abs(f) != float("inf") else -70.0


def parse_ebur128(lines: list[str]) -> R128 | None:
    """The summary ebur128 prints at the end."""
    text = "\n".join(lines)
    k = text.rfind("Summary:")
    if k < 0:
        return None
    summary = text[k:]

    def grab(label: str) -> float:
        m = re.search(label + r":\s*(-?[\d.]+|-?inf)", summary)
        return _num(m.group(1)) if m else -70.0
    return R128(grab(r"\bI"), grab(r"\bLRA"), grab(r"\bPeak"))


def gain_db(i: float) -> float | None:
    """The gain that brings integrated loudness `i` to the target, or None when there is no speech."""
    if i <= MIN_LOUDNESS:
        return None
    return max(-MAX_GAIN, min(MAX_GAIN, TARGET_I - i))


def chain(measured_i: float | None, rate: int = 48000) -> str:
    """The audio filters for the original speech, ending at `rate`. Without a usable measurement (no speech, or the
    measurement failed) only the cleaning runs."""
    parts = [PRE_FILTERS]
    g = gain_db(measured_i) if measured_i is not None else None
    if g is not None:
        limit = 10 ** (TARGET_TP / 20)
        parts.append(f"volume={g:.2f}dB")
        parts.append(f"alimiter=limit={limit:.4f}:attack=5:release=50:level=false:latency=1")
    parts.append(f"aresample={rate}")
    return ",".join(parts)


def ebur128(runner: Runner, path: Path, pre: str = "", start: float | None = None, length: float | None = None,
            peak: bool = True, stream: str = "0:a:0") -> R128 | None:
    """Loudness of `path`'s audio (after the filters `pre`). Audio only, so it is fast."""
    lines: list[str] = []
    args = [FFMPEG(), "-hide_banner", "-nostats"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    meter = "ebur128=framelog=verbose" + (":peak=true" if peak else "")
    args += ["-i", path, "-map", stream, "-vn", "-af", (pre + "," if pre else "") + meter, "-f", "null", "-"]
    code, _ = runner.run(args, lines.append, quiet=True)
    return parse_ebur128(lines) if code == 0 else None


def is_marked(runner: Runner, video: Path) -> bool:
    _, lines = runner.run([FFMPEG(), "-hide_banner", "-i", video], quiet=True)
    return any(MARK_TAG in l for l in lines)


def prepare(runner: Runner, video: Path, enabled: bool, prog=None, rate: int = 48000) -> str | None:
    """The filter chain for `video`'s speech when Forbedr lyden is on, else None (also when `video` was written
    with it already). `prog` (engine.Progress) shows that the sound is being measured."""
    if not enabled:
        return None
    if is_marked(runner, video):
        log.info("audio: %s has the improved sound already", video.name)
        return None
    if prog is not None:
        prog.update(0.0, t("audio.measuring"))
    m = ebur128(runner, video, "highpass=f=80", peak=False)
    if m is None:
        log.warning("audio: loudness measurement failed; cleaning only")
    else:
        log.info("audio: %s measured %.1f LUFS (LRA %.1f), gain %s dB", video.name, m.i, m.lra, gain_db(m.i))
    return chain(m.i if m else None, rate)

# --- Indstillinger ------------------------------------------------------------------------------------------


def settings_option(parent, var) -> None:
    """The check box (and its hint) in Indstillinger, packed into `parent` above the dub options."""
    from tkinter import ttk
    ttk.Checkbutton(parent, text=t("audio.option"), variable=var).pack(anchor="w")
    ttk.Label(parent, text=t("audio.hint"), style="Hint.TLabel").pack(anchor="w", padx=(26, 0), pady=(0, 8))
