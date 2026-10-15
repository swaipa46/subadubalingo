"""The self-update (update.py) against a local HTTP server that serves fake releases signed with a throwaway key made
here (update.PUBLIC_KEY is patched to it): a good update is staged next to the current version without switching,
then switched; a bad signature, a manifest signed with another key, a bad hash or size, an unsafe zip, a network
error and an older version are all refused or ignored and leave nothing behind. Runs in a sandbox: LOCALAPPDATA,
APPDATA and USERPROFILE point into a temp folder, so the real installation, settings and Claude config are untouched.
  python tests/test_autoupdate.py"""
import functools
import hashlib
import http.server
import io
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-autoupdate-"))
for k, sub in (("LOCALAPPDATA", "la"), ("APPDATA", "ad"), ("USERPROFILE", "home")):
    os.environ[k] = str(T / sub)
    (T / sub).mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import autoupdate, ed25519, installer, update  # noqa: E402  (after the environment)

ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


assert str(update.updates_dir()).startswith(str(T)), update.updates_dir()   # never the real data folder
shortcut_calls = []
installer._update_shortcuts = lambda exe: shortcut_calls.append(exe)   # no PowerShell, no real shortcuts

# The throwaway signing key, and one that is not trusted
SECRET, OTHER = secrets.token_bytes(32), secrets.token_bytes(32)
update.PUBLIC_KEY = ed25519.public_key(SECRET).hex()

# The current installation: 2.0.0 in its version folder, Claude's entry pointing at it
base = installer.install_dir()
cur_exe = base / "2.0.0" / "SubaDubaLingo.exe"
(cur_exe.parent / "_internal").mkdir(parents=True)
cur_exe.write_bytes(b"old exe")
(cur_exe.parent / "_internal" / "base_library.zip").write_bytes(b"old lib")
(cur_exe.parent / "version.txt").write_text("2.0.0", encoding="utf-8")
(base / "current.txt").write_text("2.0.0", encoding="utf-8")
cfg = T / "ad" / "Claude" / "claude_desktop_config.json"
cfg.parent.mkdir()
cfg.write_text(json.dumps({"mcpServers": {"subadubalingo": {"command": str(cur_exe), "args": ["--mcp"]},
                                          "other": {"command": "x"}}}), encoding="utf-8")

# The web server, serving one folder per case
www = T / "www"
www.mkdir()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(www)))
threading.Thread(target=server.serve_forever, daemon=True).start()
HOST = f"http://127.0.0.1:{server.server_address[1]}"


def build_zip(version: str, backslashes=False, extra: dict | None = None) -> bytes:
    """A release zip as build.ps1 makes it: the folder SubaDubaLingo\\ with the exe, _internal and models."""
    buf = io.BytesIO()
    sep = "\\" if backslashes else "/"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"SubaDubaLingo{sep}SubaDubaLingo.exe", f"new exe {version}")
        z.writestr(f"SubaDubaLingo{sep}_internal{sep}base_library.zip", b"lib" * 1000)
        z.writestr(f"SubaDubaLingo{sep}How to use SubaDubaLingo.txt", "guide")
        z.writestr(f"SubaDubaLingo{sep}models{sep}", "")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def publish(case: str, version: str, zip_bytes: bytes | None = None, key=SECRET, tamper=None, **override) -> str:
    """Serve a release under /<case>/ and return its manifest URL."""
    d = www / case
    d.mkdir()
    zip_bytes = build_zip(version) if zip_bytes is None else zip_bytes
    name = f"SubaDubaLingo-{version}.zip"
    (d / name).write_bytes(zip_bytes)
    m = {"version": version, "zip": name, "size": len(zip_bytes), "sha256": hashlib.sha256(zip_bytes).hexdigest(),
         "notes": "Testnoter æøå", "skill": 3}
    m.update(override)
    m["signature"] = ed25519.sign(key, update.manifest_bytes(m)).hex()
    if tamper:
        tamper(m)
    (d / update.MANIFEST_NAME).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return f"{HOST}/{case}/{update.MANIFEST_NAME}"


def clean_updates():
    shutil.rmtree(update.updates_dir(), ignore_errors=True)


def nothing_left(version: str) -> bool:
    leftovers = list(update.updates_dir().glob("*")) if update.updates_dir().exists() else []
    return (not (base / version).exists() and not (base / f"{version}.new").exists() and not leftovers
            and (base / "current.txt").read_text(encoding="utf-8") == "2.0.0")


# 1. Bad signature: a field changed after signing
url = publish("badsig", "2.1.0", tamper=lambda m: m.update(notes="changed"))
status, info = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"bad signature refused, nothing staged ({status})")

# 2. Signed with a key the app does not trust
url = publish("otherkey", "2.1.0", key=OTHER)
status, _ = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"other key refused ({status})")

# 3. Bad hash (signed, but the zip on the server is not the one signed for)
url = publish("badhash", "2.1.0", sha256="00" * 32)
status, _ = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"bad SHA-256 refused, no partial file ({status})")

# 4. Wrong size
url = publish("badsize", "2.1.0", size=12)
status, _ = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"wrong size refused ({status})")

