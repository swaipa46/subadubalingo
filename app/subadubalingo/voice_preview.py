"""Lyt til stemmen før dub: a small play button that plays a sample sentence in the dub voice of a language.

The sample is spoken with the same Piper call the dub uses (engine.run_piper, the voice's own length scale from its
.onnx.json, and engine.shift_pitch for a voice moved up or down), so what the user hears is what the dub sounds like
before it is fitted to the original's timing. The voice mode decides which voices:

- "one": the language's own voice.
- "gender": the deep and the light voice one after another (GENDER_VOICES; Danish and Norwegian have one voice,
  and the dub uses it for everyone, so the sample does too).
- "person": the first man's and the first woman's voice as speakers.assign hands them out (Danish: the one voice,
  then the same voice raised for a woman).

Every spoken sample is cached as a WAV in <data dir>/voice-preview, keyed by the voice, its model file, the pitch
shift, the length scale and the sentence, so the second click plays at once and it works offline once the voices
are there. Playback is winsound, asynchronous; a second click stops it.
"""
from __future__ import annotations

import hashlib
import json
import threading
import tkinter as tk
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import engine, skin
from .common import Language, Runner, data_dir, log
from .i18n import t
from .theme import IMG, P

try:
    import winsound
except ImportError:   # not on Windows: no sound
    winsound = None

CACHE_VERSION = 1      # count up when the way a sample is made changes, so old cached WAVs are not played
GAP_MS = 350           # silence between two voices
MODES = ("person", "gender", "one")


@dataclass(frozen=True)
class SampleVoice:
    """One voice in a sample: a Piper voice (file stem) and a pitch shift after synthesis, as speakers.Voice."""
    stem: str
    semitones: float = 0.0


def sample_text(lang: Language) -> str:
    """The sample sentence, in the language itself (voice.sample in that language's own locale file)."""
    return t("voice.sample", lang.code)


def sample_voices(lang: Language, mode: str) -> list[SampleVoice]:
    """The voices the dub would use for this language and voice mode, as gui.App._validate picks them: one, or a
    man's and a woman's voice."""
    from . import speakers
    mode = mode if mode in MODES else "person"
    if mode == "gender" and lang.code in engine.GENDER_VOICES:
        g = engine.GENDER_VOICES[lang.code]
        return [SampleVoice(g["male"]), SampleVoice(g["female"])]
    if mode == "person" and lang.code in engine.PERSON_VOICES:
        palette = {g: [Path(v) for v in vs] for g, vs in engine.PERSON_VOICES[lang.code].items()}
        got = speakers.assign([0, 1], {0: "male", 1: "female"}, {0: 2, 1: 1}, palette)
        return [SampleVoice(got[s].model.name, got[s].semitones) for s in (0, 1)]
    return [SampleVoice(lang.voice)]


def needed_files(voices: list[SampleVoice]) -> list[engine.ModelFile]:
    """The model files missing for these voices (an empty list when they are all there)."""
    out = []
    for stem in dict.fromkeys(v.stem for v in voices):
        out += [m for m in engine.voice_files(stem) if not engine.find_model(m.filename)]
    return out


def cache_dir() -> Path:
    return data_dir() / "voice-preview"


def cache_key(voice: SampleVoice, text: str, length_scale: float | None, model_size: int = 0) -> str:
    """The cached WAV's file name for one voice speaking text. The model's size is in it, so a voice downloaded
    again in another version is spoken again."""
    raw = json.dumps([CACHE_VERSION, voice.stem, model_size, round(voice.semitones, 2),
                      None if length_scale is None else round(length_scale, 3), text], ensure_ascii=False)
    return f"{voice.stem}{voice.semitones:+.1f}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}.wav"


def pair_key(parts: list[str]) -> str:
    """The cached WAV for several voices one after another, from their own cache names."""
    return "pair-" + hashlib.sha1("|".join(parts + [str(GAP_MS)]).encode()).hexdigest()[:12] + ".wav"


