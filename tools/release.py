"""Releases from McHoense (Linux), since 2026-10-20. Replaces tools\\release.ps1 + publish.ps1 on Kasper's PC; the
Windows check that release.ps1 did on his own install now happens in the Windows test VM (~/winvm, docs/release.md).

    python3 tools/release.py prepare [--run-id N]
        Finds the newest green release-build on main (or run N), downloads its zip to dist-release/, and stops when
        the version is not newer than the latest release. Prints the zip path for the VM check.
    python3 tools/release.py publish --zip "dist-release/run-N/SubaDubaLingo X.Y.Z.zip" --notes "..." [--yes]
        Writes latest.json (version, zip, size, sha256, notes), signs it with tools/release_key.py's key, checks it
        the way the app does (update.manifest_ok), and prints the gh release create command. Only with --yes does it
        run that command: Claude passes --yes only after Kasper said yes in the chat for this version.

The zip is uploaded as SubaDubaLingo-<version>.zip (GitHub turns spaces in asset names into dots, and the app takes
the zip name from the manifest) and again as SubaDubaLingo.zip for the website's download button.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))
from subadubalingo import update  # noqa: E402

NAME = "SubaDubaLingo"
OUT = ROOT / "dist-release"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args, "--repo", update.REPO], check=True, capture_output=True,
                          text=True).stdout.strip()


def vkey(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.lstrip("v").split("."))


def latest_released() -> str | None:
    try:
        return gh("release", "view", "--json", "tagName", "--jq", ".tagName").lstrip("v") or None
    except subprocess.CalledProcessError:
        return None


def prepare(run_id: int | None) -> Path:
    if run_id is None:
        rows = json.loads(gh("run", "list", "--workflow", "release-build.yml", "--branch", "main", "--status",
                             "success", "--limit", "1", "--json", "databaseId"))
        if not rows:
            sys.exit("No green release-build on main. Start one: gh workflow run release-build.yml")
        run_id = rows[0]["databaseId"]
    dest = OUT / f"run-{run_id}"
    if not any(dest.glob(f"{NAME} *.zip")):
        dest.mkdir(parents=True, exist_ok=True)
        gh("run", "download", str(run_id), "--name", "release-zip", "--dir", str(dest))
    info = json.loads((dest / "build-info.json").read_text(encoding="utf-8"))
    version, zip_path = info["version"], dest / f"{NAME} {info['version']}.zip"
    if not zip_path.exists():
        sys.exit(f"{zip_path} is missing from run {run_id}")
    have = latest_released()
    print(f"{NAME} {version} from run {run_id} (commit {info['sha'][:7]}); latest release: {have or 'none'}")
    if have and vkey(version) <= vkey(have):
        sys.exit(f"{version} is not newer than the latest release {have}: bump APP_VERSION first.")
    print(f"zip: {zip_path}")
    return zip_path


def publish(zip_path: Path, notes: str, yes: bool):
    import release_key   # the key is only needed here

    info = json.loads((zip_path.parent / "build-info.json").read_text(encoding="utf-8"))
    version = info["version"]
    if zip_path.name != f"{NAME} {version}.zip":
        sys.exit(f"{zip_path} is not {NAME} {version}.zip")
    have = latest_released()
    if have and vkey(version) <= vkey(have):
        sys.exit(f"{version} is not newer than the latest release {have}")
    release = OUT / f"release-{version}"
    release.mkdir(parents=True, exist_ok=True)
    asset, stable, manifest = release / f"{NAME}-{version}.zip", release / f"{NAME}.zip", release / "latest.json"
    shutil.copyfile(zip_path, asset)
    shutil.copyfile(zip_path, stable)
    data = asset.read_bytes()
    m = {"version": version, "zip": asset.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
         "notes": notes}
    manifest.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    release_key.main(["release_key.py", "sign", str(manifest)])
    if not update.manifest_ok(json.loads(manifest.read_text(encoding="utf-8"))):
        sys.exit("The signed manifest does not verify with update.PUBLIC_KEY: wrong key on this machine?")
    print("Signed, and it verifies with update.PUBLIC_KEY.")
    notes_file = release / "notes.txt"
    notes_file.write_text(notes, encoding="utf-8")
    cmd = ["gh", "release", "create", f"v{version}", str(asset), str(stable), str(manifest), "--repo", update.REPO,
           "--target", info["sha"], "--title", f"{NAME} {version}", "--notes-file", str(notes_file)]
    if not yes:
        print("Nothing uploaded. To publish:\n  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        return
    subprocess.run(cmd, check=True)
    print(f"Published v{version}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--run-id", type=int)
    q = sub.add_parser("publish")
    q.add_argument("--zip", required=True, type=Path)
    q.add_argument("--notes", required=True)
    q.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.run_id)
    else:
        publish(a.zip.resolve(), a.notes, a.yes)


if __name__ == "__main__":
    main()
