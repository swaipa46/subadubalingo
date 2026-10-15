"""End-to-end test of the MCP server, acting as Claude: start a job, follow it with 'vent', translate through
get_text/save_text (fake translation: prefix), follow to done.
With --cut: also suggest cuts (suggest_cuts), and when the user reviews them (review_cuts), keep the first cut
and add one by time through approve_cuts, as if the user had said so in the chat.
  python tests/mcp_test.py VIDEO [--speech swedish] [--subs norwegian] [--bad] [--cut] [--exe "D:/.../dist/SubaDubaLingo/SubaDubaLingo.exe"]"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
video = sys.argv[1]


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


cmd = [arg("--exe", ""), "--mcp"] if "--exe" in sys.argv else [sys.executable, str(root / "app" / "main.py"), "--mcp"]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, cwd=str(root))
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
    t0 = time.time()
    r = call("tools/call", {"name": name, "arguments": args})["result"]
    text = r["content"][0]["text"]
    print(f"--- {name} ({time.time() - t0:.0f}s){' ERROR' if r.get('isError') else ''}\n{text[:600]}")
    return text


print(call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}})["result"]["serverInfo"])
p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
p.stdin.flush()
print([t["name"] for t in call("tools/list")["result"]["tools"]])

text = tool("start_video", video=video, speech=arg("--speech", "swedish"), subtitles=arg("--subs", "norwegian"),
            spoken_language="danish", relisten=True, cut="--cut" in sys.argv)
job = re.search(r"Job (\S+):", text).group(1)
for _ in range(200):
    text = tool("wait", job_id=job)
    if "Claude's turn" in text or ": done" in text.split("\n")[0] or "error" in text.split("\n")[0]:
        break

if "Claude's turn" in text:
    first = tool("get_text", job_id=job, start=1, count=150, **({} if "--no-skill" in sys.argv else {"skill": 4}))
    srt = first.split("\n\n", 1)[1]
    srt = srt.split("\n\nNext:")[0]
    codes = re.findall(r"hand (?:it )?back as '(\w\w)'", first)
    print("codes:", codes)
    for i, code in enumerate(codes):
        if "--bad" in sys.argv and i == 0:
            bad = re.sub(r"00:00:0(\d)", r"00:00:1\1", srt, count=1)
            tool("save_text", job_id=job, language=code, srt=bad)
        out = re.sub(r"(-->[^\n]*\n)", lambda m: m.group(1) + ("" if i == 0 else f"{code.upper()}: "), srt)
        tool("save_text", job_id=job, language=code, srt=out, words="Testingen => Testing" if i == 0 else "")
    if "--cut" in sys.argv:
        n = len(re.findall(r"-->", srt))
        tool("suggest_cuts", job_id=job, cuts=f"1 start Folk kommer ind\n{max(1, n // 2)} offtopic Snak\nvrøvl")
        tool("suggest_cuts", job_id=job, cuts="", done=True)
        for _ in range(20):
            text = tool("wait", job_id=job)
            if "reviewing the cuts" in text.split("\n")[0] or "error" in text.split("\n")[0]:
                break
        tool("approve_cuts", job_id=job, keep=[1], add="0:05-0:07 inappropriate Navn nævnt", wait_for_user=True)
        tool("approve_cuts", job_id=job)
    for _ in range(100):
        text = tool("wait", job_id=job)
        if ": done" in text.split("\n")[0] or "error" in text.split("\n")[0]:
            break
tool("status")
p.stdin.close()
p.wait(timeout=10)