def speak(runner: Runner, model: Path, voice: SampleVoice, text: str, dest: Path,
          length_scale: float | None = None):
    """Speak text with the dub's Piper call into dest (written next to it first, so a half file is never cached)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + "-part.wav")
    tmp.unlink(missing_ok=True)
    engine.run_piper(runner, model, [(engine.clean_tts_text(text), tmp)], length_scale, lambda n: None)
    if not tmp.exists():
        raise engine.ToolError(t("engine.error.piper"))
    if voice.semitones:
        engine.shift_pitch(runner, tmp, voice.semitones)
    tmp.replace(dest)


def _read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return data, sr


def join(wavs: list[Path], dest: Path):
    """Several mono 16-bit WAVs one after another with a short silence between, at the first one's rate."""
    parts, rate = [], None
    for i, p in enumerate(wavs):
        data, sr = _read(p)
        if rate is None:
            rate = sr
        elif sr != rate:   # Piper's medium voices are all 22050 Hz; resample anyway if one is not
            x = np.arange(int(len(data) * rate / sr)) * sr / rate
            data = np.interp(x, np.arange(len(data)), data).astype(np.int16)
        if i:
            parts.append(np.zeros(int(rate * GAP_MS / 1000), dtype=np.int16))
        parts.append(data)
    tmp = dest.with_name(dest.stem + "-part.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate or 22050)
        w.writeframes(np.concatenate(parts).tobytes())
    tmp.replace(dest)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / max(1, w.getframerate())


def make_sample(runner: Runner, lang: Language, mode: str) -> Path:
    """The sample WAV for this language and voice mode, from the cache or spoken now (blocks: call from a worker).
    The voices' model files must be there (needed_files)."""
    text = sample_text(lang)
    folder = cache_dir()
    files = []
    for v in sample_voices(lang, mode):
        model = engine.find_model(v.stem + ".onnx")
        if model is None:
            raise engine.ToolError(t("voice.missing"))
        dest = folder / cache_key(v, text, None, model.stat().st_size)
        if not dest.exists():
            log.info("voice preview: speaking %s %+.1f (%s)", v.stem, v.semitones, lang.code)
            speak(runner, model, v, text, dest)
        files.append(dest)
    if len(files) == 1:
        return files[0]
    dest = folder / pair_key([f.name for f in files])
    if not dest.exists():
        join(files, dest)
    return dest


def stop_sound():
    if winsound is not None:
        winsound.PlaySound(None, 0)


def play_sound(path: Path):
    if winsound is not None:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class PreviewButton(skin.IconButton):
    """The play button: the play icon and a short text (label(), "Lyt"), "Stop" with the pause icon while the sample
    is made or plays. lang() is the language to sample (the caller hides the button when there is none), mode() the
    voice mode, and get_models(missing) is asked to fetch missing voices (True when they are there now).
    Click: speak (in a worker) and play; click again: stop."""

    def __init__(self, parent, lang: Callable[[], Language | None], mode: Callable[[], str],
                 get_models: Callable[[list[engine.ModelFile]], bool] | None = None,
                 label: Callable[[], str] = lambda: t("voice.listen_short")):
        style = ttk_style(parent)
        self.label = label
        super().__init__(parent, "play", text=label(), style=style, command=self._click)
        self.lang, self.mode, self.get_models = lang, mode, get_models
        self.state_name = "idle"
        self._show("idle")
        self.state_name = "idle"         # idle, making (speaking the sample), playing
        self._token = 0                  # counts up on every start and stop, so a late worker or timer does nothing
        self._result: tuple | None = None
        self._runner: Runner | None = None
        from .pickers import Tooltip
        Tooltip(self, self._tip)
        self.bind("<Destroy>", lambda e: self.stop() if e.widget is self else None, add="+")

    def _tip(self) -> str:
        if self.state_name != "idle":
            return t("voice.stop")
        l = self.lang()
        return t("voice.listen", singular=l.singular) if l else t("voice.listen_none")

    def _show(self, state: str):
        self.state_name = state
        self.icon = "play" if state == "idle" else "pause"
        idle, stop = self.label(), t("voice.stop")
        # The same width in both states, so nothing next to it moves (ttk width is in average characters)
        self.configure(text=idle if state == "idle" else stop, width=max(len(idle), len(stop)) + 1)
        self._set_icon()

    def _click(self):
        if self.state_name != "idle":
            self.stop()
            return
        l = self.lang()
        if l is None:
            return
        mode = self.mode()
        voices = sample_voices(l, mode)
        missing = needed_files(voices)
        if missing and not (self.get_models and self.get_models(missing)):
            return
        if needed_files(voices):
            return
        self._token += 1
        token, runner = self._token, Runner()
        self._runner, self._result = runner, None
        self._show("making")

        def work():
            try:
                self._result = (token, make_sample(runner, l, mode), None)
            except Exception as e:   # noqa: BLE001 - shown in an error box by _poll
                log.exception("voice preview failed")
                self._result = (token, None, e)
        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll, token)

    def _poll(self, token: int):
        if token != self._token:
            return
        if self._result is None:
            self.after(100, self._poll, token)
            return
        _, wav, err = self._result
        if err is not None or wav is None:
            self._show("idle")
            if not (self._runner and self._runner.cancelled.is_set()):
                from .gui import boxes
                boxes.showerror(t("voice.title"), t("voice.failed"), parent=self.winfo_toplevel())
            return
        play_sound(wav)
        self._show("playing")
        self.after(int(wav_seconds(wav) * 1000) + 150, self._ended, token)

    def _ended(self, token: int):
        if token == self._token:
            self._show("idle")

    def stop(self):
        """Stop speaking or playing (a change of language or voice mode, the view closing)."""
        if self.state_name == "idle":
            return
        self._token += 1
        if self._runner:
            self._runner.cancel()
        if self.state_name == "playing":
            stop_sound()
        try:
            self._show("idle")
        except tk.TclError:   # being destroyed
            pass


def ttk_style(parent) -> str:
    """A secondary button a little lower than the others, so it sits in a row of comboboxes without making it
    taller. Configured when a button is made, so it follows the theme (the window is rebuilt on a theme switch)."""
    from tkinter import ttk
    style = ttk.Style(parent)
    k = 2 if IMG.two else 1
    for name, bg in (("Voice.TButton", P.window), ("Card.Voice.TButton", P.card), ("YT.Voice.TButton", P.your_turn_card)):
        style.configure(name, background=bg, padding=(10 * k, 5 * k, 13 * k, 8 * k))
    return "Voice.TButton"
