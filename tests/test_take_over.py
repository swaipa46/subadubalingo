"""The MCP side of taking over a job started in the app (docs/assistant-panel.md, "MCP take-over"), without a GUI:
an app job published by the panel (jobs.publish_app_job) is found by status/wait/get_text without a job id, get_text
marks it taken, save_text completes it; a job the user finished in an AI chat (by_user) is refused; app jobs an
earlier run left open (any origin) are cancelled at start; wait returns at once on an ended state; the guide and the instructions name the panel's phrase.
The data folder is a temp folder. Run: python tests/test_take_over.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-takeover-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()

from subadubalingo import guide, i18n, jobs, mcp_server  # noqa: E402
from subadubalingo.common import APP_NAME, Cue, format_srt  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


cues = [Cue(i * 4000, i * 4000 + 3000, f"Blok {i + 1}") for i in range(5)]
task = {"src": "da", "check": True, "targets": ["sv"], "codes": ["da", "sv"], "blocks": len(cues), "words": [],
        "cut": False, "duration": 20000, "chapters": "da", "rules": []}

# Nothing yet
check("No job is waiting" in mcp_server.tool_get_text(), "get_text without a job and without an id says so")

# An MCP job of the assistant's own is running; the user's app job reaches the assistant's turn later
own = jobs.new_request("C:/v/own.mp4", "da", None, "sv", False)
jobs.set_status(own, state="running", percent=40)
time.sleep(0.02)
app_job = jobs.publish_app_job("C:/v/webinar.mp4", jobs.cues_to_json(cues), task)
jobs.set_status(own, state="running", percent=50)   # written after: the newest status, but not waiting
st = jobs.status(app_job)
check(st.get("state") == "claude_turn" and jobs.origin(app_job) == "app" and jobs.origin(own) == "assistant",
      "publish_app_job: claude_turn, origin app (an MCP job is origin assistant)")
check(json.loads((jobs.root() / app_job / "task.json").read_text(encoding="utf-8"))["codes"] == ["da", "sv"]
      and len(json.loads((jobs.root() / app_job / "source.json").read_text(encoding="utf-8"))) == 5,
      "task.json and source.json are written")
check(jobs.waiting_job() == app_job, "waiting_job prefers the job in claude_turn over a newer running one")

s = mcp_server.tool_status()
check(app_job in s and "started this job in the app" in s and i18n.t("gui.parts.mcp_phrase", app=APP_NAME) in s,
      "status without an id finds the app job and says how to take it over (with the app's phrase)")
w = mcp_server.tool_wait()
check(app_job in w and "Your turn" in w, "wait without an id finds it and returns at once")
check(not (jobs.root() / app_job / "taken.json").exists(), "status and wait do not take it over")

since = time.time()
time.sleep(0.02)
text = mcp_server.tool_get_text(count=3)
check(f"Task for job {app_job}" in text and "Next: get_text(start=4)" in text, "get_text without an id gives its text")
check(jobs.taken_since(app_job, since) and not jobs.taken_since(app_job, time.time() + 1),
      "get_text marks the job taken (taken.json)")
r = mcp_server.tool_save_text(app_job, "da", format_srt(cues))
check("Saved 5 blocks in 'da'" in r and "Still missing" in r, "save_text is accepted")
mcp_server.tool_save_text(app_job, "sv", format_srt(cues))
r = mcp_server.tool_save_chapters(app_job, "", "Et webinar.")
check((jobs.root() / app_job / "complete.json").exists(), "everything in: complete.json, as for an MCP job")
check(jobs.waiting_job() is None, "a handed-in job is no longer waiting")

# Through JSON-RPC as a client calls it: no job_id, and a stale 2.1.x skill's 'skill' argument is ignored
j1 = jobs.publish_app_job("C:/v/rpc.mp4", jobs.cues_to_json(cues), task)
sent = []
mcp_server._send = sent.append
mcp_server._handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": "get_text", "arguments": {"skill": "2.1.1", "count": 2}}})
res = sent[-1].get("result") or {}
check(not res.get("isError") and f"Task for job {j1}" in res["content"][0]["text"],
      "tools/call get_text without a job_id (and with an old 'skill' argument) takes the waiting job")
jobs.set_status(j1, state="cancelled")

# Handed-in parts count as taken too (an assistant that skips get_text)
j2 = jobs.publish_app_job("C:/v/b.mp4", jobs.cues_to_json(cues), task)
since = time.time()
time.sleep(0.02)
jobs.write_json(jobs.root() / j2 / "parts" / "da.json", {"1": "x"})
check(jobs.taken_since(j2, since), "parts/ written after the chat view count as taken")

# Done in the chat by the user: refused
jobs.set_status(j2, state="by_user")
try:
    mcp_server.tool_save_text(j2, "da", format_srt(cues))
    check(False, "save_text on a by_user job is refused")
except ValueError as e:
    check("AI chat" in str(e), "save_text on a by_user job is refused")
check("Nothing to do" in mcp_server.tool_status(j2), "status explains by_user")
t0 = time.time()
w = mcp_server.tool_wait(j2)
check("Nothing to do" in w and time.time() - t0 < 2, "wait on a by_user job returns at once")

# Taken over in a queue: the text round ends, the video is made in a later round
jq = jobs.publish_app_job("C:/v/q.mp4", jobs.cues_to_json(cues), task)
jobs.set_status(jq, state="queued_later", percent=100)
t0 = time.time()
w = mcp_server.tool_wait(jq)
check("later" in w and "ready and can be played" not in w and time.time() - t0 < 2,
      "queued_later: wait returns at once and does not say the video is ready")
check("queued_later" in guide.guide_text("Danish", [], []), "the guide explains queued_later")

# At start every job an earlier run left open is cancelled, whatever its origin (the app was closed mid-turn);
# ended jobs and a request still waiting in the inbox are left alone
j3 = jobs.publish_app_job("C:/v/c.mp4", jobs.cues_to_json(cues), task)
mine = jobs.new_request("C:/v/mine.mp4", "da", None, "sv", False)
for req in jobs.take_inbox():   # the app took both requests (own and mine)
    jobs.accept(req)
time.sleep(0.02)
jobs.set_status(mine, state="claude_turn")   # an assistant's job, the app closed in its turn
time.sleep(0.02)
inbox = jobs.new_request("C:/v/next.mp4", "da", None, "sv", False)   # sent while the app was closed
check(jobs.waiting_job() == mine, "before the cleanup the dead job looks like it is waiting")
jobs.close_orphaned_jobs("Stoppet i appen.")
check(jobs.status(j3).get("state") == "cancelled" and jobs.status(mine).get("state") == "cancelled"
      and jobs.status(own).get("state") == "cancelled" and jobs.status(j2).get("state") == "by_user"
      and jobs.status(jq).get("state") == "queued_later" and jobs.status(inbox).get("state") in (None, "queued"),
      "close_orphaned_jobs cancels every open job, not ended ones or one still in the inbox")
check(jobs.waiting_job() is None and "No job is waiting" in mcp_server.tool_get_text(),
      "after the cleanup 'take over' finds no dead job")

# The manual, the instructions and the tool list
g = mcp_server.tool_guide()
locale_phrases = set()
for code in i18n.CODES:
    data = json.loads((Path(__file__).resolve().parent.parent / "assets" / "locales" / code / "gui.json")
                      .read_text(encoding="utf-8"))
    locale_phrases.add(data["gui.parts.mcp_phrase"].format(app=APP_NAME))
check(set(guide.TAKE_OVER_PHRASES) == locale_phrases, "the guide's phrases are the panel's, in all four languages")
check("Taking over a job started in the app" in g and all(p in g for p in guide.TAKE_OVER_PHRASES)
      and "by_user" in g, "the guide explains the take-over and by_user")
check("take over" in mcp_server.instructions().lower() and "status" in mcp_server.instructions(),
      "the instructions say: on 'take over', call status first")
tools = {t["name"]: t for t in mcp_server.TOOLS}
check("required" not in tools["get_text"]["inputSchema"] and "take" in tools["get_text"]["description"].lower()
      and "take over" in tools["status"]["description"].lower(),
      "get_text needs no job id; get_text and status describe the take-over")
check(all(x in jobs.STATES and x in jobs.ENDED and x not in mcp_server.ACTIVE for x in ("by_user", "queued_later")),
      "by_user and queued_later are ended states")

print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
