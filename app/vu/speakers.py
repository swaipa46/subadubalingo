"""Who speaks when, for a dub with one voice per person.

Each dubbed sentence gets a voiceprint of the original speech (sherpa-onnx speaker embedding, WeSpeaker ResNet34,
CPU), the sentences are clustered into speakers (cosine similarity, average linkage), and each speaker gets a voice:
the language's voices for that speaker's gender (from the pitch), and when there are more speakers than voices,
the same voice moved up or down a few semitones, so every person still sounds different.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .common import log

MODEL = "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34_LM.onnx")
SAME_SPEAKER = 0.66      # cosine similarity (average linkage) above which sentences are one person
MIN_EMBED_MS = int(os.environ.get("VU_MIN_EMBED_MS", 3000))   # shorter sentences get the speaker of their neighbours


def _load_sherpa():
    # sherpa-onnx loads onnxruntime.dll by name; without its own folder first, Windows' older copy in System32
    # can be found and the process aborts (found in the dictaphone pipeline, 01.10.2026).
    import importlib.util
    spec = importlib.util.find_spec("sherpa_onnx")
    for base in (spec.submodule_search_locations or []) if spec else []:
        lib = Path(base) / "lib"   # the wheel ships its own onnxruntime.dll here
        if lib.is_dir():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    import sherpa_onnx
    return sherpa_onnx


def embeddings(model: Path, audio: np.ndarray, sr: int, spans: list[tuple[int, int]]) -> list[np.ndarray | None]:
    sherpa = _load_sherpa()
    ext = sherpa.SpeakerEmbeddingExtractor(sherpa.SpeakerEmbeddingExtractorConfig(
        model=str(model), num_threads=max(1, min(4, os.cpu_count() or 2))))
    out = []
    for a, b in spans:
        if b - a < MIN_EMBED_MS:
            out.append(None)
            continue
        seg = audio[int(a / 1000 * sr):int(b / 1000 * sr)].astype(np.float32)
        st = ext.create_stream()
        st.accept_waveform(sr, seg)
        st.input_finished()
        e = np.array(ext.compute(st), dtype=np.float32)
        out.append(e / (np.linalg.norm(e) + 1e-9))
    return out


def cluster(embs: list[np.ndarray | None], threshold: float = SAME_SPEAKER) -> list[int | None]:
    """Average-linkage agglomerative clustering on cosine similarity. None stays None."""
    idx = [i for i, e in enumerate(embs) if e is not None]
    if not idx:
        return [None] * len(embs)
    X = np.stack([embs[i] for i in idx])
    groups = [[k] for k in range(len(idx))]
    sim = X @ X.T
    while len(groups) > 1:
        best, pair = -2.0, None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                s = sim[np.ix_(groups[a], groups[b])].mean()
                if s > best:
                    best, pair = s, (a, b)
        if best < threshold:
            break
        a, b = pair
        groups[a] += groups[b]
        del groups[b]
    label = [None] * len(embs)
    for g, members in enumerate(groups):
        for k in members:
            label[idx[k]] = g
    return label


MERGE_SIMILAR = 0.75     # ... and clusters this similar with nearly the same pitch are one person too
MERGE_PITCH_HZ = 8.0


def merge_similar(labels: list[int | None], embs, hz: list[float | None]) -> list[int | None]:
    """One person can come out as two clusters that take turns sentence by sentence (13.10.2026 webinar: the
    presenter as two clusters at 127 and 132 Hz, centroid similarity 0.79). Merge clusters whose centroids are
    similar and whose median pitch is nearly the same; the host at 117 Hz stayed separate."""
    labels = list(labels)
    while True:
        groups = sorted({l for l in labels if l is not None})
        cent, pit = {}, {}
        for g in groups:
            E = np.stack([e for e, l in zip(embs, labels) if l == g and e is not None])
            c = E.mean(axis=0)
            cent[g] = c / (np.linalg.norm(c) + 1e-9)
            p = [h for h, l in zip(hz, labels) if l == g and h]
            pit[g] = float(np.median(p)) if p else None
        best, pair = 0.0, None
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                if pit[a] is None or pit[b] is None or abs(pit[a] - pit[b]) > MERGE_PITCH_HZ:
                    continue
                s = float(cent[a] @ cent[b])
                if s >= MERGE_SIMILAR and s > best:
                    best, pair = s, (a, b)
        if not pair:
            return labels
        a, b = pair
        labels = [a if l == b else l for l in labels]


def fill_and_smooth(labels: list[int | None], embs, lengths: list[int]) -> list[int]:
    """Short sentences take the label of the sentence before (or after); one short sentence of another speaker
    between two of the same is taken as a measuring error."""
    out = list(labels)
    first = next((l for l in out if l is not None), 0)
    prev = first
    for i, l in enumerate(out):
        if l is None:
            out[i] = prev
        prev = out[i]
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] != out[i] and lengths[i] < 2500 and labels[i] is None:
            out[i] = out[i - 1]
    return out


@dataclass
class Voice:
    model: Path
    semitones: float = 0.0   # pitch shift after synthesis (0 = the voice as it is)

    @property
    def key(self) -> str:
        return f"{self.model.stem}{self.semitones:+.1f}"


SHIFTS = [0.0, -2.5, 2.5, -4.5, 4.5]


def assign(speaker_of: list[int], genders: dict[int, str], talk_ms: dict[int, int],
           palette: dict[str, list[Path]]) -> dict[int, Voice]:
    """A distinct voice per speaker: the most talkative first. Each gender takes its own voices, then the same
    voices shifted in pitch. A gender without voices of its own borrows the other's, shifted."""
    order = sorted(talk_ms, key=lambda s: -talk_ms[s])
    used: dict[str, int] = {"male": 0, "female": 0}
    out = {}
    for s in order:
        g = genders.get(s, "male")
        models = palette.get(g) or []
        n = used[g]
        used[g] += 1
        if models:
            out[s] = Voice(models[n % len(models)], SHIFTS[(n // len(models)) % len(SHIFTS)])
        else:  # e.g. Danish: one male voice; women get it raised
            other = palette.get("male" if g == "female" else "female") or []
            base = 4.0 if g == "female" else -4.0
            out[s] = Voice(other[0], base + SHIFTS[n % len(SHIFTS)] / 2)
    return out
