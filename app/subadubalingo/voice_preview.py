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
import time
import tkinter as tk
import wave
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable

import numpy as np

from . import engine, skin
from .common import Language, Runner, data_dir, log
from .i18n import t
from .theme import IMG, TERMINAL_FONT, P

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




def voice_name(stem: str) -> str:
    """"lisa" for sv_SE-lisa-medium: the name the voice has in Piper."""
    parts = stem.split("-")
    return parts[1] if len(parts) > 2 else stem


def voices_text(lang: Language, mode: str) -> str:
    """The voices a sample plays, for a tooltip or a row in Indstillinger: "nst (mand) og lisa (kvinde)"."""
    voices = sample_voices(lang, mode)
    if len(voices) == 1:
        return voice_name(voices[0].stem)
    named = [f"{voice_name(v.stem)} ({t('voice.' + g)})" for v, g in zip(voices, ("male", "female"))]
    return t("voice.and", a=named[0], b=named[1])


def levels(path: Path, n: int = 12) -> list[float]:
    """The loudness of n equal parts of a WAV, 0..1 (the bars in the sample bubble)."""
    data, _ = _read(path)
    if not len(data):
        return [0.0] * n
    parts = np.array_split(data.astype(np.float32), n)
    rms = np.array([np.sqrt(np.mean(p ** 2)) if len(p) else 0.0 for p in parts])
    top = rms.max() or 1.0
    return [float(x / top) for x in rms]


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


class SampleBubble(ttk.Frame):
    """While a sample plays: 12 level bars (the played part in heading), the sentence in italics between » «, and
    "0:02 / 0:04" in VT323 (assets/ui/README.md, "Voice preview"). In the secondary button's frame, without hover."""

    BARS = 12

    def __init__(self, parent):
        k = 2 if IMG.two else 1
        style = ttk.Style(parent)
        layout = [(f"Btn2.border-{P.name}", {"sticky": "nsew", "children": [("Frame.padding", {"sticky": "nsew"})]})]
        for name, bg in (("VoiceBubble.TFrame", P.window), ("Card.VoiceBubble.TFrame", P.card)):
            style.layout(name, layout)
            style.configure(name, background=bg, padding=(12 * k, 8 * k, 15 * k, 11 * k))
        # The padding goes to the frame itself (as the header pickers do): the style's alone lets the content cover
        # the border
        super().__init__(parent, style="Card.VoiceBubble.TFrame", padding=(12 * k, 8 * k, 15 * k, 11 * k))
        self.k = k
        inner = P.button_secondary_bg
        self.bars = tk.Canvas(self, width=(self.BARS * 6 - 2) * k, height=20 * k, highlightthickness=0,
                              borderwidth=0, background=inner)
        self.bars.pack(side="left")
        self.text = tk.Label(self, text="", font=("Segoe UI", -14 * k, "italic"), foreground=P.text,
                             background=inner, borderwidth=0)
        self.text.pack(side="left", padx=(10 * k, 0))
        self.time = tk.Label(self, text="", font=(TERMINAL_FONT, -18 * k), foreground=P.hint,
                             background=inner, borderwidth=0)
        self.time.pack(side="right", padx=(18 * k, 0))
        self._levels: list[float] = [0.0] * self.BARS
        self._total = 0.0
        self._start = 0.0
        self._token = 0

    def start(self, wav: Path, sentence: str):
        self._levels, self._total = levels(wav, self.BARS), wav_seconds(wav)
        self._start = time.monotonic()
        self.text.configure(text=f"»{sentence}«")
        self._token += 1
        self._tick(self._token)

    def stop(self):
        self._token += 1

    def _tick(self, token: int):
        if token != self._token:
            return
        done = min(1.0, (time.monotonic() - self._start) / max(0.1, self._total))
        self.draw(done)
        if done < 1.0:
            self.after(100, self._tick, token)

    def draw(self, done: float):
        """The bars and the time with done (0..1) of the sample played."""
        k, c = self.k, self.bars
        c.delete("all")
        h = int(c.cget("height"))
        played = int(round(done * self.BARS))
        for i, lv in enumerate(self._levels):
            bh = max(4 * k, int(round((0.2 + 0.8 * lv) * h / (2 * k))) * 2 * k)   # even heights on the pixel grid
            x, y = i * 6 * k, (h - bh) // 2
            c.create_rectangle(x, y, x + 4 * k, y + bh, outline="",
                               fill=P.heading if i < played else P.step_pending_icon)
        self.time.configure(text=f"{fmt_time(done * self._total)} / {fmt_time(self._total + 0.999)}")


