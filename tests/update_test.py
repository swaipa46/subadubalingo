"""An update installed while Claude runs the MCP server: start OLD_DIR's exe as server from a copy, swap the copy's
folder for NEW_DIR the way installer.install() does (rename, then the new folder in place), and call hent_tekst.
Before v1.9.9 that gave zlib error -3; now the server must say to restart Claude.
  python tests/update_test.py OLD_DIR NEW_DIR JOB_ID [--expect zlib|restart]"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

old_dir, new_dir, job = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
expect = sys.argv[sys.argv.index("--expect") + 1] if "--expect" in sys.argv else "restart"
base = Path(tempfile.mkdtemp(prefix="vu-update-"))
inst = base / "Videosprog"
print("copying", old_dir)
shutil.copytree(old_dir, inst, ignore=lambda d, n: ["modeller"] if Path(d) == old_dir else [])
p = subprocess.Popen([str(inst / "Videosprog.exe"), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
rid = 0


def call(method, params=None):
    global rid
    rid += 1
    p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n").encode())
    p.stdin.flush()
    while True:
        msg = json.loads(p.stdout.readline())
        if msg.get("id") == rid:
            return msg


def tool(name, **args):
    r = call("tools/call", {"name": name, "arguments": args})["result"]
    return r["content"][0]["text"], bool(r.get("isError"))


call("initialize", {"protocolVersion": "2025-06-18"})
print("before:", tool("status", job_id=job)[0].splitlines()[0])
staging = base / "Videosprog.new"
shutil.copytree(new_dir, staging, ignore=lambda d, n: ["modeller"] if Path(d) == new_dir else [])
if not (staging / "version.txt").exists():
    (staging / "version.txt").write_text("9.9.9", encoding="utf-8")   # the installer writes it
inst.rename(base / "Videosprog.old")   # works although the server runs from it, as in the installer
staging.rename(inst)
text, err = tool("hent_tekst", job_id=job, skill=3)
print("after: ", "ERROR" if err else "", text[:400])
p.stdin.close()
p.wait(10)
ok = ("zlib" in text or "decompress" in text) if expect == "zlib" else (err and "Quit" in text and "zlib" not in text)
print("OK" if ok else "FAIL", f"(expected {expect})")
shutil.rmtree(base, ignore_errors=True)
sys.exit(0 if ok else 1)
