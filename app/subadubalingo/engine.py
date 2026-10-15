"""The work itself: transcribe, burn in subtitles, dub. Runs in a worker thread."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .common import (FFMPEG, PIPER, WHISPER, Cue, Language, Runner, ToolError, log, model_dirs)
from .i18n import t

# --- Models ----------------------------------------------------------------------

HF_WHISPER = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{f}?download=true"
HF_PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{path}/{f}?download=true"

# large-v3 heard the superbruger webinar better in 3 of 4 one-minute clips (12.10.2026); turbo is ~1.5x faster on CPU.
# key: (i18n key of the label shown in Indstillinger, file). Show the label with model_label(key).
WHISPER_MODELS = {
    "large": ("engine.model.large", "ggml-large-v3-q5_0.bin"),
    "turbo": ("engine.model.turbo", "ggml-large-v3-turbo-q5_0.bin"),
}
PIPER_PATHS = {
    "da_DK-talesyntese-medium": "da/da_DK/talesyntese/medium",
    "sv_SE-nst-medium": "sv/sv_SE/nst/medium",
    "no_NO-talesyntese-medium": "no/no_NO/talesyntese/medium",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
    "sv_SE-lisa-medium": "sv/sv_SE/lisa/medium",
    "en_US-ryan-medium": "en/en_US/ryan/medium",
    "sv_SE-alma-medium": "sv/sv_SE/alma/medium",
}
HF_PIPER_MAIN = {"sv_SE-alma-medium"}   # not in the v1.0.0 tag of piper-voices
# A deep and a light voice per language, chosen by the original speaker's pitch (measured 13.10.2026: nst ~80 Hz,
# lisa ~186 Hz, ryan ~149 Hz, lessac light). Danish and Norwegian have one voice in Piper 1.2 (no_NO-nvcc needs a
# newer Piper), so they keep it for everyone.
GENDER_VOICES = {
    "sv": {"male": "sv_SE-nst-medium", "female": "sv_SE-lisa-medium"},
    "en": {"male": "en_US-ryan-medium", "female": "en_US-lessac-medium"},
}
# All voices per language and gender, for one voice per person (measured: alma ~190 Hz; da/no talesyntese ~112 Hz).
# When there are more people than voices, the voices are reused moved up or down a few semitones.
PERSON_VOICES = {
    "sv": {"male": ["sv_SE-nst-medium"], "female": ["sv_SE-lisa-medium", "sv_SE-alma-medium"]},
    "en": {"male": ["en_US-ryan-medium"], "female": ["en_US-lessac-medium"]},
    "da": {"male": ["da_DK-talesyntese-medium"]},
    "no": {"male": ["no_NO-talesyntese-medium"]},
}
MALE_BELOW_HZ, FEMALE_ABOVE_HZ = 160.0, 175.0


@dataclass
class ModelFile:
    filename: str
    url: str
    approx_mb: int


def model_label(key: str) -> str:
    """The Whisper model's label for Indstillinger, in the UI language."""
    return t(WHISPER_MODELS[key][0])


def whisper_model_file(key: str) -> ModelFile:
    f = WHISPER_MODELS[key][1]
    return ModelFile(f, HF_WHISPER.format(f=f), 550 if key == "turbo" else 1080)


def voice_files(voice: str) -> list[ModelFile]:
    p = PIPER_PATHS[voice]
    url = HF_PIPER.replace("/v1.0.0/", "/main/") if voice in HF_PIPER_MAIN else HF_PIPER
    return [ModelFile(voice + ".onnx", url.format(path=p, f=voice + ".onnx"), 63),
            ModelFile(voice + ".onnx.json", url.format(path=p, f=voice + ".onnx.json"), 0)]


def speaker_model_file() -> ModelFile:
    from . import speakers
    return ModelFile(speakers.MODEL, speakers.MODEL_URL, 27)


