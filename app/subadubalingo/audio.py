"""Forbedr lyden: loudness normalisation and light noise reduction of the original speech in the videos the app writes.

One quick first pass over the audio alone (ebur128, about 0.3 s per minute of video) measures the loudness and the
noise floor (the quietest tenth of the 400 ms windows). The chain is then:

- afwtdn, wavelet noise reduction with the measured noise level, only when there is steady noise (Teams has
  already removed most noise, and its pauses are digital silence, so Teams recordings usually skip it);
- a highpass at 80 Hz (rumble, desk knocks, mains hum);
- one gain to the loudness target and a peak limiter.

Measured on the 18 s test call (tests/test_audio.py): -19.0 -> -16.2 LUFS, peak at most -1.5 dB; with white noise
added at -53 dB, the pauses 16 dB quieter and the speech unchanged. Tried and left out:

- loudnorm: the same loudness, but it works at 192 kHz, and the two passes took 6 minutes for a 31-minute webinar,
  against about half a minute for this chain;
- afftdn: took 4 dB of noise off, but delays the sound by 25 ms (its FFT window), which moves it against the picture;
- afwtdn's own adaptive noise estimate: anything from 0 to 8 dB less noise on the same noise with another seed;
- anlmdn: did nothing at speech settings and is 10 times slower.

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

TARGET_I = -16.0      # LUFS: speech on a laptop or phone (EBU R128 broadcast is -23, which sounds weak there)
TARGET_TP = -1.5      # dBFS peak ceiling: room for the AAC encoder
MAX_GAIN = 20.0       # dB: a nearly silent track is not blown up into noise
MIN_LOUDNESS = -60.0  # LUFS: below this there is no speech to normalise
NOISE_MIN = -75.0     # LUFS: a quieter floor is (near) digital silence, nothing to remove
NOISE_MAX = -35.0     # LUFS: never treat more than this as noise (speech would suffer)
NOISE_GAP = 15.0      # LU: the floor must be this far below the speech, else the quiet tenth is speech, not noise
HIGHPASS = "highpass=f=80"

# The written file's comment says the sound went through the chain.
MARK_TAG = "SubaDubaLingo: improved sound"
MARK_ARGS = ["-metadata", f"comment={MARK_TAG}"]


@dataclass
class R128:
    i: float                    # integrated loudness, LUFS
    lra: float                  # loudness range, LU
    peak: float                 # true peak, dBFS (-70 when not measured)
    floor: float | None = None  # noise floor: the 10th percentile of the momentary loudness, LUFS


def _num(v, missing: float = -70.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return missing
    return f if f == f and abs(f) != float("inf") else missing


_MOMENTARY = re.compile(r"\bt:\s*([\d.]+).*?\bM:\s*(-?[\d.]+|-?inf)")


def parse_ebur128(lines: list[str]) -> R128 | None:
    """The summary ebur128 prints at the end, and the noise floor from its frame lines (when it logged them)."""
    text = "\n".join(lines)
    k = text.rfind("Summary:")
    if k < 0:
        return None
    summary = text[k:]

    def grab(label: str) -> float:
        m = re.search(label + r":\s*(-?[\d.]+|-?inf)", summary)
        return _num(m.group(1)) if m else -70.0
    # The first 0.4 s fill the meter's window; silence reads -120
    momentary = sorted(_num(m.group(2), -120.0) for line in lines if (m := _MOMENTARY.search(line))
                       and float(m.group(1)) >= 0.4)
    floor = momentary[len(momentary) // 10] if momentary else None
    return R128(grab(r"\bI"), grab(r"\bLRA"), grab(r"\bPeak"), floor)


def gain_db(i: float) -> float | None:
    """The gain that brings integrated loudness `i` to the target, or None when there is no speech."""
    if i <= MIN_LOUDNESS:
        return None
    return max(-MAX_GAIN, min(MAX_GAIN, TARGET_I - i))


def noise_sigma(i: float, floor: float | None) -> float | None:
    """afwtdn's noise level (linear amplitude) for a measured noise floor, or None when there is nothing to remove.
    For white, pink and brown noise the momentary loudness is 0-3 dB above the RMS level."""
    if floor is None or floor <= NOISE_MIN or floor > NOISE_MAX or i - floor < NOISE_GAP:
        return None
    return 10 ** ((floor - 2) / 20)


def chain(measured_i: float | None, rate: int = 48000, floor: float | None = None) -> str:
    """The audio filters for the original speech, ending at `rate`. Without a usable measurement (no speech, or the
    measurement failed) only the highpass runs."""
    parts = []
    sigma = noise_sigma(measured_i, floor) if measured_i is not None else None
    if sigma:
        parts.append(f"afwtdn=sigma={sigma:.6f}")
    parts.append(HIGHPASS)
    g = gain_db(measured_i) if measured_i is not None else None
    if g is not None:
        limit = 10 ** (TARGET_TP / 20)
        parts.append(f"volume={g:.2f}dB")
        parts.append(f"alimiter=limit={limit:.4f}:attack=5:release=50:level=false:latency=1")
    parts.append(f"aresample={rate}")
    return ",".join(parts)


def ebur128(runner: Runner, path: Path, pre: str = "", start: float | None = None, length: float | None = None,
            peak: bool = True, frames: bool = False, stream: str = "0:a:0") -> R128 | None:
    """Loudness of `path`'s audio (after the filters `pre`); with `frames` also the noise floor. Audio only, so it
    is fast."""
    lines: list[str] = []
    args = [FFMPEG(), "-hide_banner", "-nostats"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    meter = "ebur128=framelog=" + ("info" if frames else "verbose") + (":peak=true" if peak else "")
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
    m = ebur128(runner, video, HIGHPASS, peak=False, frames=True)
    if m is None:
        log.warning("audio: loudness measurement failed; highpass only")
        return chain(None, rate)
    log.info("audio: %s measured %.1f LUFS (LRA %.1f, noise floor %s LUFS): gain %s dB, noise sigma %s", video.name,
             m.i, m.lra, m.floor, gain_db(m.i), noise_sigma(m.i, m.floor))
    return chain(m.i, rate, m.floor)

# --- The check boxes (setup card 2 and Indstillinger > Lyd; design T8 item 8) ----------------------------------


def applies(cut: bool, speech, subs, keep_original: bool) -> bool:
    """Whether a run with these choices writes a video with the original speech in it: the cut video, the video
    with burned-in subtitles, or a dub with the original kept under it."""
    return bool(cut or (subs and not speech) or (speech and keep_original))


def setup_option(parent, var, row: int, indent: int, wrap):
    """Setup card 2's check box with its hint under it, in grid `row` of `parent`. Returns the check button (it is
    disabled when the choices make no video with the original speech)."""
    from tkinter import ttk
    box = ttk.Frame(parent)
    box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    box.columnconfigure(0, weight=1)
    cb = ttk.Checkbutton(box, variable=var, text=t("audio.setup_option"))
    cb.grid(row=0, column=0, sticky="w")
    hint = ttk.Label(box, text=t("audio.setup_hint"), style="Hint.TLabel", justify="left")
    hint.grid(row=1, column=0, sticky="ew", padx=(indent, 0))
    wrap(hint, parent, 40 + indent)
    return cb


def settings_card(parent, var):
    """Indstillinger's "Lyd" card: the default for the setup check box."""
    from tkinter import ttk
    from . import skin
    card = skin.Card(parent, text=t("audio.settings_title"), padding=12)
    ttk.Checkbutton(card, text=t("audio.settings_option"), variable=var).pack(anchor="w")
    ttk.Label(card, text=t("audio.settings_hint"), style="Hint.TLabel").pack(anchor="w", padx=(30, 0))
    return card
