"""Forbedr lyden (audio.py) without the GUI: the filter chain, the dub's mix graph, and (with a video) a real run.

  python tests/test_audio.py [VIDEO]

With VIDEO (default: the 18 s test recording, if it is there): takes 5 s with speech from a copy in a temp
folder, puts it through the chain, and checks the loudness (ebur128) before and after, that the sound has not
moved in time, that the result is marked so it is not cleaned twice, and that a cut video gets the chain too.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np

from subadubalingo import audio, cutting, engine
from subadubalingo.common import FFMPEG, NO_WINDOW, Runner

# --- The chain ----------------------------------------------------------------------------------------------
c = audio.chain(-20.0, floor=-55.0)
parts = c.split(",")
assert parts[0].startswith("afwtdn=sigma=") and parts[1] == "highpass=f=80", c
assert abs(float(parts[0].split("=")[-1]) - 10 ** (-57 / 20)) < 1e-6, c   # floor -55 LUFS -> RMS about -57 dB
assert "volume=4.00dB" in parts, c                                  # -20 LUFS -> -16 LUFS
assert any(p.startswith("alimiter=") and "latency=1" in p for p in parts), c   # look-ahead compensated
assert parts[-1] == "aresample=48000", c
for slow_or_shifting in ("loudnorm", "afftdn", "adaptive", "atempo", "asetpts", "adelay"):   # nothing that moves time
    assert slow_or_shifting not in c, c
assert audio.chain(-20.0, 16000).endswith("aresample=16000")
# Noise reduction only with steady noise well below the speech
assert audio.chain(-20.0) == audio.chain(-20.0, floor=-120.0)      # digital silence in the pauses (Teams)
for floor in (None, -120.0, -80.0, -30.0, -25.0):
    assert audio.noise_sigma(-20.0, floor) is None, floor
assert audio.noise_sigma(-20.0, -40.0) and audio.noise_sigma(-30.0, -40.0) is None   # 10 LU: that is speech
# No speech (or no measurement): highpass only, no gain
for i in (None, -70.0, -61.0):
    c0 = audio.chain(i, floor=-65.0)
    assert c0 == "highpass=f=80,aresample=48000", c0
assert audio.gain_db(-50.0) == audio.MAX_GAIN and audio.gain_db(0.0) == -16.0
assert abs(audio.gain_db(-12.5) + 3.5) < 1e-9
assert audio.prepare(Runner(), Path("nope.mp4"), False) is None    # off: nothing runs
assert audio.applies(True, None, None, False) and audio.applies(False, None, "no", False)
assert not audio.applies(False, "sv", "no", False) and audio.applies(False, "sv", None, True)
assert not audio.applies(False, None, None, True)
print("chain ok")

summary = """[Parsed_ebur128_0 @ 000001] Summary:

  Integrated loudness:
    I:         -20.4 LUFS
    Threshold: -31.2 LUFS

  Loudness range:
    LRA:         9.4 LU
    Threshold: -41.0 LUFS
    LRA low:   -26.6 LUFS
    LRA high:  -17.2 LUFS

  True peak:
    Peak:       -1.4 dBFS""".splitlines()
frame = "[Parsed_ebur128_1 @ 0000021] t: {t}  TARGET:-23 LUFS    M:{m} S:-30.0     I: -20.0 LUFS       LRA:   3.0 LU"
frames = [frame.format(t=k / 10, m=m) for k, m in
          enumerate([-120.7] * 4 + [-50.0] * 2 + [-60.0] + [-20.0] * 12 + ["-inf"] + [-25.0] * 3)]
r = audio.parse_ebur128(frames + summary)
assert (r.i, r.lra, r.peak) == (-20.4, 9.4, -1.4), r
assert r.floor == -60.0, r       # sorted from 0.4 s on: -inf (-120), -60, -50, -50, -25, ...; the tenth of 19 is index 1
assert audio.parse_ebur128(summary).floor is None
assert audio.parse_ebur128(["nothing"]) is None
assert audio.parse_ebur128(summary[:-1] + ["    Peak:       -inf dBFS"]).peak == -70.0
print("parse ok")

# --- The dub's mix: the dub voice (input 1) is never filtered ---------------------------------------------
plain, kept = engine.dub_mix(False, "X"), engine.dub_mix(True, None)
cleaned = engine.dub_mix(True, audio.chain(-20.0))
assert plain == "[1:a]aresample=48000,aformat=channel_layouts=stereo[a]", plain
assert kept.startswith("[0:a:0]volume=0.12[o];"), kept
voice = "[1:a]aresample=48000,volume=1.0[d]"
assert voice in kept and voice in cleaned and audio.HIGHPASS not in kept
assert cleaned.startswith("[0:a:0]" + audio.chain(-20.0) + ",volume=0.12[o];"), cleaned
assert cleaned.split(";")[1:] == kept.split(";")[1:], "only the original's branch may change"
g = cutting._filters([(1000, 2000)], "yuv420p", True, "highpass=f=80")
assert "[0:a:0]highpass=f=80,aresample=48000,asetnsamples" in g, g
assert "[0:a:0]aresample=48000,asetnsamples" in cutting._filters([(1000, 2000)], "yuv420p", True), "off: unchanged"
print("mix ok")

# --- A real run on 5 s of a video ---------------------------------------------------------------------------
DEFAULT = Path(os.environ.get("SDL_TEST_VIDEO", "C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4"))
src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
if not src.is_file():
    print("no test video; skipped the ffmpeg part")
    print("all ok")
    sys.exit(0)

work = Path(tempfile.mkdtemp(prefix="audio-test-"))
try:
    runner = Runner()

    def ff(*args):
        code, tail = runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", *args])
        assert code == 0, tail

    def samples(path: Path) -> np.ndarray:
        raw = subprocess.run([str(FFMPEG()), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "f32le", "-"], capture_output=True, creationflags=NO_WINDOW).stdout
        return np.frombuffer(raw, dtype=np.float32)

    video = work / "clip.mp4"
    # 8.5..13.5 s: "Nu skal vi se, om Anne, hun kan læse undertekster." (re-encoded, so it starts on a frame)
    ff("-ss", "8.5", "-t", "5", "-i", shutil.copy(src, work / "src.mp4"), "-c:v", "libx264", "-preset",
       "ultrafast", "-c:a", "aac", "-b:a", "128k", video)
    before = audio.ebur128(runner, video)
    assert before and before.i > -40, before

    clean = audio.prepare(runner, video, True)
    assert clean and "volume=" in clean, clean
    out = work / "clean.mp4"
    ff("-i", video, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", clean, *audio.MARK_ARGS,
       *engine.AUDIO_AAC, out)
    after = audio.ebur128(runner, out)
    print(f"before: {before.i:.1f} LUFS, LRA {before.lra:.1f}, peak {before.peak:.1f} dBFS")
    print(f"after:  {after.i:.1f} LUFS, LRA {after.lra:.1f}, peak {after.peak:.1f} dBFS")
    assert abs(after.i - audio.TARGET_I) <= 1.0, after
    assert after.peak <= audio.TARGET_TP + 0.6, after                 # AAC may overshoot the limiter a little
    assert abs(after.lra - before.lra) <= 3.0, (before, after)        # a gain, not a compressor

    def lag_ms(a: np.ndarray, b: np.ndarray) -> float:
        """How much later b is than a (cross-correlation peak within +- 50 ms), at 16 kHz."""
        n = min(len(a), len(b))
        a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
        lags = range(-800, 801)
        corr = [float(np.dot(a[max(0, -k):n - max(0, k)], b[max(0, k):n - max(0, -k)])) for k in lags]
        return list(lags)[int(np.argmax(corr))] / 16

    # Not moved in time: same length, and the waveforms line up (cross-correlation peak at 0 +- 1 ms)
    a, b = samples(video), samples(out)
    assert abs(len(a) - len(b)) <= 16000 * 0.03, (len(a), len(b))
    print(f"offset: {lag_ms(a, b):.2f} ms")
    assert abs(lag_ms(a, b)) <= 1.0

    # Noise: the whole call with white noise added; the pause (0.3-1.9 s) gets quieter, the speech does not
    noisy = work / "noisy.wav"
    ff("-i", work / "src.mp4", "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.004:r=16000,highpass=f=100",
       "-filter_complex", "[0:a:0]aresample=16000[s];[s][1:a]amix=inputs=2:duration=first:normalize=0[a]",
       "-map", "[a]", "-ac", "1", "-c:a", "pcm_s16le", noisy)

    def rms_db(x, a, b):
        return 20 * np.log10(max(1e-9, float(np.sqrt((x[int(a * 16000):int(b * 16000)] ** 2).mean()))))
    denoised = work / "denoised.wav"
    m = audio.ebur128(runner, noisy, audio.HIGHPASS, peak=False, frames=True)
    noisy_chain = audio.chain(m.i, 16000, m.floor)
    print(f"noise:  floor {m.floor:.1f} LUFS -> {noisy_chain.split(',')[0]}")
    assert noisy_chain.startswith("afwtdn=sigma="), noisy_chain
    ff("-i", noisy, "-af", noisy_chain.split(",volume")[0], "-c:a", "pcm_s16le", denoised)
    x0, x1 = samples(noisy), samples(denoised)
    pause0, pause1, speech0, speech1 = rms_db(x0, .3, 1.9), rms_db(x1, .3, 1.9), rms_db(x0, 9, 12), rms_db(x1, 9, 12)
    print(f"noise:  pause {pause0:.1f} -> {pause1:.1f} dB, speech {speech0:.1f} -> {speech1:.1f} dB")
    assert pause1 <= pause0 - 8 and abs(speech1 - speech0) <= 1.0
    assert abs(lag_ms(x0, x1)) <= 1.0, lag_ms(x0, x1)

    # Marked, so a cut video that is burned or dubbed later is not cleaned twice
    assert audio.is_marked(runner, out) and not audio.is_marked(runner, video)
    assert audio.prepare(runner, out, True) is None

    # The cut: the chain goes in, and the cut video is louder and marked
    class Prog:
        def update(self, frac, text):
            pass
    cut = work / "cut.mp4"
    cutting.cut_video(runner, video, [(1000, 1500)], cut, 5000, True, Prog(), work, improve_audio=True)
    cut_r = audio.ebur128(runner, cut)
    print(f"cut:    {cut_r.i:.1f} LUFS")
    assert abs(cut_r.i - audio.TARGET_I) <= 1.5 and audio.is_marked(runner, cut), cut_r
    plain_cut = work / "cut-plain.mp4"
    cutting.cut_video(runner, video, [(1000, 1500)], plain_cut, 5000, True, Prog(), work)
    assert not audio.is_marked(runner, plain_cut) and abs(audio.ebur128(runner, plain_cut).i - before.i) <= 1.5
    print("ffmpeg ok")
finally:
    shutil.rmtree(work, ignore_errors=True)
print("all ok")
