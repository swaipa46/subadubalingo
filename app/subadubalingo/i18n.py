"""The app's own language (Programmets sprog): Danish, Swedish, Norwegian or English, picked by the user in the header,
on the first start or in Indstillinger. Not to be confused with the languages of a video (common.Language), which are
picked per video.

Texts live in assets/locales/<code>/<area>.json as flat keys "<area>.<name>" (gui.json holds "gui.*" and so on), one
file per module so they can be edited apart. t("gui.start") gives the text in the current language, falling back to
English and then Danish, and fills in {placeholders} from its keyword arguments. Switching the language rebuilds the
window (App.set_language), like switching the theme, so no widget has to retranslate itself.
"""
from __future__ import annotations

import ctypes
import json
import string

from .common import log

CODES = ("da", "sv", "no", "en")
# Each language's name in itself, for the picker (never translated: a Swede looks for "Svenska")
NATIVE = {"da": "Dansk", "sv": "Svenska", "no": "Norsk", "en": "English"}
FALLBACK = ("en", "da")

_current = "da"
_texts: dict[str, dict[str, str]] = {}


def _load(code: str) -> dict[str, str]:
    if code not in _texts:
        from .theme import assets_dir
        out: dict[str, str] = {}
        for f in sorted((assets_dir() / "locales" / code).glob("*.json")):
            try:
                out.update(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                log.exception("could not read %s", f)
        _texts[code] = out
    return _texts[code]


def windows_language() -> str:
    """The Windows display language as one of CODES (English when it is none of the four)."""
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    except Exception:
        return "en"
    return {0x06: "da", 0x1D: "sv", 0x14: "no"}.get(primary, "en")


def resolve(choice: str | None) -> str:
    """The code to use for a saved setting (empty before the user has picked one: follow Windows)."""
    return choice if choice in CODES else windows_language()


def set_language(code: str):
    global _current
    _current = code if code in CODES else "en"


def current() -> str:
    return _current


class _Keep(dict):
    """format_map that leaves unknown {placeholders} as they are rather than failing."""

    def __missing__(self, key):
        return "{" + key + "}"


def t(key: str, lang: str | None = None, **kw) -> str:
    """The text for key in lang (default: the current language). Unknown keys are logged and shown as the key."""
    for code in (lang or _current,) + FALLBACK:
        text = _load(code).get(key)
        if text is not None:
            break
    else:
        log.warning("missing text %s", key)
        return key
    if kw:
        try:
            return string.Formatter().vformat(text, (), _Keep(kw))
        except (ValueError, IndexError):
            log.exception("bad text %s", key)
    return text


def has(key: str, lang: str | None = None) -> bool:
    return key in _load(lang or _current)


def all_texts(key: str, **kw) -> list[str]:
    """key in every language (for recognising files named in another language than the current one)."""
    return list(dict.fromkeys(t(key, lang=c, **kw) for c in CODES))