# 5. An unsafe zip (a name outside the folder), properly signed
evil = build_zip("2.1.0", extra={"../../evil.txt": "x"})
url = publish("evil", "2.1.0", zip_bytes=evil)
status, _ = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0") and not list(T.rglob("evil.txt")),
      f"zip with ../ refused, nothing written outside ({status})")
clean_updates()

# 6. A zip without the program in it
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("readme.txt", "no exe")
url = publish("noexe", "2.1.0", zip_bytes=buf.getvalue())
status, _ = update.run(url, "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"zip without the exe refused ({status})")
clean_updates()

# 7. Network errors: nothing listening, a 404, a manifest that is not JSON
closed = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
port = closed.server_address[1]
closed.server_close()
for what, u, want in (("connection refused", f"http://127.0.0.1:{port}/latest.json", "offline"),
                      ("404", f"{HOST}/nothing/latest.json", "offline")):
    status, _ = update.run(u, "2.0.0")
    check(status == want and nothing_left("2.1.0"), f"network error ({what}) is quiet: {status}")
(www / "garbage").mkdir()
(www / "garbage" / "latest.json").write_text("<html>", encoding="utf-8")
status, _ = update.run(f"{HOST}/garbage/latest.json", "2.0.0")
check(status == "refused" and nothing_left("2.1.0"), f"a manifest that is not JSON refused ({status})")

# 8. Older and same version: nothing to do
for v in ("1.9.10", "2.0.0"):
    url = publish(f"old-{v}", v)
    status, _ = update.run(url, "2.0.0")
    check(status == "none" and update.ready("2.0.0") is None, f"{v} vs 2.0.0: no update ({status})")

# 9. A good update (zip made by Windows PowerShell's Compress-Archive: backslashes)
url = publish("good", "2.1.0", zip_bytes=build_zip("2.1.0", backslashes=True))
waited = []
status, info = update.run(url, "2.0.0", wait_idle=lambda: waited.append(1))
new_exe = base / "2.1.0" / "SubaDubaLingo.exe"
check(status == "ready" and info and info["version"] == "2.1.0" and info["notes"] == "Testnoter æøå",
      f"good update staged ({status}, {info})")
check(waited == [1], "the download waited for the app to be idle first")
check(new_exe.read_text() == "new exe 2.1.0" and (base / "2.1.0" / "_internal" / "base_library.zip").is_file()
      and (base / "2.1.0" / "version.txt").read_text(encoding="utf-8") == "2.1.0"
      and not (base / "2.1.0" / "models").exists(), "unpacked into Programs\\SubaDubaLingo\\2.1.0 (models skipped)")
check((base / "current.txt").read_text(encoding="utf-8") == "2.0.0" and cur_exe.read_bytes() == b"old exe",
      "not switched yet: current.txt still 2.0.0, the old folder untouched")
check(not list(update.updates_dir().glob("*.zip")) and not list(update.updates_dir().glob("*.unpacked")),
      "the zip and the unpacked copy are gone")
check(update.ready("2.0.0")["version"] == "2.1.0", "ready() remembers it (also after a restart without switching)")
status, info = update.run(url, "2.0.0", wait_idle=lambda: waited.append(2))
check(status == "ready" and waited == [1], "the next check finds it staged and downloads nothing")
installer.cleanup()
check(new_exe.is_file() and cur_exe.is_file(), "cleanup at start keeps the staged version and the current one")

# 10. Restart: switch to it
exe = update.switch("2.1.0")
check(exe == new_exe and (base / "current.txt").read_text(encoding="utf-8") == "2.1.0", "switched: current.txt 2.1.0")
servers = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
check(servers["subadubalingo"]["command"] == str(new_exe) and "other" in servers, "Claude's entry -> the new exe")
check(shortcut_calls == [new_exe], "shortcuts pointed at the new exe")
check(cur_exe.is_file(), "the previous version stays (to go back, and for a running MCP server)")
check(update.ready("2.1.0") is None and update.ready("2.0.0") is None, "nothing waiting any more")

# 11. When to check
day = autoupdate.DAY
check(autoupdate.should_check(True, True, 0, 1e9, None), "installed, never checked: check")
check(not autoupdate.should_check(True, True, 1e9 - day / 2, 1e9, None), "checked 12 hours ago: wait")
check(autoupdate.should_check(True, True, 1e9 - day - 1, 1e9, None), "checked over a day ago: check")
check(not autoupdate.should_check(False, True, 0, 1e9, None), "setting off: never")
check(not autoupdate.should_check(True, False, 0, 1e9, None), "run from source/zip: never")
check(autoupdate.should_check(True, False, 0, 1e9, "http://x"), "a test URL: check")
check(autoupdate._updated_from(["x", "--updated-from", "2.0.0"]) == "2.0.0" and autoupdate._updated_from(["x"]) is None,
      "--updated-from is read")

# 12. Settings files from before this feature still load, and get the defaults
os.environ["USERPROFILE"] = str(T / "home")
from subadubalingo import gui  # noqa: E402
gui.Settings.path.write_text('{"theme": "night", "ui_language": "sv"}', encoding="utf-8")
s = gui.Settings()
check(s.check_updates is True and s.update_checked == 0.0 and s.theme == "night", "old settings file loads")

server.shutdown()
shutil.rmtree(T, ignore_errors=True)
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
