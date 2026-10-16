"""Self-update from GitHub Releases (docs/auto-opdatering.md). latest.json is signed with Ed25519 by
tools/release_key.py; the public key below is the only one the app trusts, so a release nobody signed with Kasper's
key is never installed, even if the GitHub account is taken.

    {"version": "2.0.1", "zip": "SubaDubaLingo-2.0.1.zip", "size": ..., "sha256": "...", "notes": "...",
     "skill": 3, "signature": "<hex>"}

The steps, each usable on its own (tests/test_autoupdate.py runs them against a local server):
    check()     fetch the manifest, verify the signature, and return it when its version is newer than this one
    download()  fetch the zip next to the manifest into data_dir()/updates, checking its size and SHA-256
    stage()     unpack it and copy it into its own version folder next to the current one (installer.place),
                without switching: Claude's MCP server and the running app keep their folder
    ready()     a staged version waiting for the restart (also after the app was closed without restarting)
    switch()    make it the current one (installer.activate: current.txt, shortcuts, Claude's entry) and start it
The GUI side (when to check, the banner) is autoupdate.py.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import ed25519
from .common import APP_NAME, APP_VERSION, data_dir, log

REPO = "swaipa46/subadubalingo"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"
MANIFEST_NAME = "latest.json"
PUBLIC_KEY = "ce94608fd831a809f63459dbae38195b31e426694b1388faf42d6d7a1c105ffc"   # tools/release_key.py public
TIMEOUT = 5            # seconds for the manifest (docs/auto-opdatering.md: quiet when offline)
ZIP_TIMEOUT = 30       # seconds without data before a zip download gives up
MAX_MANIFEST = 64 * 1024
READY_FILE = "ready.json"


class UpdateError(Exception):
    """A release that must not be installed (bad signature, hash or zip). Network trouble is OSError."""


def manifest_bytes(manifest: dict) -> bytes:
    """The exact bytes that are signed: every field except the signature, sorted, compact."""
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_ok(manifest: dict, public_key: str | None = None) -> bool:
    key = PUBLIC_KEY if public_key is None else public_key   # read at call time, so a test can patch PUBLIC_KEY
    try:
        return bool(key) and ed25519.verify(bytes.fromhex(key), manifest_bytes(manifest),
                                            bytes.fromhex(manifest.get("signature", "")))
    except (ValueError, TypeError, AttributeError):
        return False


def vtuple(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def updates_dir() -> Path:
    return data_dir() / "updates"


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_manifest(url: str = MANIFEST_URL) -> dict:
    """The manifest as published. Raises OSError on network trouble (offline, 404, timeout)."""
    with _get(url, TIMEOUT) as r:
        raw = r.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise UpdateError("manifest too large")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError(f"manifest is not JSON: {e}") from None
    if not isinstance(manifest, dict):
        raise UpdateError("manifest is not an object")
    return manifest


def _valid(manifest: dict) -> bool:
    """The fields stage() relies on, in the shape publish.ps1 writes them."""
    try:
        name = manifest["zip"]
        return (isinstance(name, str) and name.endswith(".zip") and "/" not in name and "\\" not in name
                and ".." not in name and vtuple(manifest["version"]) > (0,)
                and all(x.isdigit() for x in str(manifest["version"]).split("."))
                and len(bytes.fromhex(manifest["sha256"])) == 32 and int(manifest["size"]) > 0)
    except (KeyError, ValueError, TypeError):
        return False


def check(url: str = MANIFEST_URL, current: str = APP_VERSION) -> dict | None:
    """The signed manifest of the latest release when it is newer than `current`, else None. Raises OSError on
    network trouble and UpdateError on a manifest that is not signed with PUBLIC_KEY or malformed."""
    manifest = fetch_manifest(url)
    if not manifest_ok(manifest):
        raise UpdateError("bad signature")
    if not _valid(manifest):
        raise UpdateError("malformed manifest")
    if vtuple(manifest["version"]) <= vtuple(current):
        return None
    manifest["url"] = urllib.parse.urljoin(url, urllib.parse.quote(manifest["zip"]))   # next to the manifest
    return manifest


def download(manifest: dict, dest_dir: Path | None = None, progress=None) -> Path:
    """Fetch the release zip into dest_dir (default updates_dir()) and check its size and SHA-256 against the
    signed manifest. A bad file is deleted and raises UpdateError; network trouble raises OSError."""
    dest_dir = dest_dir or updates_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / manifest["zip"]
    part = dest.with_name(dest.name + ".part")
    size, want = int(manifest["size"]), manifest["sha256"].lower()
    h, got = hashlib.sha256(), 0
    try:
        with _get(manifest["url"], ZIP_TIMEOUT) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:
                    raise UpdateError(f"zip larger than the manifest says ({size} bytes)")
                h.update(chunk)
                f.write(chunk)
                if progress:
                    progress(got, size)
        if got != size:
            raise UpdateError(f"zip is {got} bytes, the manifest says {size}")
        if h.hexdigest() != want:
            raise UpdateError("zip SHA-256 does not match the manifest")
        part.replace(dest)
        return dest
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _extract(zip_path: Path, into: Path):
    """Unpack, refusing names that would land outside `into` (absolute, drive letters, ..). Compress-Archive in
    Windows PowerShell writes backslashes, so both separators count."""
    root = into.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ":" in name or ".." in parts:
                raise UpdateError(f"unsafe name in the zip: {info.filename!r}")
            target = root.joinpath(*parts)
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


def stage(zip_path: Path, manifest: dict) -> Path:
    """Unpack a downloaded release and copy it into its own version folder next to the current one, without
    switching (installer.place; the current folder is not touched). Records it in ready.json for the banner.
    Returns the new version folder."""
    from . import installer
    version = str(manifest["version"])
    work = zip_path.parent / f"{version}.unpacked"
    shutil.rmtree(work, ignore_errors=True)
    try:
        try:
            _extract(zip_path, work)
        except zipfile.BadZipFile as e:
            raise UpdateError(f"not a zip: {e}") from None
        # build.ps1 zips the folder SubaDubaLingo\ itself; accept the files at the top as well
        src = next((d for d in [work] + sorted(p for p in work.iterdir() if p.is_dir())
                    if (d / installer.EXE_NAME).is_file()), None)
        if not src or not (src / "_internal" / "base_library.zip").is_file():
            raise UpdateError(f"no {installer.EXE_NAME} with _internal in the zip")
        dst = installer.place(src, version)
    finally:   # a refused zip is not kept either: the next check downloads it again
        shutil.rmtree(work, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
    info = {k: manifest.get(k) for k in ("version", "notes", "skill")}
    (updates_dir() / READY_FILE).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    log.info("update %s staged in %s", version, dst)
    return dst


def ready(current: str = APP_VERSION) -> dict | None:
    """The staged update waiting for a restart ({"version", "notes", ...}), if its folder is complete and newer
    than `current`."""
    from . import installer
    try:
        info = json.loads((updates_dir() / READY_FILE).read_text(encoding="utf-8"))
        version = str(info["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    folder = installer.install_dir() / version
    if vtuple(version) <= vtuple(current) or not (folder / installer.EXE_NAME).is_file() \
            or installer._read(folder / "version.txt") != version:
        return None
    return info


def run(url: str = MANIFEST_URL, current: str = APP_VERSION, wait_idle=None) -> tuple[str, dict | None]:
    """One whole check: ("ready", info) when an update is staged (now or earlier), ("none", None) when this is the
    newest, ("offline", None) on network trouble, ("refused", None) on a release that failed a check. Never raises.
    wait_idle, if given, is called before the download (it blocks while a job runs)."""
    have = ready(current)
    if have:
        return "ready", have
    try:
        manifest = check(url, current)
        if not manifest:
            log.info("update: %s is the newest", current)
            return "none", None
        log.info("update: %s found, downloading %s", manifest["version"], manifest["url"])
        if wait_idle:
            wait_idle()
        stage(download(manifest), manifest)
        return "ready", ready(current)
    except UpdateError as e:
        log.warning("update refused: %s", e)
        return "refused", None
    except (OSError, ValueError) as e:   # urllib's errors are OSError; ValueError: a bad URL
        log.info("update check failed (quietly): %s", e)
        return "offline", None
    except Exception:
        log.exception("update failed")
        return "refused", None


def switch(version: str) -> Path:
    """Make the staged version the current one (current.txt, shortcuts, Claude's entry: installer.activate) and
    return its exe. Claude's running MCP server keeps its old folder and says "restart Claude"."""
    from . import installer
    exe = installer.activate(version, connect_claude=False)
    (updates_dir() / READY_FILE).unlink(missing_ok=True)
    return exe


def restart(exe: Path, old_version: str = APP_VERSION):
    """Start the new exe (it says "Opdateret til ..."); the caller closes this app right after."""
    subprocess.Popen([str(exe), "--updated-from", old_version], close_fds=True)
