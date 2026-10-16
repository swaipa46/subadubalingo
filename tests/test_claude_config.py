"""Claude desktop's config is only rewritten while Claude desktop is not running (it writes its in-memory copy back
when it saves its preferences, which undid the 2.1.0 install on 2026-10-16). Sandbox: APPDATA points into a temp
folder; the running processes are faked.
  python tests/test_claude_config.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-claudecfg-"))
os.environ["APPDATA"] = str(T / "Roaming")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import installer  # noqa: E402

failed = 0


def check(ok, what):
    global failed
    print(("OK  " if ok else "FAIL") + " " + what)
    failed += not ok


cfg = T / "Roaming" / "Claude" / "claude_desktop_config.json"
cfg.parent.mkdir(parents=True)
old_exe = r"C:\Users\x\AppData\Local\Programs\Videosprog\Videosprog.exe"
new_exe = Path(r"C:\Users\x\AppData\Local\Programs\SubaDubaLingo\2.1.1\SubaDubaLingo.exe")


def write(servers):
    cfg.write_text(json.dumps({"mcpServers": servers, "preferences": {"theme": "dark"}}), encoding="utf-8")


desktop = [r"c:\program files\windowsapps\claude_2.37337.1.0_x64__pzs8sxrjxfjjc\app\claude.exe"]
code_cli = [r"c:\users\x\.local\bin\claude.exe"]
check(installer.claude_desktop_running(desktop), "Claude desktop (MSIX) counts as running")
check(installer.claude_desktop_running([r"c:\program files\anthropicclaude\claude.exe"]), "Claude desktop (classic) counts")
check(not installer.claude_desktop_running(code_cli), "Claude Code's claude.exe does not count")

write({"videosprog": {"command": old_exe, "args": ["--mcp"]}, "other": {"command": "x"}})
check(installer.claude_config_state(new_exe) == "stale", "old name -> stale")

# Claude desktop runs: nothing is written now; a thread waits and writes once it has quit
running = {"v": True}
installer._process_images = lambda: desktop if running["v"] else code_cli
asked = []
th = installer.keep_claude_pointed(new_exe, on_waiting=lambda: asked.append(1), poll_s=0.1)
check(th is not None and asked == [1], "while Claude runs: waits and asks the user to quit Claude")
check("videosprog" in json.loads(cfg.read_text())["mcpServers"], "nothing written while Claude runs")
running["v"] = False
th.join(timeout=10)
data = json.loads(cfg.read_text())
check(data["mcpServers"].get("subadubalingo", {}).get("command") == str(new_exe), "after Claude quit: points at the new exe")
check("videosprog" not in data["mcpServers"] and "other" in data["mcpServers"], "old entry gone, others kept")
check(data.get("preferences") == {"theme": "dark"}, "Claude's preferences kept")
check(installer.claude_config_state(new_exe) == "ok", "state ok afterwards")

# Not running: fixed at once; ok config: nothing happens; not set up: nothing added
write({"subadubalingo": {"command": "C:\\old\\2.1.0\\SubaDubaLingo.exe", "args": ["--mcp"]}})
check(installer.keep_claude_pointed(new_exe) is None and installer.claude_config_state(new_exe) == "ok",
      "Claude not running: fixed at once")
before = cfg.read_text()
installer.keep_claude_pointed(new_exe)
check(cfg.read_text() == before, "already ok: untouched")
write({"other": {"command": "x"}})
installer.keep_claude_pointed(new_exe)
check("subadubalingo" not in json.loads(cfg.read_text())["mcpServers"], "not set up for the app: nothing added")

print("all ok" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
