"""The release signing key (Ed25519). The private key never leaves this pc unencrypted: it is stored with
Windows DPAPI (only this Windows user can decrypt it) in %USERPROFILE%\\.videosprog-release\\signing-key.dpapi.

    python tools/release_key.py new          make the key; prints the public key for app/subadubalingo/update.py and writes a
                                             one-time backup file to move into a password manager
    python tools/release_key.py public       print the public key
    python tools/release_key.py restore      read the 64 hex characters from the backup (typed, not echoed)
    python tools/release_key.py sign FILE    sign a manifest (JSON): adds "signature" over the other fields

The app checks the signature with app/subadubalingo/ed25519.py; manifest_bytes() is the exact text that is signed."""
from __future__ import annotations

import ctypes
import getpass
import json
import secrets
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import ed25519  # noqa: E402
from subadubalingo.update import manifest_bytes  # noqa: E402

KEY_DIR = Path.home() / ".videosprog-release"
KEY_FILE = KEY_DIR / "signing-key.dpapi"
BACKUP_FILE = KEY_DIR / "BACKUP-signing-key.txt"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), "Videosprog release key" if protect else None, None, None, None, 0,
            ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _save(secret: bytes):
    KEY_DIR.mkdir(exist_ok=True)
    KEY_FILE.write_bytes(_dpapi(secret, True))


def load_secret() -> bytes:
    if not KEY_FILE.exists():
        sys.exit(f"No key: {KEY_FILE}. Run 'new' or 'restore'.")
    return _dpapi(KEY_FILE.read_bytes(), False)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "new":
        if KEY_FILE.exists():
            sys.exit(f"A key exists already: {KEY_FILE}. A new one would lock out every installed copy.")
        secret = secrets.token_bytes(32)
        _save(secret)
        BACKUP_FILE.write_text(
            "Videosprog release signing key (Ed25519, private). Put this in your password manager, then delete "
            "this file.\nWithout it, a new pc cannot publish updates the installed copies accept.\n\n"
            f"{secret.hex()}\n", encoding="utf-8")
        print(f"Key made and stored with DPAPI: {KEY_FILE}")
        print(f"Backup (move into a password manager, then delete): {BACKUP_FILE}")
        print(f"Public key: {ed25519.public_key(secret).hex()}")
    elif cmd == "public":
        print(ed25519.public_key(load_secret()).hex())
    elif cmd == "restore":
        if KEY_FILE.exists():
            sys.exit(f"A key exists already: {KEY_FILE}")
        secret = bytes.fromhex(getpass.getpass("64 hex characters from the backup: ").strip())
        if len(secret) != 32:
            sys.exit("That is not 64 hex characters.")
        _save(secret)
        print(f"Restored. Public key: {ed25519.public_key(secret).hex()}")
    elif cmd == "sign" and len(argv) > 2:
        path = Path(argv[2])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["signature"] = ed25519.sign(load_secret(), manifest_bytes(manifest)).hex()
        path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Signed {path}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
