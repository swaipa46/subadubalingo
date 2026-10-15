"""Version folders (installer.py), in a sandbox: LOCALAPPDATA, APPDATA and USERPROFILE point into a temp folder,
so the real shortcuts, Claude config and installation are not touched.

1. A flat v1.9.9 installation with Claude running its MCP server, a taskbar pin and Claude's config.
2. NEW_DIR\\Videosprog.exe --install: lands in 1.9.10\\, current.txt, shortcuts/pin/config point there, and the
   running 1.9.9 server keeps working (before v1.9.10 it broke: zlib error -3).
3. The flat files go once the server has stopped.
4. Two more versions (faked from source): a running 1.9.10 server says "restart Claude", is kept while it
   runs, and goes once it stops; the current and the previous version always stay.
5. Installing the version that is running is refused (exit 2).
  python tests/install_test.py OLD_FLAT_DIR NEW_DIR      e.g. dist-1.9.9/Videosprog dist-1.9.10/Videosprog"""
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
from subadubalingo import installer  # noqa: E402  (after the environment, so install_dir() is the sandbox)

base = installer.install_dir()
start_menu, desktop, taskbar = installer._shortcut_dirs()
for d in (start_menu, desktop, taskbar, T / "ad" / "Claude"):
    d.mkdir(parents=True, exist_ok=True)
cfg = T / "ad" / "Claude" / "claude_desktop_config.json"
cfg.write_text(json.dumps({"mcpServers": {"videosprog": {"command": str(base / "Videosprog.exe"), "args": ["--mcp"]},
                                          "other": {"command": "x"}}}), encoding="utf-8")
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


# 1. flat 1.9.9 + Claude running its server + a taskbar pin
shutil.copytree(old_dir, base, ignore=lambda d, n: ["modeller"] if Path(d) == old_dir else [])
(base / "version.txt").write_text("1.9.9", encoding="utf-8")
subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{taskbar / 'Videosprog.lnk'}');"
                f"$s.TargetPath='{base / 'Videosprog.exe'}';$s.Save()"], check=True)
flat = Server(base / "Videosprog.exe")
check(not flat.tool("status")[1], "flat 1.9.9 server answers")

# 2. install the new version while it runs
t0 = time.time()
r = subprocess.run([str(new_dir / "Videosprog.exe"), "--install"], env=env, timeout=300)
new_ver = installer._read(base / "current.txt")
print(f"--install took {time.time() - t0:.0f} s, exit {r.returncode}, current {new_ver}")
exe = base / str(new_ver) / "Videosprog.exe"
check(r.returncode == 0 and exe.is_file(), f"installed into {exe.parent.name}\\")
check((base / "Videosprog.exe").exists(), "flat files left alone while the old server runs")
text, err = flat.tool("status")
check(not err and "zlib" not in text, "old server still works after the update")
for lnk in (start_menu / "Videosprog.lnk", desktop / "Videosprog.lnk", taskbar / "Videosprog.lnk"):
    check(lnk_target(lnk).lower() == str(exe).lower(), f"{lnk.parent.name}\\{lnk.name} -> new exe")
c = json.loads(cfg.read_text(encoding="utf-8"))
check(c["mcpServers"]["videosprog"]["command"].lower() == str(exe).lower() and "other" in c["mcpServers"],
      "Claude config -> new exe, other servers kept")
flat.stop()

# 3. the flat files go once nothing runs from them
removed = installer.cleanup()
check("flat" in removed and not (base / "Videosprog.exe").exists() and not (base / "_internal").exists()
      and exe.is_file(), f"flat layout removed after the server stopped ({removed})")

# 4. two more versions, faked from source (install() copies app_dir() and names it APP_VERSION)
srv = Server(exe)
check(not srv.tool("status")[1], f"{new_ver} server answers")
fake = T / "fake"
(fake / "_internal").mkdir(parents=True)
shutil.copy2(exe, fake / "Videosprog.exe")
shutil.copy2(exe.parent / "_internal" / "base_library.zip", fake / "_internal" / "base_library.zip")
installer.app_dir = lambda: fake
installer._update_shortcuts = lambda e: None
for v in ("1.9.11", "1.9.12"):
    installer.APP_VERSION = v
    installer.install(False)
check(installer._read(base / "current.txt") == "1.9.12", "current.txt = 1.9.12")
text, err = srv.tool("status")
check(err and "Quit" in text and "1.9.12" in text, f"running {new_ver} server says restart Claude")
check((base / str(new_ver)).is_dir(), f"{new_ver} kept while its server runs")
srv.stop()
installer.cleanup()
left = sorted(d.name for d in base.iterdir() if d.is_dir())
check(left == ["1.9.11", "1.9.12"], f"after it stopped: current + previous left ({left})")

# 5. installing the version that runs is refused
shutil.rmtree(base / "1.9.12")
shutil.copytree(new_dir, base / str(new_ver), ignore=lambda d, n: ["modeller"] if Path(d) == new_dir else [])
(base / "current.txt").write_text(str(new_ver), encoding="utf-8")
srv = Server(base / str(new_ver) / "Videosprog.exe")
r = subprocess.run([str(new_dir / "Videosprog.exe"), "--install"], env=env, timeout=300)
check(r.returncode == 2 and (base / str(new_ver) / "Videosprog.exe").exists(), "same version while it runs: refused, intact")
srv.stop()

shutil.rmtree(T, ignore_errors=True)
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
