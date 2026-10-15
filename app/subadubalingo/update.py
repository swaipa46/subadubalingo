"""Self-update from GitHub Releases (docs/auto-opdatering.md). This part: where releases are, and how a manifest
is checked. latest.json is signed with Ed25519 by tools/release_key.py; the public key below is the only one the
app trusts, so a release nobody signed with Kasper's key is never installed, even if the GitHub account is taken.

    {"version": "2.0.1", "zip": "SubaDubaLingo 2.0.1.zip", "size": ..., "sha256": "...", "notes": "...",
     "skill": 3, "signature": "<hex>"}
"""
from __future__ import annotations

import json

from . import ed25519

REPO = "swaipa46/subadubalingo"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"
PUBLIC_KEY = "ce94608fd831a809f63459dbae38195b31e426694b1388faf42d6d7a1c105ffc"   # tools/release_key.py public


def manifest_bytes(manifest: dict) -> bytes:
    """The exact bytes that are signed: every field except the signature, sorted, compact."""
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_ok(manifest: dict, public_key: str = PUBLIC_KEY) -> bool:
    try:
        return bool(public_key) and ed25519.verify(bytes.fromhex(public_key), manifest_bytes(manifest),
                                                   bytes.fromhex(manifest.get("signature", "")))
    except (ValueError, TypeError):
        return False