def find_model(filename: str) -> Path | None:
    for d in model_dirs():
        p = d / filename
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def download(mf: ModelFile, progress: Callable[[float, str], None], runner: Runner) -> Path:
    dest_dir = model_dirs()[0]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / mf.filename
    part = dest.with_name(dest.name + ".part")
    log.info("download %s", mf.url)
    req = urllib.request.Request(mf.url, headers={"User-Agent": "SubaDubaLingo/2"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            runner.check()
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(done / total, t("engine.download", file=mf.filename, done=done >> 20, total=total >> 20))
    part.replace(dest)
    return dest

# --- Media info ------------------------------------------------------------------

@dataclass
class MediaInfo:
    duration_ms: int
    has_audio: bool


def probe(runner: Runner, video: Path) -> MediaInfo:
    _, lines = runner.run([FFMPEG(), "-hide_banner", "-i", video], quiet=True)
    dur, has_audio = 0, False
    for l in lines:
        m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", l)
        if m:
            h, mi, s, frac = m.groups()
            dur = ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])
        if re.search(r"Stream #.*Audio:", l):
            has_audio = True
    if not dur:
        raise ToolError(t("engine.error.probe"))
    return MediaInfo(dur, has_audio)

# --- Progress ----------------------------------------------------------------------

class Progress:
    """Turns step progress (0..1) into overall %, time left and a status text."""

    def __init__(self, report: Callable[[float, str, str], None], weights: list[tuple[str, float]],
                 runner: Runner | None = None):
        self.report = report
        self.runner = runner
        self.paused_at_start = 0.0
        self.weights = weights
        self.total = sum(w for _, w in weights) or 1
        self.step = -1
        self.step_started = time.time()

    def start(self, name: str):
        self.step = [n for n, _ in self.weights].index(name)
        self.step_started = time.time()
        self.paused_at_start = self.runner.paused_seconds() if self.runner else 0.0
        self.update(0.0, "")

    def update(self, frac: float, text: str):
        frac = max(0.0, min(1.0, frac))
        before = sum(w for _, w in self.weights[:self.step])
        overall = (before + self.weights[self.step][1] * frac) / self.total
        left = ""
        elapsed = time.time() - self.step_started
        if self.runner:
            elapsed -= self.runner.paused_seconds() - self.paused_at_start
        if frac > 0.03 and elapsed > 5:
            secs = elapsed / frac * (1 - frac)
            left = t("engine.progress.left_1min") if secs < 90 else t("engine.progress.left_min", min=round(secs / 60))
        label = t("engine.progress.step", n=self.step + 1, total=len(self.weights), name=self.weights[self.step][0])
        self.report(overall, label + (f" - {left}" if left else ""), text)

# --- Transcription ---------------------------------------------------------------------

def extract_audio(runner: Runner, video: Path, wav: Path):
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-i", video,
                          "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])
    if code != 0 or not wav.exists():
        raise ToolError(t("engine.error.extract_audio"))


_GPU_DEVICE: int | None = None


def gpu_device(runner: Runner) -> int:
    """The Vulkan device whisper should use: the first dedicated GPU (uma: 0), else device 0. With both an
    Intel and an NVIDIA GPU the order varies (12.10.2026 evening: Intel was device 0, and large-v3 ran at
    1.85x real time instead of ~0.1x)."""
    global _GPU_DEVICE
    if _GPU_DEVICE is None:
        _GPU_DEVICE = 0
        try:
            lines: list[str] = []   # all of them: run() only returns the last lines, and these come first
            runner.run([WHISPER(), "--help"], lines.append, quiet=True)
            for line in lines:
                m = re.match(r"ggml_vulkan: (\d+) = (.*?)\|\s*uma: (\d)", line)
                if m and m.group(3) == "0":
                    _GPU_DEVICE = int(m.group(1))
                    log.info("GPU device %s: %s", m.group(1), m.group(2).strip())
                    break
        except Exception:
            log.exception("could not list GPU devices")
    return _GPU_DEVICE


