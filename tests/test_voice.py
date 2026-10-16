"""Lyt til stemmen without the GUI: which voices a sample uses per voice mode, the cache key, the Piper call it
makes (the dub's run_piper), the cache, joining two voices, and one real Piper run of a short sentence.

  python tests/test_voice.py
"""
import json
import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np

from subadubalingo import common, engine
from subadubalingo.common import LANG_BY_CODE, Runner
from subadubalingo.i18n import t

tmp = Path(tempfile.mkdtemp(prefix="sdl-voice-"))
common._DATA_DIR = tmp / "data"          # never the real ~/.subadubalingo
common._DATA_DIR.mkdir()
from subadubalingo import voice_preview as vp

da, sv, no, en = (LANG_BY_CODE[c] for c in ("da", "sv", "no", "en"))
V = vp.SampleVoice

# --- Voices per mode: the ones the dub would use --------------------------------------------------------------
assert vp.sample_voices(sv, "one") == [V("sv_SE-nst-medium")]
assert vp.sample_voices(sv, "gender") == [V("sv_SE-nst-medium"), V("sv_SE-lisa-medium")]
assert vp.sample_voices(en, "gender") == [V("en_US-ryan-medium"), V("en_US-lessac-medium")]
assert vp.sample_voices(da, "gender") == [V("da_DK-talesyntese-medium")]      # one voice: the dub uses it for all
assert vp.sample_voices(sv, "person") == [V("sv_SE-nst-medium"), V("sv_SE-lisa-medium")]
assert vp.sample_voices(en, "person") == [V("en_US-ryan-medium"), V("en_US-lessac-medium")]
# Danish and Norwegian: the one voice, then raised for a woman (speakers.assign)
assert vp.sample_voices(da, "person") == [V("da_DK-talesyntese-medium"), V("da_DK-talesyntese-medium", 4.0)]
assert vp.sample_voices(no, "person") == [V("no_NO-talesyntese-medium"), V("no_NO-talesyntese-medium", 4.0)]
assert vp.sample_voices(sv, "nonsense") == vp.sample_voices(sv, "person")   # an unknown mode is the default
for l in (da, sv, no, en):
    for m in vp.MODES:
        assert all(v.stem in engine.PIPER_PATHS for v in vp.sample_voices(l, m)), (l, m)

# --- Sample sentences: one per language, in that language ---------------------------------------------------
texts = {l.code: vp.sample_text(l) for l in (da, sv, no, en)}
assert len(set(texts.values())) == 4 and not any(x.startswith("voice.") for x in texts.values()), texts
assert "dansk" in texts["da"] and "svenska" in texts["sv"] and "norsk" in texts["no"] and "English" in texts["en"]
for c in ("da", "sv", "no", "en"):     # the same keys in all four locale files
    keys = set(json.loads((Path(__file__).resolve().parent.parent / "assets" / "locales" / c / "voice.json")
                          .read_text(encoding="utf-8")))
    assert keys == set(json.loads((Path(__file__).resolve().parent.parent / "assets" / "locales" / "da" /
                                   "voice.json").read_text(encoding="utf-8"))), c

# --- What the rows in Indstillinger and the tooltip say ---------------------------------------------------------
from subadubalingo import i18n  # noqa: E402
assert vp.voice_name("sv_SE-lisa-medium") == "Lisa" and vp.voice_name("odd") == "Odd"
assert vp.voices_text(sv, "one") == "Nst"
assert vp.voices_text(sv, "gender") == t("voice.and", a=f"Nst ({t('voice.male')})", b=f"Lisa ({t('voice.female')})")
# One voice for the language: its name once, and it says so (design review round 4, decision 5)
assert vp.voices_text(da, "gender") == t("voice.one_voice", name="Talesyntese") == "Talesyntese (én stemme)"
assert vp.voices_text(da, "person") == vp.voices_text(da, "one") == "Talesyntese (én stemme)"
assert vp.fmt_time(0) == "0:00" and vp.fmt_time(4.9) == "0:04" and vp.fmt_time(65) == "1:05"

# --- Cache key ------------------------------------------------------------------------------------------------
k = vp.cache_key(V("sv_SE-nst-medium"), "Hej", None, 100)
assert k == vp.cache_key(V("sv_SE-nst-medium"), "Hej", None, 100)           # stable
assert k.startswith("sv_SE-nst-medium+0.0-") and k.endswith(".wav")
others = [vp.cache_key(V("sv_SE-lisa-medium"), "Hej", None, 100),           # another voice
          vp.cache_key(V("sv_SE-nst-medium", 2.5), "Hej", None, 100),       # moved in pitch
          vp.cache_key(V("sv_SE-nst-medium"), "Hej då", None, 100),         # another sentence (a new translation)
          vp.cache_key(V("sv_SE-nst-medium"), "Hej", 1.2, 100),             # another pace
          vp.cache_key(V("sv_SE-nst-medium"), "Hej", None, 101)]            # the voice downloaded again
assert len(set(others + [k])) == 6
assert vp.pair_key(["a.wav", "b.wav"]) != vp.pair_key(["b.wav", "a.wav"])

# --- The Piper call: engine.run_piper with the model, its config, JSON input, the voice's own pace ------------
fake_models = tmp / "models"
fake_models.mkdir()
for stem in ("sv_SE-nst-medium", "sv_SE-lisa-medium", "da_DK-talesyntese-medium"):
    (fake_models / f"{stem}.onnx").write_bytes(b"x" * 10)
    (fake_models / f"{stem}.onnx.json").write_text("{}", encoding="utf-8")
