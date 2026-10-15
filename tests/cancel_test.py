"""MCP 'cancel' and the outdated-server check, acting as Claude.

1. A job left in claude_turn by a closed app is stopped directly (no app to ask).
2. A live job: start_video, wait for Claude's turn, cancel -> the app stops it, and a new job is not 'busy'.
3. The exe the server runs from is replaced -> every call says to restart Claude (no zlib error).
  python tests/cancel_test.py VIDEO [--stale JOB_ID] [--exe COPY_OF_SubaDubaLingo.exe]
Step 3 needs --exe: a copy of the built folder the test may touch (it rewrites the exe's timestamp)."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
video = sys.argv[1]


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


class Server:
    def __init__(self, cmd):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=str(root))
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
        text = r["content"][0]["text"]
        print(f"--- {name} {args} -> {'ERROR ' if r.get('isError') else ''}{text[:300]}")
        return text, bool(r.get("isError"))

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def check(cond, what):
    print(("OK   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


src = Server([sys.executable, str(root / "app" / "main.py"), "--mcp"])
jobs_dir = Path.home() / ".subadubalingo" / "jobs"

stale = arg("--stale")
if stale:
    text, _ = src.tool("cancel", job_id=stale)
    st = json.loads((jobs_dir / stale / "status.json").read_text(encoding="utf-8"))
    check(st["state"] == "cancelled" and "was not running" in text, "stale job stopped without the app")

text, _ = src.tool("start_video", video=video, speech="swedish", subtitles="swedish", relisten=True)
m = re.search(r"\d{8}-\d{6}-[0-9a-f]{4}", text)
job = m and m.group(0)
for _ in range(60):
    text, _ = src.tool("wait", job_id=job) if job else src.tool("wait")
    if "din tur" in text.lower() or "claude_turn" in text or "Teksten er klar" in text:
        break
st_files = sorted(jobs_dir.glob("*/status.json"), key=lambda f: f.stat().st_mtime)
job = st_files[-1].parent.name
check(json.loads(st_files[-1].read_text(encoding="utf-8"))["state"] == "claude_turn", f"job {job} reached Claude's turn")
text, _ = src.tool("get_text", job_id=job, skill=4)
check("Task for job" in text, "get_text works")
text, _ = src.tool("cancel")
st = json.loads((jobs_dir / job / "status.json").read_text(encoding="utf-8"))
check(st["state"] == "cancelled", "live job stopped by the app")
text, _ = src.tool("cancel", job_id=job)
check("is not running" in text, "cancel on a stopped job says so")
src.close()

exe = arg("--exe")
if exe:
    srv = Server([exe, "--mcp"])
    text, err = srv.tool("status")
    check(not err, "built server answers before the update")
    st = os.stat(exe)
    os.utime(exe, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # as if an update replaced it
    text, err = srv.tool("get_text", job_id=job, skill=4)
    check(err and "Quit" in text and "zlib" not in text, "outdated server says to restart Claude")
    srv.close()
print("all ok")