def transcribe(runner: Runner, wav: Path, lang: Language, model: Path, duration_ms: int,
               use_gpu: bool, prog: Progress, workdir: Path) -> list[Cue]:
    out_base = workdir / "whisper"
    threads = max(2, min(os.cpu_count() or 4, 16))
    device_id = gpu_device(runner) if use_gpu else 0

    def args(gpu: bool):
        # No --vad: with VAD, whisper.cpp maps segment times back to the original audio but leaves the
        # token (word) times in the shortened audio, so every subtitle drifts earlier (12.10.2026: 2 s at
        # the start of a 18 s clip). It also dropped a short phrase.
        # -mc 0: no text from the previous window as prompt. With it, large-v3 got stuck on the webinar
        # (13.10.2026: "Hvordan gør det samme med betalingen?" 1110 times, from 12:01 to the end).
        a = [WHISPER(), "-m", model, "-f", wav, "-l", lang.code, "-t", threads, "-mc", "0", "-ojf", "-of", out_base,
             "-pp"]
        if not gpu:
            a += ["-ng"]
        elif device_id:
            a += ["-dev", device_id]
        return a

    seen = [0.0]
    device = [""]

    def on_line(line: str):
        if line.startswith(f"ggml_vulkan: {device_id} =") and not device[0]:
            device[0] = line.split("=", 1)[1].split("|")[0].strip()
        m = re.search(r"progress\s*=\s*(\d+)%", line)
        if m:
            seen[0] = max(seen[0], int(m.group(1)) / 100)
            prog.update(seen[0], "")
            return
        m = re.match(r"\[(\d+):(\d+):(\d+)\.(\d+) --> [^\]]+\]\s*(.*)", line)
        if m:
            h, mi, s, ms, text = m.groups()
            t = ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(ms)
            seen[0] = max(seen[0], t / max(1, duration_ms))
            prog.update(seen[0], text)

    started = time.time()
    gpu_used = use_gpu
    code, tail = runner.run(args(use_gpu), on_line)
    if code != 0 and use_gpu:
        gpu_used = False
        log.warning("whisper with GPU failed (exit %s); retrying on CPU", code)
        prog.update(0.0, t("engine.gpu_retry"))
        seen[0] = 0.0
        started = time.time()
        code, tail = runner.run(args(False), on_line)
    if code != 0:
        raise ToolError(t("engine.error.transcribe"))
    log.info("transcribed %.1f min audio in %.1f min (gpu setting=%s, device=%s, threads=%d)",
             duration_ms / 60000, (time.time() - started) / 60, use_gpu, (device[0] if gpu_used else "") or "CPU", threads)
    words = words_from_json(Path(str(out_base) + ".json"))
    return drop_repeats(cues_from_words(words))


def drop_repeats(cues: list[Cue], limit: int = 3) -> list[Cue]:
    """Whisper can get stuck repeating one sentence. More than `limit` identical blocks in a row are a loop,
    not speech: keep the first and drop the rest (the time then has no subtitle, which is better than nonsense)."""
    out: list[Cue] = []
    run = 0
    dropped = 0
    for c in cues:
        same = out and c.text.strip().lower() == out[-1].text.strip().lower()
        run = run + 1 if same else 1
        if same and run > 1 and _in_loop(cues, c, limit):
            dropped += 1
            continue
        out.append(c)
    if dropped:
        log.warning("dropped %d repeated blocks (Whisper loop)", dropped)
    return out


def _in_loop(cues: list[Cue], c: Cue, limit: int) -> bool:
    i = cues.index(c)
    text = c.text.strip().lower()
    a = i
    while a > 0 and cues[a - 1].text.strip().lower() == text:
        a -= 1
    b = i
    while b + 1 < len(cues) and cues[b + 1].text.strip().lower() == text:
        b += 1
    return b - a + 1 > limit


def words_from_json(path: Path) -> list[tuple[int, int, str]]:
    """Word timings from whisper-cli's full JSON. Tokens may split a UTF-8 character, so work in bytes."""
    data = json.loads(path.read_bytes().decode("utf-8", "surrogateescape"))
    words: list[tuple[int, int, bytes]] = []
    for seg in data.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if text.startswith("[_") or not text:
                continue
            b = text.encode("utf-8", "surrogateescape")
            t0, t1 = tok["offsets"]["from"], tok["offsets"]["to"]
            if b.startswith(b" ") or not words:
                words.append((t0, t1, b.strip()))
            else:
                s, _, prev = words[-1]
                words[-1] = (s, t1, prev + b)
    out = [(s, e, w.decode("utf-8", "replace").strip()) for s, e, w in words]
    # Drop "words" without letters or digits (Whisper's "..." for a pause) - they would start a block early.
    return [x for x in out if any(ch.isalnum() for ch in x[2])]


