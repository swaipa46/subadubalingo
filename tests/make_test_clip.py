"""Makes the test media for machines without Kasper's recording (CI): an 18 s clip shaped like the real test video
(the same six Danish cues at the same times, silence in the first 2 s) with its Danish, English and Norwegian
subtitles next to it (srt_name), and a 45 s clip for test_cutting.py (which cuts 5-20 s and 40-41.5 s).

  python tests/make_test_clip.py OUT_DIR
  python tests/make_test_clip.py --fetch-voices MODELS_DIR    the Piper voices CI uses (the clip's Danish speaker,
                                                              and the Swedish ones voice_shots.py samples)

The speech is spoken by Piper with the first installed Danish/Swedish voice (engine.find_model, e.g. a voice in
app/models); without one the cues are tones, so the clip still has loud and quiet parts. The picture is ffmpeg's
testsrc2 on dark grey. Prints the clip's path last."""
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import engine, i18n  # noqa: E402
from subadubalingo.common import FFMPEG, LANG_BY_CODE, PIPER, srt_name  # noqa: E402

NAME = "SubaDubaLingo test - a meeting recording with a long name, as Teams gives them"   # editor_shot shortens
SR = 16000
LENGTH_S = 18.5
CUES = [  # start ms, end ms, text (the real test video's Danish subtitles)
    (2060, 3680, "Til mit webinar."),
    (3740, 5240, "Er det ikke bare her, fedt?"),
    (6180, 7680, "Testing, Testing."),
    (8600, 13160, "Nu skal vi se, om Anne,\nhun kan læse undertekster."),
    (13700, 16640, "Det ville jo være fedt,\nisær hvis de var på svensk."),
    (16960, 18460, "Hej, hej."),
]
# The same cues translated, as the real video has them (editor_shot.py wants the English ones)
TRANSLATED = {
    "en": ["For my webinar.", "Isn't it just here, cool?", "Testing, testing.",
           "Now let's see if Anne,\nshe can read subtitles.",
           "That would be great,\nespecially if they were in Swedish.", "Bye, bye."],
    "no": ["Til mitt webinar.", "Er det ikke bare her, kult?", "Testing, testing.",
           "Nå skal vi se om Anne,\nhun kan lese undertekster.",
           "Det hadde jo vært kult,\nspesielt hvis de var på svensk.", "Hei, hei."],
}


def stamp(ms: int) -> str:
    return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def read_wav(path: Path) -> np.ndarray:
    """Mono float32 at SR (Piper writes 22050 Hz; resampled by linear interpolation, good enough for a test)."""
    with wave.open(str(path), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    if sr != SR:
        x = np.interp(np.arange(0, len(x) * SR / sr) * sr / SR, np.arange(len(x)), x).astype(np.float32)
    return x


def speech(work: Path) -> tuple[np.ndarray, str]:
    track = np.zeros(int(LENGTH_S * SR), dtype=np.float32)
    voice = next((m for m in (engine.find_model(s + ".onnx") for s in (
        "da_DK-talesyntese-medium", "sv_SE-lisa-medium", "sv_SE-nst-medium", "sv_SE-alma-medium")) if m), None)
    for i, (start, end, text) in enumerate(CUES):
        a, b = start * SR // 1000, end * SR // 1000
        if voice:
            out = work / f"cue{i}.wav"
            subprocess.run([str(PIPER()), "--model", str(voice), "--config", f"{voice}.json", "--output_file",
                            str(out)], input=text.replace("\n", " ").encode("utf-8"), cwd=PIPER().parent,
                           check=True, capture_output=True)
            x = read_wav(out)[: b - a]
            x *= 0.5 / max(1e-6, float(np.abs(x).max()))
        else:   # syllable-like tone bursts
            t = np.arange(b - a) / SR
            x = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) ** 2
        track[a:a + len(x)] += x
    return track, voice.name if voice else "tones"


def write_wav(path: Path, x: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def ffmpeg(*args):
    subprocess.run([str(FFMPEG()), "-hide_banner", "-v", "error", "-y", *map(str, args)], check=True)


VOICES = ["da_DK-talesyntese-medium", "sv_SE-lisa-medium", "sv_SE-alma-medium"]


def fetch_voices(dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for voice in VOICES:
        for mf in engine.voice_files(voice):
            path = dest / mf.filename
            if path.is_file() and path.stat().st_size > 0:
                continue
            req = urllib.request.Request(mf.url, headers={"User-Agent": "SubaDubaLingo-CI"})
            with urllib.request.urlopen(req, timeout=120) as r:
                path.with_name(path.name + ".part").write_bytes(r.read())
            path.with_name(path.name + ".part").replace(path)
            print(f"fetched {path.name} ({path.stat().st_size // 1_000_000} MB)")


def main():
    if sys.argv[1] == "--fetch-voices":
        return fetch_voices(Path(sys.argv[2]))
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sdl-clip-"))
    try:
        track, used = speech(work)
        wav = work / "speech.wav"
        write_wav(wav, track)
        clip = out / f"{NAME}.mp4"
        # Dark grey like the real meeting recording (test_burnin looks for its box and logo against that), with a
        # moving testsrc2 in the middle so stills and thumbnails differ; nothing in the corners or the text bands
        ffmpeg("-f", "lavfi", "-i", f"color=c=0x3a3f45:size=1280x720:rate=16:duration={LENGTH_S}",
               "-f", "lavfi", "-i", f"testsrc2=size=480x270:rate=16:duration={LENGTH_S}", "-i", wav,
               "-filter_complex", "[0:v][1:v]overlay=400:180[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264",
               "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
               "-shortest", clip)
        i18n.set_language("da")   # Danish file names, as the scripts expect ("- danske undertekster.srt")
        for code, texts in {"da": [t for _, _, t in CUES], **TRANSLATED}.items():
            srt = "\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{t}\n"
                            for i, ((a, b, _), t) in enumerate(zip(CUES, texts), 1))
            srt_name(clip, LANG_BY_CODE[code]).write_text(srt, encoding="utf-8")
        # 45 s for the cutting test: a tone that changes pitch every 5 s
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=45", "-f", "lavfi", "-i",
               "aevalsrc=0.3*sin(2*PI*(300+100*floor(t/5))*t):s=16000:d=45", "-c:v", "libx264", "-preset",
               "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out / f"{NAME} 45s.mp4")
        print(f"speech: {used}")
        print(clip)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
