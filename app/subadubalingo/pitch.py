"""Voice pitch (fundamental frequency) from mono audio, with numpy only: autocorrelation on short frames.

Used to tell a deep (typically male) from a light (typically female) voice in the original speech, so the dub can
use a matching voice. Men speak around 85-155 Hz, women around 165-255 Hz.
"""
from __future__ import annotations

import numpy as np

FRAME_MS = 40
HOP_MS = 20
F0_MIN, F0_MAX = 70.0, 400.0


def frames_f0(data: np.ndarray, sr: int) -> np.ndarray:
    """F0 in Hz per 20 ms hop (0 where the frame is unvoiced or quiet)."""
    n = int(sr * FRAME_MS / 1000)
    hop = int(sr * HOP_MS / 1000)
    if len(data) < n:
        return np.zeros(0, dtype=np.float32)
    count = 1 + (len(data) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    fr = data[idx].astype(np.float32)
    fr = fr - fr.mean(axis=1, keepdims=True)
    energy = np.sqrt((fr ** 2).mean(axis=1))
    loud = energy > max(1e-4, np.percentile(energy, 60) * 0.5)
    lag_min, lag_max = int(sr / F0_MAX), int(sr / F0_MIN)
    # Autocorrelation through the FFT, for all frames at once
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(fr * np.hanning(n)[None, :], size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, size, axis=1)[:, :lag_max + 1]
    ac0 = ac[:, :1] + 1e-12
    seg = ac[:, lag_min:lag_max + 1] / ac0
    best = seg.argmax(axis=1)
    peak = seg[np.arange(len(seg)), best]
    f0 = sr / (best + lag_min)
    voiced = loud & (peak > 0.45)
    return np.where(voiced, f0, 0.0).astype(np.float32)


def median_f0(f0: np.ndarray, min_voiced: int = 8) -> float | None:
    """Median F0 of the voiced frames, or None when there are too few to tell."""
    v = f0[f0 > 0]
    return float(np.median(v)) if len(v) >= min_voiced else None
