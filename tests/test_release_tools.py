"""tools/release_key.py and tools/release.py (releases from McHoense, 2.2.0): a manifest signed by release_key verifies
with the matching public key the way the app checks it, and fails with another key; release.py compares versions as
numbers; on Linux the key file is written with mode 600 and refused when others can read it. The key lives in a temp
HOME, never the real one.
  python tests/test_release_tools.py"""
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="sdl-test-release-")
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
import release  # noqa: E402
import release_key  # noqa: E402
from subadubalingo import ed25519, update  # noqa: E402

ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


check(str(release_key.KEY_DIR).startswith(os.environ["HOME"]), "the key folder is in the temp HOME")
secret = secrets.token_bytes(32)
release_key.load_secret = lambda: secret
manifest = Path(os.environ["HOME"]) / "latest.json"
manifest.write_text(json.dumps({"version": "9.9.9", "zip": "SubaDubaLingo-9.9.9.zip", "size": 3,
                                "sha256": "0" * 64, "notes": "æøå"}), encoding="utf-8")
release_key.main(["release_key.py", "sign", str(manifest)])
signed = json.loads(manifest.read_text(encoding="utf-8"))
check(update.manifest_ok(signed, ed25519.public_key(secret).hex()), "signed manifest verifies with its public key")
check(not update.manifest_ok(signed, ed25519.public_key(secrets.token_bytes(32)).hex()),
      "and not with another key")
signed["notes"] = "changed"
check(not update.manifest_ok(signed, ed25519.public_key(secret).hex()), "a changed field breaks the signature")

check(release.vkey("2.10.0") > release.vkey("2.2.0") > release.vkey("v2.1.1"), "versions compare as numbers")

if not release_key.WINDOWS:
    import importlib
    importlib.reload(release_key)   # the real load_secret again
    release_key._save(secret)
    check(oct(release_key.KEY_FILE.stat().st_mode & 0o777) == "0o600", "Linux: the key file is mode 600")
    check(release_key.load_secret() == secret, "Linux: it reads back")
    release_key.KEY_FILE.chmod(0o644)
    try:
        release_key.load_secret()
        check(False, "Linux: a key readable by others is refused")
    except SystemExit:
        check(True, "Linux: a key readable by others is refused")

print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
