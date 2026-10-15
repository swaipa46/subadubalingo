"""Installing SubaDubaLingo over Videosprog, in a sandbox: LOCALAPPDATA, APPDATA and USERPROFILE point into a temp
folder, so the real shortcuts, Claude config, data and installation are not touched.

1. Videosprog 1.9.10 installed the usual way (its own --install): Programs\\Videosprog\\1.9.10, Start and desktop
   shortcuts, a taskbar pin, Claude's "videosprog" entry, data in ~/.videosprog (settings, rettelser.txt, a model).
   Claude runs its MCP server, which keeps ~/.videosprog/log.txt open.
2. NEW_DIR\\SubaDubaLingo.exe --install while that server runs: lands in Programs\\SubaDubaLingo\\2.0.0; shortcuts,
   the pin and Claude's config point there (the old entry gone, others kept); the old Start/desktop shortcuts are
   gone; the old server says "restart Claude"; the old folder stays while it runs; the data is in
   ~/.subadubalingo under the English names (copied, as the old folder was in use).
3. The server stops: cleanup() removes Programs\\Videosprog, and the models move over.
4. Two more versions (faked from source): the current and the previous one stay, older ones go once unused, and
   installing the version that is running is refused.
5. A fresh profile with only ~/.videosprog and nothing running: the folder is renamed as a whole.
  python tests/install_test.py OLD_DIR NEW_DIR      e.g. dist-1.9.10/Videosprog dist-2.0.0/SubaDubaLingo"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
old_dir, new_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
T = Path(tempfile.mkdtemp(prefix="sdl-install-"))
env = dict(os.environ, LOCALAPPDATA=str(T / "la"), APPDATA=str(T / "ad"), USERPROFILE=str(T / "home"))
os.environ.update({k: env[k] for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE")})
sys.path.insert(0, str(root / "app"))
from subadubalingo import common, installer  # noqa: E402  (after the environment, so the paths are the sandbox)

home = T / "home"
base, old_base = installer.install_dir(), installer.old_install_dirs()[0]
start_menu, desktop, taskbar = installer._shortcut_dirs()
for d in (start_menu, desktop, taskbar, T / "ad" / "Claude", home / ".videosprog" / "modeller"):
    d.mkdir(parents=True, exist_ok=True)
cfg = T / "ad" / "Claude" / "claude_desktop_config.json"
cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
(home / ".videosprog" / "settings.json").write_text('{"quality": "best"}', encoding="utf-8")
(home / ".videosprog" / "rettelser.txt").write_text("memberkær = Membercare\nkasper-test = Test\n", encoding="utf-8")
(home / ".videosprog" / "modeller" / "fake-model.bin").write_bytes(b"x" * 1000)
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def lnk_target(lnk: Path) -> str:
    ps = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}').TargetPath"
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()


class Server:
    def __init__(self, exe: Path):
        self.p = subprocess.Popen([str(exe), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.rid = 0
        self.call("initialize", {"protocolVersion": "2025-06-18"})

    def call(self, method, params=None):
        self.rid += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.rid, "method": method, "params": params or {}}) + "\n").encode())
        self.p.stdin.flush()
        while True:
            msg = json.loads(self.p.stdout.readline())
            if msg.get("id") == self.rid:
                return msg

    def tool(self, name, **args):
        r = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], bool(r.get("isError"))

    def stop(self):
        self.p.stdin.close()
        self.p.wait(10)


# 1. Videosprog 1.9.10, installed by itself, with Claude running its server and a taskbar pin
r = subprocess.run([str(old_dir / "Videosprog.exe"), "--install", "--connect"], env=env, timeout=300)
old_exe = old_base / "1.9.10" / "Videosprog.exe"
check(r.returncode == 0 and old_exe.is_file(), "Videosprog 1.9.10 installed in the sandbox")
subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{taskbar / 'Videosprog.lnk'}');"
                f"$s.TargetPath='{old_exe}';$s.Save()"], check=True)
old = Server(old_exe)
check(not old.tool("status")[1], "the old server answers")

# 2. SubaDubaLingo 2.0.0 over it, while the old server runs
r = subprocess.run([str(new_dir / "SubaDubaLingo.exe"), "--install"], env=env, timeout=300)
new_ver = installer._read(base / "current.txt")
exe = base / str(new_ver) / "SubaDubaLingo.exe"
check(r.returncode == 0 and exe.is_file(), f"installed into Programs\\SubaDubaLingo\\{new_ver}")
for lnk in (start_menu / "SubaDubaLingo.lnk", desktop / "SubaDubaLingo.lnk", taskbar / "Videosprog.lnk"):
    check(lnk_target(lnk).lower() == str(exe).lower(), f"{lnk.parent.name}\\{lnk.name} -> new exe")
check(not (start_menu / "Videosprog.lnk").exists() and not (desktop / "Videosprog.lnk").exists(),
      "old Start/desktop shortcuts removed")
servers = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
check(servers.get("subadubalingo", {}).get("command", "").lower() == str(exe).lower() and "videosprog" not in servers
      and "other" in servers, f"Claude config: subadubalingo -> new exe, videosprog gone, others kept ({sorted(servers)})")
text, err = old.tool("status")
check(err and "Quit" in text, "the old server says restart Claude")
check(old_exe.is_file(), "the old folder stays while its server runs")
d = home / ".subadubalingo"
check((d / "settings.json").read_text(encoding="utf-8") == '{"quality": "best"}'
      and "kasper-test = Test" in (d / "corrections.txt").read_text(encoding="utf-8"),
      "settings and the word list are in ~/.subadubalingo (corrections.txt)")
old.stop()

# 3. cleanup once nothing runs from the old folder; the models follow
removed = installer.cleanup()
check(not old_base.exists() and "Videosprog" in removed, f"Programs\\Videosprog removed ({removed})")
common._DATA_DIR = None
moved = common.adopt_previous_models()
check("fake-model.bin" in moved and (d / "models" / "fake-model.bin").is_file(), "models moved to ~/.subadubalingo/models")

# 4. later versions, faked from source (install() copies app_dir() and names it APP_VERSION)
srv = Server(exe)
check(not srv.tool("status")[1], f"{new_ver} server answers")
fake = T / "fake"
(fake / "_internal").mkdir(parents=True)
shutil.copy2(exe, fake / "SubaDubaLingo.exe")
shutil.copy2(exe.parent / "_internal" / "base_library.zip", fake / "_internal" / "base_library.zip")
installer.app_dir = lambda: fake
installer._update_shortcuts = lambda e: None
for v in ("2.0.1", "2.0.2"):
    installer.APP_VERSION = v
    installer.install(False)
text, err = srv.tool("status")
check(err and "Quit" in text and "2.0.2" in text, f"running {new_ver} server says restart Claude")
check((base / str(new_ver)).is_dir(), f"{new_ver} kept while its server runs")
srv.stop()
installer.cleanup()
left = sorted(p.name for p in base.iterdir() if p.is_dir())
check(left == ["2.0.1", "2.0.2"], f"after it stopped: current + previous left ({left})")
shutil.copytree(new_dir, base / str(new_ver), ignore=lambda d_, n: ["models"] if Path(d_) == new_dir else [])
(base / "current.txt").write_text(str(new_ver), encoding="utf-8")
srv = Server(base / str(new_ver) / "SubaDubaLingo.exe")
r = subprocess.run([str(new_dir / "SubaDubaLingo.exe"), "--install"], env=env, timeout=300)
check(r.returncode == 2 and (base / str(new_ver) / "SubaDubaLingo.exe").exists(), "same version while it runs: refused, intact")
srv.stop()

# 5. a fresh profile, nothing running: ~/.videosprog is renamed as a whole
home2 = T / "home2"
(home2 / ".videosprog" / "modeller").mkdir(parents=True)
(home2 / ".videosprog" / "rettelser.txt").write_text("a = b\n", encoding="utf-8")
(home2 / ".videosprog" / "tekster.json").write_text("{}", encoding="utf-8")
(home2 / ".videosprog" / "modeller" / "m.bin").write_bytes(b"m")
code = ("import sys; sys.path.insert(0, r'%s'); from subadubalingo import common; print(common.data_dir())" % (root / "app"))
r = subprocess.run([sys.executable, "-c", code], env=dict(env, USERPROFILE=str(home2)), capture_output=True, text=True)
d2 = home2 / ".subadubalingo"
check(not (home2 / ".videosprog").exists() and (d2 / "corrections.txt").is_file() and (d2 / "text_state.json").is_file()
      and (d2 / "models" / "m.bin").is_file(), f"~/.videosprog renamed whole, files under English names ({r.stdout.strip()})")

time.sleep(0.5)
shutil.rmtree(T, ignore_errors=True)
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
