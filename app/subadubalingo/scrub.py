"""Scrubbing in the cut review: while the user drags the edge of a cut, the picture and the sound at that point.

The pictures are small thumbnails of the whole video, 4 a second, made in one ffmpeg pass when the review opens.
The sound is read from a 16 kHz mono WAV of the video: a short snippet at the edge, played asynchronously, each new
one cutting off the one before (the feel of a tape being scrubbed). The UI only calls Scrubber; how the picture is
shown is the review's business.
"""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import cutting
from .common import FFMPEG, Runner, log

try:
    import winsound
except ImportError:   # not on Windows: scrubbing is silent
    winsound = None

THUMB_MS = 250        # one thumbnail per 250 ms (fps=4); thumbnail n (1-based) is the picture at (n - 1) * 250 ms
SNIPPET_MS = 200      # the sound played at the edge
FADE_MS = 5           # fade in and out, so a snippet does not click
SOUND_FILES = 3       # snippet files used in turn, so a file is never rewritten while it plays


def fmt_fine(ms: int) -> str:
    """0:06.25 or 1:02:03.50: the clock with hundredths, for the point being dragged."""
    cs = max(0, int(round(ms / 10)))
    s, cs = divmod(cs, 100)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}.{cs:02}" if h else f"{m}:{s:02}.{cs:02}"


def thumb_index(ms: int) -> int:
    """The number (1-based, as ffmpeg writes %06d) of the thumbnail nearest a time."""
    return max(0, int(round(ms / THUMB_MS))) + 1


def thumb_name(n: int) -> str:
    return f"{n:06d}.png"


def faded(samples: np.ndarray, sr: int, fade_ms: int = FADE_MS) -> np.ndarray:
    """int16 samples with a linear fade in and out of fade_ms (shorter for very short snippets)."""
    x = samples.astype(np.float32)
    n = min(int(sr * fade_ms / 1000), len(x) // 2)
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, endpoint=False, dtype=np.float32)
        x[:n] *= ramp
        x[len(x) - n:] *= ramp[::-1]
    return np.clip(x, -32768, 32767).astype(np.int16)


def snippet(samples: np.ndarray, sr: int, ms: int, length_ms: int = SNIPPET_MS, fade_ms: int = FADE_MS) -> np.ndarray:
    """The faded snippet of length_ms that starts at ms (shorter at the end of the sound, empty after it)."""
    a = max(0, int(sr * ms / 1000))
    return faded(samples[a:a + int(sr * length_ms / 1000)], sr, fade_ms)