real_find = engine.find_model
engine.find_model = lambda name: (fake_models / name) if (fake_models / name).exists() else None


def tone(path: Path, seconds: float, sr: int = 22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((np.sin(np.arange(int(sr * seconds)) / 10) * 8000).astype(np.int16).tobytes())


calls = []


class FakeRunner(Runner):
    def run(self, args, on_line=None, cwd=None, stdin_text=None, quiet=False):
        args = [str(a) for a in args]
        calls.append((args, stdin_text))
        if args[0].endswith("piper.exe"):
            for line in stdin_text.splitlines():
                out = Path(json.loads(line)["output_file"])
                tone(out, 0.5)
                on_line(str(out))
        else:   # ffmpeg (the pitch shift): write the output file
            tone(Path(args[-1]), 0.5)
        return 0, []


r = FakeRunner()
dest = tmp / "one.wav"
vp.speak(r, fake_models / "sv_SE-nst-medium.onnx", V("sv_SE-nst-medium"), "  Hej <i>där</i>! ", dest)
(args, stdin), = calls
assert args[0] == str(common.PIPER())
assert args[1:3] == ["--model", str(fake_models / "sv_SE-nst-medium.onnx")]
assert args[3:5] == ["--config", str(fake_models / "sv_SE-nst-medium.onnx") + ".json"]
assert "--json-input" in args and "--length_scale" not in args             # the voice's own pace, as the dub's first run
item = json.loads(stdin.strip())
assert item["text"] == "Hej där!", item                                    # cleaned like the dub's sentences
assert item["output_file"].endswith("one-part.wav")                        # never a half file under the cache name
assert dest.exists() and not (tmp / "one-part.wav").exists()
calls.clear()
vp.speak(r, fake_models / "sv_SE-nst-medium.onnx", V("sv_SE-nst-medium"), "Hej", tmp / "pace.wav", 1.25)
assert calls[0][0][-2:] == ["--length_scale", "1.250"]

# A shifted voice: Piper, then the dub's pitch shift (ffmpeg asetrate/atempo)
calls.clear()
vp.speak(r, fake_models / "da_DK-talesyntese-medium.onnx", V("da_DK-talesyntese-medium", 4.0), "Hej", tmp / "up.wav")
assert len(calls) == 2 and calls[1][0][0].endswith("ffmpeg.exe") and any("asetrate" in a for a in calls[1][0])

# --- make_sample: cached per voice; two voices joined; a second click speaks nothing --------------------------
calls.clear()
one = vp.make_sample(r, sv, "one")
assert one.parent == vp.cache_dir() == common._DATA_DIR / "voice-preview" and one.exists()
assert len(calls) == 1
pair = vp.make_sample(r, sv, "person")
assert len(calls) == 2, "nst came from the cache, only lisa was spoken"
assert pair.name.startswith("pair-")
assert abs(vp.wav_seconds(pair) - (0.5 + vp.GAP_MS / 1000 + 0.5)) < 0.01
calls.clear()
assert vp.make_sample(r, sv, "person") == pair and vp.make_sample(r, sv, "one") == one and not calls
da_pair = vp.make_sample(r, da, "person")
assert [c[0][0].rsplit("\\", 1)[-1] for c in calls] == ["piper.exe", "piper.exe", "ffmpeg.exe"]

# Missing models: needed_files lists them, make_sample refuses
assert vp.needed_files(vp.sample_voices(sv, "person")) == []
missing = vp.needed_files(vp.sample_voices(en, "gender"))
assert sorted(m.filename for m in missing) == ["en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json",
                                               "en_US-ryan-medium.onnx", "en_US-ryan-medium.onnx.json"]
try:
    vp.make_sample(r, en, "one")
    raise AssertionError("no model, no sample")
except engine.ToolError:
    pass

# The bubble's level bars: loudness of 12 parts, the loudest 1.0
lv = vp.levels(pair)
assert len(lv) == 12 and max(lv) == 1.0 and min(lv) < 0.2, lv          # the gap between the voices is silent

# join resamples a voice at another rate
a, b = tmp / "a.wav", tmp / "b.wav"
tone(a, 1.0, 22050), tone(b, 1.0, 16000)
vp.join([a, b], tmp / "ab.wav")
assert abs(vp.wav_seconds(tmp / "ab.wav") - (2.0 + vp.GAP_MS / 1000)) < 0.01

# --- One real Piper run of a short sentence (any installed Swedish voice) -------------------------------------
engine.find_model = real_find
model = next((m for m in (real_find(s + ".onnx") for s in ("sv_SE-lisa-medium", "sv_SE-nst-medium",
                                                            "sv_SE-alma-medium", "da_DK-talesyntese-medium",
                                                            "en_US-lessac-medium")) if m), None)
if model is None:
    print("no Piper voice installed: the real run was skipped")
else:
    out = tmp / "real.wav"
    vp.speak(Runner(), model, V(model.name.removesuffix(".onnx")), "Hej!", out)
    secs = vp.wav_seconds(out)
    assert 0.2 < secs < 3, secs
    data, _ = vp._read(out)
    assert np.abs(data).max() > 1000, "silent"
    print(f"real Piper run: {model.name} said 'Hej!' in {secs:.2f} s")

shutil.rmtree(tmp, ignore_errors=True)
print("all ok")