def cues_from_words(words: list[tuple[int, int, str]]) -> list[Cue]:
    """Group words into subtitle blocks: end at a sentence end, a pause > 0.8 s or ~70 characters;
    at most two lines of ~42 characters; at least 1.2 s on screen."""
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        txt = " ".join(x[2] for x in cur)
        gap = words[i + 1][0] - w[1] if i + 1 < len(words) else 10 ** 9
        if txt[-1] in ".?!" or gap > 800 or len(txt) > 70:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    corrections = load_corrections()
    cues = []
    for i, g in enumerate(groups):
        t0, t1 = g[0][0], g[-1][1]
        text = apply_corrections(drop_hallucinations(" ".join(x[2] for x in g)), corrections)
        if not text:
            continue
        if not cues or cues[-1].text.rstrip()[-1:] in ".?!":
            text = text[0].upper() + text[1:]
        nxt = groups[i + 1][0][0] if i + 1 < len(groups) else t1 + 1500
        end = min(max(t1, t0 + 1200) + 300, nxt - 50)
        cues.append(Cue(t0, max(end, t0 + 300), wrap(text)))
    return cues


# Whisper learned TV subtitle credits and sometimes "hears" them in pauses or at the end.
HALLUCINATIONS = [
    re.compile(r"\b(?:danske|svenske|norske|engelske)?\s*(?:under)?tekst(?:er|et|ning)?\s+(?:af|ved|av)\b.*$", re.I),
    re.compile(r"\bScandinavian Text Service\b.*$", re.I),
    re.compile(r"\b(?:www\.|https?://)\S+", re.I),
]


def drop_hallucinations(text: str) -> str:
    for rx in HALLUCINATIONS:
        text = rx.sub("", text)
    return " ".join(text.split())


# The rules a new corrections.txt starts with (Danish words Whisper mishears: data, not UI text). The explanation
# on top of the file is in the UI language, see default_corrections().
DEFAULT_CORRECTIONS = """\
fløvet = flowet
fløve = flow
fløv = flow
Superbrugere webinar = superbrugerwebinar
Member-kærligheden = Membercare
memberkæret = Membercare
memberkær = Membercare
MemberCare = Membercare
Members Care = Membercare
IAN-nummer = EAN-nummer
AN-felter = EAN-felter
midt-ID = MitID
rebatkode = rabatkode
"""


def default_corrections() -> str:
    """A new corrections.txt: how the file works (# lines, in the UI language), then the default rules."""
    return t("engine.corrections.header").rstrip("\n") + "\n\n" + DEFAULT_CORRECTIONS


def corrections_file() -> Path:
    from .common import data_dir
    f = data_dir() / "corrections.txt"
    if not f.exists():
        try:
            f.write_text(default_corrections(), encoding="utf-8")
        except OSError:
            pass
    return f


def load_corrections() -> list[tuple[re.Pattern, str]]:
    try:
        lines = corrections_file().read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = DEFAULT_CORRECTIONS.splitlines()
    rules = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        wrong, right = (x.strip() for x in line.split("=", 1))
        if wrong:
            rules.append((wrong, right))
    # Longer phrases first, so "memberkæret" wins over "memberkær".
    rules.sort(key=lambda r: -len(r[0]))
    # A rule must end a word but may end a compound word: "fløv" fixes "tilmeldingsfløv", not "fløvet".
    return [(re.compile(re.escape(w) + r"(?!\w)", re.I), r) for w, r in rules]


