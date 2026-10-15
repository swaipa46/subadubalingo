"""Scrubbing without the GUI: time -> thumbnail, the sound snippet (slice and fade), and the WAV bytes.

  python tests/test_scrub.py
"""
import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np

from subadubalingo import scrub

# Thumbnail n (1-based) is the picture at (n - 1) * 250 ms; a time goes to the nearest
assert scrub.thumb_index(0) == 1
assert scrub.thumb_index(124) == 1 and scrub.thumb_index(126) == 2
assert scrub.thumb_index(250) == 2 and scrub.thumb_index(6_250) == 26 and scrub.thumb_index(6_370) == 26
assert scrub.thumb_index(6_380) == 27
assert scrub.thumb_index(-500) == 1
assert scrub.thumb_name(26) == "000026.png"
assert scrub.fmt_fine(6_250) == "0:06.25", scrub.fmt_fine(6_250)
assert scrub.fmt_fine(62_004) == "1:02.00" and scrub.fmt_fine(3_723_500) == "1:02:03.50"

# Snippet: 200 ms from the asked time, faded in and out, the middle untouched
sr = 16000
samples = np.full(sr * 2, 10_000, dtype=np.int16)   # 2 s of a constant level
cut = scrub.snippet(samples, sr, 1000)
assert len(cut) == sr * 200 // 1000 and cut.dtype == np.int16
fade = sr * scrub.FADE_MS // 1000
assert cut[0] == 0 and cut[-1] < 10_000 // fade + 1, (cut[0], cut[-1])
assert np.all(np.diff(cut[:fade].astype(int)) >= 0) and np.all(np.diff(cut[-fade:].astype(int)) <= 0)
assert np.all(cut[fade:-fade] == 10_000)
ramp = np.arange(sr * 2, dtype=np.int16)   # the sample's own index (up to 32000), to see where the slice starts
assert scrub.snippet(ramp, sr, 1000, fade_ms=0)[0] == sr
assert len(scrub.snippet(samples, sr, 1900)) == sr // 10      # at the end: only what is left
assert len(scrub.snippet(samples, sr, 5000)) == 0             # after the end: nothing
assert len(scrub.faded(np.full(3, 100, dtype=np.int16), sr)) == 3   # shorter than the fades: no error

# WAV bytes: a valid mono 16-bit file that reads back to the same samples
tmp = Path(tempfile.mkdtemp())
try:
    data = scrub.wav_bytes(cut, sr)
    assert data[:4] == b"RIFF" and data[8:16] == b"WAVEfmt " and data[36:40] == b"data"
    assert int.from_bytes(data[4:8], "little") == len(data) - 8 and int.from_bytes(data[40:44], "little") == len(cut) * 2
    f = tmp / "s.wav"
    f.write_bytes(data)
    with wave.open(str(f), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, sr, len(cut))
        assert np.array_equal(np.frombuffer(w.readframes(len(cut)), dtype=np.int16), cut)

    # read_snippet from a stereo file takes the first channel, from the right place
    st = tmp / "st.wav"
    with wave.open(str(st), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(np.stack([ramp, -ramp], axis=1).astype("<i2").tobytes())
    got, got_sr = scrub.read_snippet(st, 500, fade_ms=0)
    assert got_sr == sr and len(got) == sr // 5 and got[0] == sr // 2 and got[1] == sr // 2 + 1, got[:3]
    assert len(scrub.read_snippet(st, 9_000)[0]) == 0
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# Latest: a request still waiting is replaced by the newest
import threading
import time
seen, gate = [], threading.Event()
worker = scrub.Latest(lambda x: (gate.wait(2), seen.append(x)))
worker.put(1)
time.sleep(0.1)          # 1 is being served (waits at the gate)
for x in (2, 3, 4):
    worker.put(x)
gate.set()
time.sleep(0.2)
worker.stop()
assert seen == [1, 4], seen
print("scrub ok")