class PreviewButton(skin.IconButton):
    """The square play button (voice-play; stop while the sample is made or plays). lang() is the language to sample
    (the caller hides the button when there is none), mode() the voice mode, get_models(missing) is asked to fetch
    missing voices (True when they are there now), and show_bubble(bubble or None) places the sample bubble (made in
    bubble_parent) while the sample plays. Click: speak (in a worker) and play; click again: stop."""

    active: "PreviewButton | None" = None   # the button whose sample is being made or played

    def __init__(self, parent, lang: Callable[[], Language | None], mode: Callable[[], str],
                 get_models: Callable[[list[engine.ModelFile]], bool] | None = None,
                 bubble_parent=None, show_bubble: Callable[[SampleBubble | None], None] | None = None):
        super().__init__(parent, "voice-play", text="", style=ttk_style(parent), command=self._click)
        self.lang, self.mode, self.get_models = lang, mode, get_models
        self.state_name = "idle"         # idle, making (speaking the sample), playing
        self._token = 0                  # counts up on every start and stop, so a late worker or timer does nothing
        self._result: tuple | None = None
        self._runner: Runner | None = None
        self.bubble = SampleBubble(bubble_parent) if bubble_parent is not None and show_bubble else None
        self.show_bubble = show_bubble
        from .pickers import Tooltip
        Tooltip(self, self._tip)
        self.bind("<Destroy>", lambda e: self.stop() if e.widget is self else None, add="+")

    def _tip(self) -> str:
        if self.state_name != "idle":
            return t("voice.stop")
        l = self.lang()
        return t("voice.listen", singular=l.singular, voices=voices_text(l, self.mode())) if l else ""

    def _show(self, state: str):
        self.state_name = state
        self.icon = "voice-play" if state == "idle" else "stop"
        self._set_icon()
        if self.bubble is not None and state != "playing":
            self.bubble.stop()
            self.show_bubble(None)

    def _click(self):
        if self.state_name != "idle":
            self.stop()
            return
        if PreviewButton.active not in (None, self):   # one sample at a time (winsound plays one sound)
            PreviewButton.active.stop()
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
        PreviewButton.active = self
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
        self.after(100, self._poll, token, l)

    def _poll(self, token: int, lang: Language):
        if token != self._token:
            return
        if self._result is None:
            self.after(100, self._poll, token, lang)
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
        if self.bubble is not None:
            self.bubble.start(wav, sample_text(lang))
            self.show_bubble(self.bubble)
        self.after(int(wav_seconds(wav) * 1000) + 150, self._ended, token)

    def _ended(self, token: int):
        if token == self._token:
            if PreviewButton.active is self:
                PreviewButton.active = None
            self._show("idle")

    def stop(self):
        """Stop speaking or playing (a change of language or voice mode, the view closing)."""
        if self.state_name == "idle":
            return
        if PreviewButton.active is self:
            PreviewButton.active = None
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
    """The square 34 x 34 secondary button with the 16 px icon centred (the frame's shadow takes 3 px right and
    below). Configured when a button is made, so it follows the theme (the window is rebuilt on a theme switch)."""
    style = ttk.Style(parent)
    k = 2 if IMG.two else 1
    for name, bg in (("Voice.TButton", P.window), ("Card.Voice.TButton", P.card), ("YT.Voice.TButton", P.your_turn_card)):
        style.configure(name, background=bg, padding=(7 * k, 7 * k, 11 * k, 11 * k), width=0)
    return "Voice.TButton"


def settings_rows(parent, mode_var: tk.StringVar,
                  get_models: Callable[[list[engine.ModelFile]], bool] | None = None) -> list[PreviewButton]:
    """Indstillinger, under the voice modes: "Tryk på (play) for at høre ...", a dashed line, then each dub language
    with the voices the chosen mode uses and a play button, two languages a row so the dialog does not grow much.
    While a sample plays, the bubble takes the hint line's place. The radio buttons get no play buttons (a mode is
    not a voice). Returns the buttons."""
    from .common import LANGUAGES
    k = 2 if IMG.two else 1
    slot = ttk.Frame(parent)            # the hint, or the sample bubble while one plays
    slot.pack(anchor="w", fill="x", pady=(8 * k, 0))
    hint = ttk.Frame(slot)
    hint.grid(row=0, column=0, sticky="w")
    ttk.Label(hint, text=t("voice.hint_before"), style="Hint.TLabel").pack(side="left")
    ttk.Label(hint, image=IMG.get("icons/voice-play"), style="Hint.TLabel").pack(side="left", padx=3 * k)
    ttk.Label(hint, text=t("voice.hint_after"), style="Hint.TLabel").pack(side="left")
    line = tk.Canvas(parent, height=2 * k, highlightthickness=0, borderwidth=0, background=P.card)
    line.pack(fill="x", pady=(6 * k, 6 * k))
    line.bind("<Configure>", lambda e: (line.delete("all"), line.create_line(
        0, 1, e.width, 1, fill=P.step_pending_icon, width=2 * k, dash=(4, 4))))
    grid = ttk.Frame(parent)
    grid.pack(anchor="w", fill="x")
    bubble = SampleBubble(slot)
    slot.update_idletasks()
    slot.rowconfigure(0, minsize=bubble.winfo_reqheight())   # the same height with the hint or the bubble

    def show(b):
        if b is None:
            bubble.grid_remove()
            hint.grid()
        else:
            hint.grid_remove()
            bubble.grid(row=0, column=0, sticky="w")

    buttons, names = [], []
    for i, lang in enumerate(LANGUAGES):
        row, col = divmod(i, 2)
        c = col * 4
        ttk.Label(grid, text=lang.name).grid(row=row, column=c, sticky="w", padx=(24 * k if col else 0, 0))
        names.append(ttk.Label(grid, text=voices_text(lang, mode_var.get())))
        names[-1].grid(row=row, column=c + 1, sticky="w", padx=(12 * k, 8 * k))
        b = PreviewButton(grid, lambda lang=lang: lang, mode_var.get, get_models)
        b.bubble, b.show_bubble = bubble, show
        b.grid(row=row, column=c + 2, sticky="w", pady=2 * k)
        buttons.append(b)

    def mode_changed(*_):
        for b in buttons:
            b.stop()
        for lang, label in zip(LANGUAGES, names):
            label.configure(text=voices_text(lang, mode_var.get()))
    mode_var.trace_add("write", mode_changed)
    return buttons