def apply_corrections(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    def fix(m: re.Match, right: str) -> str:
        if right and m.group(0)[:1].isupper() and right[:1].islower():
            return right[0].upper() + right[1:]
        return right
    for rx, right in rules:
        text = rx.sub(lambda m, r=right: fix(m, r), text)
    return text


def wrap(text: str, width: int = 42) -> str:
    if len(text) <= width:
        return text
    spaces = [j for j, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text
    k = min(spaces, key=lambda j: abs(j - len(text) / 2))
    return text[:k] + "\n" + text[k + 1:]

# --- Burning in subtitles and encoding ---------------------------------------------------

SUB_STYLE = "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1.6,Shadow=0.6,MarginV=28"


def _ffmpeg_progress(prog: Progress, duration_ms: int):
    def on_line(line: str):
        m = re.match(r"out_time_us=(\d+)", line)
        if m:
            prog.update(int(m.group(1)) / 1000 / max(1, duration_ms), "")
    return on_line


def encode(runner: Runner, inputs: list, filter_complex: str | None, vf_srt: Path | None, maps: list,
           audio_args: list, out: Path, duration_ms: int, prog: Progress, workdir: Path):
    """Run ffmpeg into out (via a temp name). With vf_srt, the video is re-encoded with the subtitles
    burned in: Quick Sync first (fast on Intel laptops), then libx264."""
    tmp_out = out.with_name(out.stem + ".tmp" + out.suffix)
    common_tail = ["-movflags", "+faststart", "-t", f"{duration_ms / 1000:.3f}",
                   "-progress", "pipe:1", "-nostats", tmp_out]
    attempts = []
    if vf_srt:
        # The subtitles filter breaks on drive colons, backslashes and quotes in paths, so the file is
        # copied to the work folder and given by a relative name, with ffmpeg running there.
        shutil.copyfile(vf_srt, workdir / "s.srt")
        vf = f"subtitles=s.srt:charenc=UTF-8:force_style='{SUB_STYLE}'"
        attempts.append(["-vf", vf + ",format=nv12", "-c:v", "h264_qsv", "-preset", "faster", "-global_quality", "23"])
        attempts.append(["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"])
    else:
        attempts.append(["-c:v", "copy"])
    for n, video_args in enumerate(attempts):
        args = [FFMPEG(), "-hide_banner", "-y"]
        for i in inputs:
            args += ["-i", i]
        if filter_complex:
            args += ["-filter_complex", filter_complex]
        args += maps + video_args + audio_args + common_tail
        code, _ = runner.run(args, _ffmpeg_progress(prog, duration_ms), cwd=workdir)
        if code == 0 and tmp_out.exists():
            tmp_out.replace(out)
            return
        tmp_out.unlink(missing_ok=True)
        if n + 1 < len(attempts):
            log.info("encoder attempt %d failed, trying the next", n + 1)
            prog.update(0.0, t("engine.encode_retry"))
    raise ToolError(t("engine.error.encode", file=out.name))


AUDIO_AAC = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def burn(runner: Runner, video: Path, srt: Path, out: Path, info: MediaInfo, prog: Progress, workdir: Path):
    maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if info.has_audio else [])
    encode(runner, [video], None, srt, maps, AUDIO_AAC if info.has_audio else [], out,
           info.duration_ms, prog, workdir)

# --- Dubbing ---------------------------------------------------------------------------------

MAX_TEMPO = 1.5


def clean_tts_text(text: str) -> str:
    text = re.sub(r"<[^>]+>|\{[^}]+\}", "", text)
    return " ".join(text.split())


def run_piper(runner: Runner, voice: Path, items: list[tuple[str, Path]], length_scale: float | None,
              on_done: Callable[[int], None]):
    lines = "\n".join(json.dumps({"text": t, "output_file": str(p)}, ensure_ascii=False) for t, p in items) + "\n"
    args = [PIPER(), "--model", voice, "--config", str(voice) + ".json", "--json-input"]
    if length_scale:
        args += ["--length_scale", f"{length_scale:.3f}"]
    count = [0]

    def on_line(line: str):
        if line.lower().endswith(".wav"):
            count[0] += 1
            on_done(count[0])

    code, _ = runner.run(args, on_line, cwd=PIPER().parent, stdin_text=lines)
    if code != 0:
        raise ToolError(t("engine.error.piper"))


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data, sr


MIN_TEMPO = 0.87          # at most ~15 % slower than the voice's own pace (slower drags)
SENTENCE_GAP_MS = 700     # a pause this long in the original ends a sentence for the dub
SENTENCE_MAX_MS = 25_000
CUE_PADDING_MS = 300      # cues_from_words keeps a block on screen this long after its last word


@dataclass
class Sentence:
    start: int      # ms: where the dubbed sentence starts (its first block's start)
    length: int     # ms: how long the speaker took for it
    text: str
    blocks: int


def sentences(cues: list[Cue]) -> list[Sentence]:
    """Blocks joined into the sentences they belong to: until . ? ! or a pause in the original. A sentence is
    spoken in one piece, so the voice does not stop and wait in the middle of it when it is faster than the
    speaker (12.10.2026: "velkommen til vores ... demo, der handler om ...")."""
    out: list[Sentence] = []
    group: list[Cue] = []

    def flush():
        if group:
            text = " ".join(t for t in (clean_tts_text(c.text) for c in group) if t)
            if text:
                last_word_end = max(group[-1].end - CUE_PADDING_MS, group[-1].start + 300)
                out.append(Sentence(group[0].start, max(400, last_word_end - group[0].start), text, len(group)))
            group.clear()

    for c in cues:
        if group:
            prev = group[-1]
            ends = clean_tts_text(prev.text).rstrip("\"'”)»").endswith((".", "?", "!", "…"))
            gap = c.start - prev.end
            if ends or gap > SENTENCE_GAP_MS or c.end - group[0].start > SENTENCE_MAX_MS:
                flush()
        group.append(c)
    flush()
    return out


def speaker_genders(runner: Runner, video: Path, sents: list[Sentence], workdir: Path) -> list[str]:
    """'male' or 'female' per sentence, from the original speaker's pitch. Unclear sentences (no voiced frames,
    or a pitch between the two ranges) take the voice of the sentence before, and a short sentence alone between
    two of the other voice follows them, so the voice does not flip within one person's turn."""
    from . import pitch
    wav = workdir / "orig16k.wav"
    extract_audio(runner, video, wav)
    data, sr = read_wav(wav)
    f0 = pitch.frames_f0(data, sr)
    per = pitch.HOP_MS
    labels: list[str | None] = []
    hzs: list[float | None] = []
    for s in sents:
        hz = pitch.median_f0(f0[s.start // per:(s.start + s.length) // per])
        hzs.append(hz)
        labels.append(None if hz is None else "male" if hz < MALE_BELOW_HZ else "female" if hz > FEMALE_ABOVE_HZ else None)
    first = next((l for l in labels if l), "male")
    out, prev = [], first
    for l in labels:
        prev = l or prev
        out.append(prev)
    # One sentence of the other voice between two of the same is usually a measuring error (a raised voice, a
    # laugh), unless its pitch is clearly in the other range (13.10.2026 webinar: 3 such errors in 251 sentences).
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] != out[i]:
            hz = hzs[i] or 0
            clear = hz > FEMALE_ABOVE_HZ + 25 if out[i] == "female" else 0 < hz < MALE_BELOW_HZ - 25
            if not clear or sents[i].length < 2500:
                out[i] = out[i - 1]
    return out


def per_person_voices(runner: Runner, video: Path, sents: list[Sentence], workdir: Path, speaker_model: Path,
                      palette: dict[str, list[Path]]):
    """One voice per person: voiceprints cluster the sentences into speakers, the pitch gives each speaker's
    gender, and speakers.assign hands out the voices. Returns the voice per sentence and a summary."""
    from . import pitch, speakers
    wav = workdir / "orig16k.wav"
    if not wav.exists():
        extract_audio(runner, video, wav)
    audio, sr = read_wav(wav)
    spans = [(s.start, s.start + s.length) for s in sents]
    embs = speakers.embeddings(speaker_model, audio, sr, spans)
    f0 = pitch.frames_f0(audio, sr)
    hz = [pitch.median_f0(f0[a // pitch.HOP_MS:b // pitch.HOP_MS]) for a, b in spans]
    raw = speakers.merge_similar(speakers.cluster(embs), embs, hz)
    who = speakers.fill_and_smooth(raw, embs, [s.length for s in sents])
    talk: dict[int, int] = {}
    for s, w in zip(sents, who):
        talk[w] = talk.get(w, 0) + s.length
    genders = {}
    for w in talk:
        h = [x for x, y in zip(hz, who) if y == w and x]
        genders[w] = "female" if h and float(np.median(h)) > (MALE_BELOW_HZ + FEMALE_ABOVE_HZ) / 2 else "male"
    voice_for = speakers.assign(who, genders, talk, palette)
    summary = [(w, genders[w], talk[w], voice_for[w].key) for w in sorted(talk, key=lambda x: -talk[x])]
    log.info("dub speakers: %s", "; ".join(f"{w} {g} {t / 1000:.0f}s -> {k}" for w, g, t, k in summary))
    return [voice_for[w] for w in who], summary, who


def shift_pitch(runner: Runner, wav: Path, semitones: float):
    """Move a voice up or down without changing its length (a distinct voice from the same model)."""
    data, sr = read_wav(wav)
    f = 2 ** (semitones / 12)
    tmp = wav.with_name(wav.stem + "-p.wav")
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-v", "error", "-y", "-i", wav, "-af",
                          f"asetrate={sr * f:.0f},aresample={sr},atempo={1 / f:.5f}", tmp], quiet=True)
    if code == 0 and tmp.exists():
        tmp.replace(wav)


def dub(runner: Runner, video: Path, cues: list[Cue], voice: Path, out: Path, burn_srt: Path | None,
        keep_original: bool, info: MediaInfo, prog: Progress, workdir: Path, step_tts: str, step_video: str,
        voices: dict[str, Path] | None = None, palette: dict[str, list[Path]] | None = None,
        speaker_model: Path | None = None):
    """`voice` is the language's voice. With `palette` and `speaker_model`, every person in the video gets a voice
    of their own; with `voices` ({"male": ..., "female": ...}) each sentence gets the one that matches the
    original speaker's pitch."""
    from .speakers import Voice
    tts_dir = workdir / "tts"
    tts_dir.mkdir(exist_ok=True)
    sents = sentences(cues)
    if not sents:
        raise ToolError(t("engine.error.empty_srt"))

    prog.start(step_tts)
    total = len(sents)
    voice_of = [Voice(voice)] * total
    if speaker_model and palette and info.has_audio:
        prog.update(0.0, t("engine.dub.who_speaks"))
        voice_of, _, _ = per_person_voices(runner, video, sents, workdir, speaker_model, palette)
    elif voices and len(voices) > 1 and info.has_audio:
        prog.update(0.0, t("engine.dub.gender"))
        genders = speaker_genders(runner, video, sents, workdir)
        voice_of = [Voice(voices.get(g, voice)) for g in genders]
        log.info("dub voices: %s", {g: genders.count(g) for g in set(genders)})
    model_of = [v.model for v in voice_of]
    scale_of = {m: float(json.loads(Path(str(m) + ".json").read_text(encoding="utf-8"))
                         .get("inference", {}).get("length_scale", 1.0)) for m in set(model_of)}
    done = 0
    for v in sorted(set(model_of), key=str):
        idxs = [i for i in range(total) if model_of[i] == v]
        base = done
        run_piper(runner, v, [(sents[i].text, tts_dir / f"s{i}.wav") for i in idxs], None,
                  lambda n: prog.update((base + n) / total * 0.5, t("engine.dub.speaking", n=base + n, total=total)))
        done += len(idxs)

    # Measure each dubbed sentence against the time the speaker took, and speak it again at the pace that makes
    # it as long as the original (Piper's own length scale sounds better than stretching afterwards). Never past
    # the next sentence's start, at most MAX_TEMPO faster and MIN_TEMPO slower. Paces are rounded to steps of
    # 0.02, one Piper run per step.
    redo: dict[tuple[float, Path], list[int]] = {}
    for i, s in enumerate(sents):
        nxt = sents[i + 1].start if i + 1 < total else info.duration_ms
        target = max(400, min(s.length, nxt - s.start - 100))
        data, sr = read_wav(tts_dir / f"s{i}.wav")
        tempo = min(MAX_TEMPO, max(MIN_TEMPO, len(data) / sr * 1000 / target))
        tempo = round(tempo * 50) / 50
        if abs(tempo - 1.0) >= 0.03:
            redo.setdefault((tempo, model_of[i]), []).append(i)
    done_redo = 0
    n_redo = sum(len(v) for v in redo.values())
    log.info("dub: %d blocks -> %d sentences; %d spoken again at another pace (%s)", len(cues), total, n_redo,
             ", ".join(f"x{t:.2f} {Path(v).stem}: {len(ix)}" for (t, v), ix in sorted(redo.items(), key=str)))
    for (tempo, v), idxs in sorted(redo.items(), key=str):
        base = done_redo
        run_piper(runner, v, [(sents[i].text, tts_dir / f"s{i}.wav") for i in idxs], scale_of[v] / tempo,
                  lambda n: prog.update(0.5 + 0.5 * (base + n) / max(1, n_redo),
                                        t("engine.dub.tempo", n=base + n, total=n_redo)))
        done_redo += len(idxs)

    shifted = [i for i, v in enumerate(voice_of) if v.semitones]
    for k, i in enumerate(shifted):
        shift_pitch(runner, tts_dir / f"s{i}.wav", voice_of[i].semitones)
        prog.update(1.0, t("engine.dub.pitch", n=k + 1, total=len(shifted)))

    # Place every sentence at its first block's start (or right after the previous one) on one track.
    cfg = json.loads(Path(str(voice) + ".json").read_text(encoding="utf-8"))
    sr = int(cfg.get("audio", {}).get("sample_rate", 22050))
    track = np.zeros(int(info.duration_ms / 1000 * sr) + sr, dtype=np.float32)
    pos_end = 0
    late = 0
    for i, s in enumerate(sents):
        data, clip_sr = read_wav(tts_dir / f"s{i}.wav")
        if clip_sr != sr:
            data = np.interp(np.arange(0, len(data), clip_sr / sr), np.arange(len(data)), data).astype(np.float32)
        pos = max(int(s.start / 1000 * sr), pos_end)
        if pos > int(s.start / 1000 * sr) + sr // 2:
            late += 1
        if pos >= len(track):
            break
        data = data[:len(track) - pos]
        track[pos:pos + len(data)] += data
        pos_end = pos + len(data) + int(0.05 * sr)
    if late:
        log.info("dub: %d of %d sentences start more than 0.5 s late (the one before was too long)", late, total)
    track = np.clip(track, -1, 1)
    dub_wav = workdir / "dub.wav"
    with wave.open(str(dub_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((track * 32767).astype(np.int16).tobytes())

    prog.start(step_video)
    if keep_original and info.has_audio:
        fc = ("[0:a:0]volume=0.12[o];[1:a]aresample=48000,volume=1.0[d];"
              "[o][d]amix=inputs=2:duration=first:normalize=0,aformat=channel_layouts=stereo[a]")
    else:
        fc = "[1:a]aresample=48000,aformat=channel_layouts=stereo[a]"
    encode(runner, [video, dub_wav], fc, burn_srt, ["-map", "0:v:0", "-map", "[a]"], AUDIO_AAC, out,
           info.duration_ms, prog, workdir)

# --- Checking a translation against the original ------------------------------------------------

def compare(original: list[Cue], translated: list[Cue]) -> str | None:
    """None if the translation matches the original's blocks and time codes, else a message."""
    if not translated:
        return t("engine.compare.none")
    for n, (a, b) in enumerate(zip(original, translated), 1):
        if abs(a.start - b.start) > 1 or abs(a.end - b.end) > 1:
            return t("engine.compare.timecode", n=n, orig=f"{a.start / 1000:.1f}", got=f"{b.start / 1000:.1f}")
    if len(translated) < len(original):
        return t("engine.compare.missing", missing=len(original) - len(translated), got=len(translated),
                 total=len(original))
    if len(translated) > len(original):
        return t("engine.compare.extra", extra=len(translated) - len(original))
    for n, b in enumerate(translated, 1):
        if not b.text.strip():
            return t("engine.compare.empty", n=n)
    return None
