"""What the program knows about the subtitle files it made: for which video (size and modification time),
with which version of the transcription, and whether Claude has checked the text.

A text file is reused only when it was made for this exact video by the current transcription; otherwise it is
made again. That catches a video that was cut and exported again under the same name, and texts made by a
version with a known timing error (before v1.3.2, VAD shifted every subtitle).
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import data_dir, log

# Bump when a change to the transcription makes earlier texts wrong (1 = before v1.3.2, VAD timing error;
# 2 = before v1.9.1, Whisper could loop on one sentence).
TEXT_VERSION = 3


def _file() -> Path:
    return data_dir() / "tekster.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict):
    try:
        _file().write_text(json.dumps(st, indent=1), encoding="utf-8")
    except OSError:
        log.exception("could not save text state")


def _key(p: Path) -> str:
    return str(p.resolve()).lower()


def fingerprint(video: Path) -> list:
    s = video.stat()
    return [s.st_size, int(s.st_mtime)]


def record(srt: Path, video: Path):
    """Call after transcribing `video` into `srt`."""
    st = _load()
    st[_key(srt)] = {"video": fingerprint(video), "version": TEXT_VERSION, "checked": False}
    _save(st)


def status(srt: Path, video: Path) -> str:
    """'missing', 'ok', 'changed' (video differs from when the text was made) or 'old' (older transcription)."""
    if not srt.exists():
        return "missing"
    entry = _load().get(_key(srt))
    if not entry or entry.get("version", 0) < TEXT_VERSION:
        return "old"
    try:
        if entry.get("video") != fingerprint(video):
            return "changed"
    except OSError:
        return "changed"
    return "ok"


def is_checked(srt: Path) -> bool:
    return bool(_load().get(_key(srt), {}).get("checked"))


def set_checked(srt: Path, checked: bool):
    st = _load()
    entry = st.get(_key(srt))
    if entry is None:
        return
    entry["checked"] = checked
    _save(st)


# --- Cut videos ----------------------------------------------------------------------------
# "<video> - klippet.mp4" is reused for more language versions while the original is unchanged and the cut file
# is the one the program wrote (the user may have deleted or replaced it).

def _cut_key(edited: Path) -> str:
    return "klip:" + _key(edited)


def record_cut(edited: Path, video: Path, ranges: list):
    st = _load()
    st[_cut_key(edited)] = {"source": fingerprint(video), "video": fingerprint(edited), "ranges": [list(r) for r in ranges]}
    _save(st)


def cut_status(edited: Path, video: Path) -> str:
    """'missing', 'ok' (made from this video by the program) or 'changed'."""
    if not edited.exists():
        return "missing"
    entry = _load().get(_cut_key(edited))
    try:
        if not entry or entry.get("source") != fingerprint(video) or entry.get("video") != fingerprint(edited):
            return "changed"
    except OSError:
        return "changed"
    return "ok"


def cut_ranges(edited: Path) -> list:
    return (_load().get(_cut_key(edited)) or {}).get("ranges", [])
