"""Headless end-to-end run of the engine: python tests/smoke.py VIDEO [--gpu] [--dub SRT] [--burn-dub SRT]"""
import sys, time, tempfile, shutil, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import engine
from subadubalingo.common import LANG_BY_NAME, Runner, srt_name, burned_name, dub_name, guess_srt_language, read_srt, write_srt

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("--lang", default="Dansk"); ap.add_argument("--gpu", action="store_true")
ap.add_argument("--no-srt", action="store_true"); ap.add_argument("--no-burn", action="store_true")
ap.add_argument("--dub"); ap.add_argument("--burn-dub"); ap.add_argument("--model", default="large")
a = ap.parse_args()
v = Path(a.video); lang = LANG_BY_NAME[a.lang]; r = Runner()
last = [0]
def rep(o, s, d):
    if time.time() - last[0] > 3:
        last[0] = time.time(); print(f"{o*100:5.1f}% {s} | {d[:70]}", flush=True)
steps = [("a", 1), ("t", 20), ("b", 6), ("d1", 4), ("d2", 2)]
p = engine.Progress(rep, steps)
w = Path(tempfile.mkdtemp())
info = engine.probe(r, v); print(info)
t0 = time.time()
if not a.no_srt:
    p.start("a"); engine.extract_audio(r, v, w / "a.wav")
    p.start("t"); m = engine.find_model(engine.whisper_model_file(a.model).filename)
    cues = engine.transcribe(r, w / "a.wav", lang, m, info.duration_ms, a.gpu, p, w)
    write_srt(srt_name(v, lang), cues); print("SRT", len(cues), f"{time.time()-t0:.0f}s")
if not a.no_burn:
    t1 = time.time(); p.start("b"); engine.burn(r, v, srt_name(v, lang), burned_name(v, lang), info, p, w); print("BURN", f"{time.time()-t1:.0f}s")
if a.dub:
    s = Path(a.dub); dl = guess_srt_language(s); bs = Path(a.burn_dub) if a.burn_dub else None
    out = dub_name(v, dl, guess_srt_language(bs) if bs else None)
    t1 = time.time()
    engine.dub(r, v, read_srt(s), engine.find_model(dl.voice + ".onnx"), out, bs, True, info, p, w, "d1", "d2")
    print("DUB", out.name, f"{time.time()-t1:.0f}s")
shutil.rmtree(w, ignore_errors=True)