def read_snippet(wav: Path, ms: int, length_ms: int = SNIPPET_MS, fade_ms: int = FADE_MS) -> tuple[np.ndarray, int]:
    """The faded snippet at ms of a 16-bit WAV (first channel), read straight from the file: an hour of sound is
    never held in memory, and the file is not kept open (it lives in a folder that is removed afterwards)."""
    with wave.open(str(wav), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        a = max(0, int(sr * ms / 1000))
        if a >= w.getnframes():
            return np.zeros(0, dtype=np.int16), sr
        w.setpos(a)
        data = np.frombuffer(w.readframes(int(sr * length_ms / 1000)), dtype=np.int16)
    if ch > 1:
        data = data[::ch]
    return faded(data, sr, fade_ms), sr


def wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    """A mono 16-bit PCM WAV file's bytes."""
    data = np.asarray(samples, dtype="<i2").tobytes()
    head = (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
            + sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data" + len(data).to_bytes(4, "little"))
    return head + data


class Latest:
    """A worker thread that only serves the newest request: one still waiting is replaced by a newer one."""

    def __init__(self, work):
        self._work = work
        self._next = None
        self._cond = threading.Condition()
        self._stopped = False
        threading.Thread(target=self._loop, daemon=True).start()

    def put(self, item):
        with self._cond:
            self._next = item
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stopped = True
            self._cond.notify()

    def _loop(self):
        while True:
            with self._cond:
                while self._next is None and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                item, self._next = self._next, None
            try:
                self._work(item)
            except Exception:
                log.exception("scrub worker failed")


class Scrubber:
    """Thumbnails and sound for scrubbing one video, made in the background in `folder`. `wav` is the job's 16 kHz
    mono WAV when there is one; without it the sound is extracted too. Everything here may be called from the UI
    thread: nothing waits for ffmpeg."""

    def __init__(self, video: Path, folder: Path, width: int, wav: Path | None = None):
        self.video, self.width = video, width
        self.thumbs = folder / "scrub"
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.wav = wav if wav and wav.exists() else None
        self.runner = Runner()          # the long extraction; cancelled when the review closes
        self.thumbs_done = False
        self.seconds: dict[str, float] = {}   # how long each extraction took (logged, and read by the tests)
        self._sound_n = 0
        self._sound = Latest(self._play)
        threading.Thread(target=self._extract, daemon=True).start()

    def _extract(self):
        try:
            if self.wav is None:
                t0 = time.monotonic()
                wav = self.folder / "scrub.wav"
                code, _ = self.runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-threads", "2",
                                           "-i", self.video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                                           wav], quiet=True)
                if code == 0 and wav.exists():
                    self.wav = wav
                self.seconds["audio"] = time.monotonic() - t0
            t0 = time.monotonic()
            # Two decoder threads and below-normal priority (Runner): the user may be working meanwhile
            code, _ = self.runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-threads", "2",
                                       "-i", self.video, "-an", "-sn", "-vf", f"fps={1000 // THUMB_MS},scale={self.width}:-2",
                                       self.thumbs / "%06d.png"], quiet=True)
            self.thumbs_done = code == 0
            self.seconds["thumbs"] = time.monotonic() - t0
            log.info("scrub: thumbnails %s in %.1f s, sound %s", "made" if self.thumbs_done else "failed",
                     self.seconds["thumbs"], "ready" if self.wav else "missing")
        except Exception:   # cancelled (the review closed) or ffmpeg missing: scrubbing just stays without
            pass

    def thumb(self, ms: int) -> Path | None:
        """The finished thumbnail nearest ms, or None while it is not made yet. While ffmpeg runs, a file only counts
        once the next one exists (the newest may be half written)."""
        n = thumb_index(ms)
        p = self.thumbs / thumb_name(n)
        if self.thumbs_done:
            if p.exists():
                return p
            for back in range(1, 9):   # just past the last thumbnail (the end of the video): the last one
                p = self.thumbs / thumb_name(n - back)
                if n - back >= 1 and p.exists():
                    return p
            return None
        return p if (self.thumbs / thumb_name(n + 1)).exists() else None

    def frame(self, runner: Runner, ms: int) -> Path | None:
        """One exact frame at ms, made now (blocks: call from a worker)."""
        out = self.folder / f"scrub-{ms}.png"
        return out if cutting.frame_png(runner, self.video, ms, out, self.width) else None

    def play(self, ms: int):
        """Play the snippet at ms (cutting off the one playing); silent while the sound is not ready."""
        if winsound is not None and self.wav is not None:
            self._sound.put(ms)

    def _play(self, ms):
        if ms == "stop":
            winsound.PlaySound(None, 0)
            return
        if self.wav is None:
            return
        samples, sr = read_snippet(self.wav, ms)
        if not len(samples):
            return
        self._sound_n = (self._sound_n + 1) % SOUND_FILES
        f = self.folder / f"scrub-sound{self._sound_n}.wav"
        f.write_bytes(wav_bytes(samples, sr))
        winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def silence(self):
        """Stop the snippet playing (through the worker, so a snippet still waiting does not start after it)."""
        if winsound is not None:
            self._sound.put("stop")

    def close(self):
        self._sound.stop()
        if winsound is not None:
            winsound.PlaySound(None, 0)
        self.runner.cancel()
