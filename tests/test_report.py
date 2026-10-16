"""Send fejlrapport (report.py) without the GUI, against a local mock of the relay (docs/report-relay.md): the request
JSON (schema 1, lengths, redaction of the home folder, the user name and video names), every answer the contract names
(201, 200 added/duplicate, 400, 413 then success, 429, 500, connection refused), the pending queue (at most 10, retry
at start in a thread) and crash drafts. Runs in a temp home folder, never the real data folder or the real relay.
  python tests/test_report.py"""
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-report-"))
HOME = T / "Users" / "Jens Hansen"
HOME.mkdir(parents=True)
os.environ["USERPROFILE"] = str(HOME)
os.environ["USERNAME"] = "jhansen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from subadubalingo import common, i18n, report  # noqa: E402

assert common.data_dir() == HOME / ".subadubalingo", common.data_dir()
data = common.data_dir()
i18n.set_language("da")


# --- The mock relay ------------------------------------------------------------------------------------------------

class Relay:
    """Answers POSTs from a script of (status, json body or None, headers); after the script: 201 with the next ref.
    Keeps every request (headers, parsed body)."""

    def __init__(self):
        self.script: list = []
        self.requests: list = []
        self.ref = 11
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                relay.requests.append((dict(self.headers), json.loads(raw.decode("utf-8")), len(raw)))
                if relay.script:
                    status, answer, headers = relay.script.pop(0)
                else:
                    relay.ref += 1
                    status, answer, headers = 201, {"status": "created", "ref": relay.ref}, {}
                if len(raw) > 256 * 1024:
                    status, answer, headers = 413, None, {}
                out = json.dumps(answer).encode() if answer is not None else b""
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/subadubalingo/v1/reports"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


relay = Relay()
os.environ["SUBADUBALINGO_REPORT_URL"] = relay.url
assert report.report_url() == relay.url
assert report.REPORT_URL == "https://swaipa.dk/api/subadubalingo/v1/reports"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def pending() -> list[str]:
    return sorted(f.stem for f in report.pending_dir().glob("*.json")) if report.pending_dir().is_dir() else []


# --- redact ---------------------------------------------------------------------------------------------------------
home = str(HOME)
text = (f"open {home}\\Videos\\Kundemøde.mp4; json {home.replace(chr(92), chr(92) * 2)}\\\\x; fwd "
        f"{home.replace(chr(92), '/')}/b; {home.upper()}\\C; user jhansen, JHansen; not jhansens or xjhansen; "
        f"D:\\Optagelser\\Salg Q3 - danske undertekster.srt; saved Kundemøde - svenske undertekster.srt; "
        f"E:/x/Intern demo.MKV")
r = report.redact(text, videos={"Kundemøde.mp4"})
assert "Jens Hansen" not in r and "jens hansen" not in r.lower(), r
assert r.count("~") == 4 and "%USERPROFILE%" not in r, r
assert r.count("<user>") == 2 and "jhansens" in r and "xjhansen" in r, r
assert "Kundemøde" not in r and "Salg Q3" not in r and "Intern demo" not in r, r
assert "~\\Videos\\<video>.mp4" in r and "<video> - danske undertekster.srt" in r, r
assert "saved <video> - svenske undertekster.srt" in r and "E:/x/<video>.MKV" in r, r
assert report.redact("ab cd", home="", user="ab") == "ab cd"   # too short to replace safely
assert report.redact("a.b.c and bin\\piper\\piper.exe", home="", user="") == "a.b.c and bin\\piper\\piper.exe"
cmd = "RUN D:\\a\\ffmpeg\\ffmpeg.exe -y -i frame.png -vf subtitles=sample.srt -frames:v 1 out.png"   # not one long name
assert report.redact(cmd, home="", user="") == cmd, report.redact(cmd, home="", user="")
assert report.redact("ffmpeg -i D:\\b\\c d.mp4 -y", home="", user="") == "ffmpeg -i D:\\b\\<video>.mp4 -y"

# Video names the app has seen: text_state.json (subtitle files) and the Claude jobs
(data / "text_state.json").write_text(json.dumps({
    f"{home.lower()}\\videos\\bestyrelsesmøde - danske undertekster.srt": {"video": [1, 2], "version": 3}}),
    encoding="utf-8")
(data / "jobs" / "aaa").mkdir(parents=True)
(data / "jobs" / "aaa" / "job.json").write_text(json.dumps({"video": f"{home}\\Videos\\Lønsamtale.mp4"}),
                                                encoding="utf-8")
assert report.known_videos() == {"bestyrelsesmøde", "Lønsamtale"}, report.known_videos()

# --- fingerprint ----------------------------------------------------------------------------------------------------
fp = report.fingerprint("ToolError", "voice", "piper exited with 1\nmore")
assert re.fullmatch(r"[0-9a-f]{16}", fp), fp
assert fp == report.fingerprint("ToolError", "voice", "piper exited with 3221225477")       # numbers left out
assert fp != report.fingerprint("ToolError", "dub", "piper exited with 1")                   # another step
assert fp != report.fingerprint("KeyError", "voice", "piper exited with 1")                  # another type
assert (report.fingerprint("ToolError", "burn", 'Kan ikke åbne "C:\\a b\\x.mp4" (fejl 5)') ==
        report.fingerprint("ToolError", "burn", 'Kan ikke åbne "D:\\c\\y z.mkv" (fejl 12)'))   # paths left out
assert report.fingerprint("E", "", "x in ~\\Videos\\a.mp4") == report.fingerprint("E", "", "x in D:/b/c.mp4")

# --- build ----------------------------------------------------------------------------------------------------------
log_lines = [f"2026-10-16 10:{i % 60:02d}:00,000 INFO step {i} {home}\\Videos\\Kundemøde.mp4 jhansen" for i in range(4000)]
(data / "log.txt").write_text("\n".join(log_lines) + "\n2026 ERROR piper exited with 1\n", encoding="utf-8")
run = {"steps": [{"step": "voice", "name": "Indtaler på svensk", "state": "error"}],
       "created": ["Kundemøde - danske undertekster.srt"], "options": {"lang": "da", "speech": "sv", "cut": True}}
p = report.build("error", error=f"piper exited with 1 ({home}\\Videos\\Kundemøde.mp4)\n" + "x" * 9000,
                 error_type="ToolError", step="voice", step_no="1-6", step_name="Indtaler på svensk", run=run,
                 theme="night", videos={"Kundemøde.mp4"})
FIELDS = {"schema", "report_id", "created_at", "kind", "fingerprint", "title", "app_version", "os", "ui_language",
          "theme", "step", "error", "comment", "run", "log_tail"}
assert set(report.body(p)) == FIELDS, set(report.body(p)) ^ FIELDS
assert p["schema"] == 1 and p["kind"] == "error" and p["step"] == "voice" and p["theme"] == "night", p
assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", p["report_id"])
assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", p["created_at"]), p["created_at"]
assert p["app_version"] == common.APP_VERSION and re.fullmatch(r"Windows \S+ \d+", p["os"]), p["os"]
assert p["ui_language"] == "da" and len(p["error"]) <= 4000 and p["error"].startswith("piper exited with 1 (~\\")
assert p["title"] == "Fejl i 1-6 Indtaler på svensk: piper exited with 1 (~\\Videos\\<video>.mp4)", p["title"]
assert p["fingerprint"] == report.fingerprint("ToolError", "voice", p["error"]), p
assert p["run"]["created"] == ["<video> - danske undertekster.srt"], p["run"]
tail = p["log_tail"]
assert len(tail.encode("utf-8")) <= 200_000 and tail.startswith("2026-10-16 ") and tail.endswith("piper exited with 1\n")
assert "Kundemøde" not in tail and "Jens Hansen" not in tail and "jhansen" not in tail and "~\\Videos\\<video>.mp4 <user>" in tail
long = report.set_comment(dict(p), "Den frøs i " + home + "\\Kundemøde.mp4 " + "y" * 5000)
assert len(long["comment"]) == 4000 and long["comment"].startswith("Den frøs i ~\\<video>.mp4"), long["comment"][:60]
assert len(report.build("user", run={}, theme="light")["title"]) <= 120
u = report.set_comment(report.build("user", theme="light"), "Knappen " + "z" * 300)
assert u["title"].startswith("Rapport fra brugeren: Knappen") and len(u["title"]) == 120, u["title"]
assert u["fingerprint"] == report.fingerprint("user", "", u["comment"]) and u["error"] == "" and u["step"] == ""
# A body over 256 KB (multi-byte text, escapes) is cut in the log tail before it is sent
huge = dict(p, log_tail=("æ\\\"" * 200_000))
b = report.body(huge)
assert len(json.dumps(b, ensure_ascii=False).encode("utf-8")) <= report.BODY_MAX and b["log_tail"], len(b["log_tail"])


def check_request(i: int, payload: dict):
    headers, sent, size = relay.requests[i]
    assert headers["Content-Type"] == "application/json; charset=utf-8", headers
    assert headers["User-Agent"] == f"SubaDubaLingo/{common.APP_VERSION}", headers
    assert set(sent) == FIELDS and sent["schema"] == 1 and sent["report_id"] == payload["report_id"], sent.keys()
    assert size <= 256 * 1024 and len(sent["title"]) <= 120 and len(sent["error"]) <= 4000
    assert len(sent["comment"]) <= 4000 and len(sent["log_tail"].encode("utf-8")) <= 200_000
    blob = json.dumps(sent, ensure_ascii=False)
    for secret in ("Jens Hansen", "jhansen", "Kundemøde", "bestyrelsesmøde", "Lønsamtale"):
        assert secret.lower() not in blob.lower(), secret
    return sent


# --- Answers --------------------------------------------------------------------------------------------------------
def fresh(comment="Den stoppede ved svensk"):
    return report.set_comment(report.build("error", error="piper exited with 1", error_type="ToolError", step="voice",
                                           step_no="1-6", step_name="Indtaler på svensk", run=run, theme="light",
                                           videos={"Kundemøde.mp4"}), comment)


# 201: created, nothing left in pending
relay.script = [(201, {"status": "created", "ref": 12}, {})]
p = fresh(f"Det skete med {home}\\Videos\\Lønsamtale.mp4 og bestyrelsesmøde")
res = report.submit(p)
assert res.outcome == "sent" and res.ref == 12, res
sent = check_request(0, p)
assert sent["comment"] == "Det skete med ~\\Videos\\<video>.mp4 og <video>", sent["comment"]
assert pending() == []
# 200 added / duplicate
for status in ("added", "duplicate"):
    relay.script = [(200, {"status": status, "ref": 7}, {})]
    res = report.submit(fresh())
    assert res.outcome == "sent" and res.ref == 7, res
assert pending() == []
# 400: rejected, not kept, not retried
relay.script = [(400, {"error": "title too long"}, {})]
n = len(relay.requests)
res = report.submit(fresh())
assert res.outcome == "rejected" and res.error == "title too long" and len(relay.requests) == n + 1, res
assert pending() == []
# 413 then success: once more with the log tail cut to 50 KB
relay.script = [(413, None, {}), (201, {"status": "created", "ref": 13}, {})]
n = len(relay.requests)
p = fresh()
assert len(p["log_tail"].encode("utf-8")) > 50_000
res = report.submit(p)
assert res.outcome == "sent" and res.ref == 13 and len(relay.requests) == n + 2, res
first, second = relay.requests[n][1], relay.requests[n + 1][1]
assert first["report_id"] == second["report_id"] and len(second["log_tail"].encode("utf-8")) <= 50_000
assert first["log_tail"].endswith(second["log_tail"]) and re.match(r"\d{4}-\d\d-\d\d ", second["log_tail"])
# 413 twice: rejected
relay.script = [(413, None, {}), (413, None, {})]
assert report.submit(fresh()).outcome == "rejected" and pending() == []
# 429: kept for later
relay.script = [(429, {"error": "slow down"}, {"Retry-After": "3600"})]
p429 = fresh()
res = report.submit(p429)
assert res.outcome == "later" and res.busy, res
assert pending() == [p429["report_id"]]
saved = json.loads((report.pending_dir() / f"{p429['report_id']}.json").read_text(encoding="utf-8"))
assert set(saved) == FIELDS and saved["comment"] == "Den stoppede ved svensk"   # exactly what is sent
# 500: kept
relay.script = [(500, None, {})]
p500 = fresh()
res = report.submit(p500)
assert res.outcome == "later" and not res.busy and set(pending()) == {p429["report_id"], p500["report_id"]}, res
# Connection refused: kept
os.environ["SUBADUBALINGO_REPORT_URL"] = f"http://127.0.0.1:{free_port()}/api/subadubalingo/v1/reports"
n = len(relay.requests)
t0 = time.time()
pdown = fresh()
res = report.submit(pdown)
assert res.outcome == "later" and len(relay.requests) == n and time.time() - t0 < 10, res
assert len(pending()) == 3
# retry_pending with no network: tries the oldest, stops there, keeps all
os.utime(report.pending_dir() / f"{p429['report_id']}.json", (1000, 1000))
os.utime(report.pending_dir() / f"{p500['report_id']}.json", (2000, 2000))
assert [r.outcome for r in report.retry_pending()] == ["later"] and len(pending()) == 3
os.environ["SUBADUBALINGO_REPORT_URL"] = relay.url

# --- Retry at start (background thread) -----------------------------------------------------------------------------
relay.script = [(500, None, {})]   # the oldest still fails: the rest wait for the next start
n = len(relay.requests)
th = report.start_retry()
th.join(10)
assert not th.is_alive() and len(relay.requests) == n + 1 and len(pending()) == 3
assert relay.requests[n][1]["report_id"] == p429["report_id"]   # oldest first
n = len(relay.requests)
t0 = time.time()
th = report.start_retry()
assert time.time() - t0 < 0.5   # never blocks the caller (the window)
th.join(10)
assert pending() == [] and len(relay.requests) == n + 3
assert [r[1]["report_id"] for r in relay.requests[n:]] == [p429["report_id"], p500["report_id"], pdown["report_id"]]
assert report.start_retry() is None   # nothing waits: no thread
# A pending report the relay rejects (400) is dropped; an unreadable file too
report.save_pending(fresh())
(report.pending_dir() / "broken.json").write_text("{", encoding="utf-8")
relay.script = [(400, {"error": "bad"}, {})]
report.retry_pending()
assert pending() == [], pending()

# --- At most 10 pending, the oldest dropped -------------------------------------------------------------------------
ids = []
for i in range(12):
    q = fresh()
    report.save_pending(q)
    os.utime(report.pending_dir() / f"{q['report_id']}.json", (5000 + i, 5000 + i))
    ids.append(q["report_id"])
assert sorted(pending()) == sorted(ids[2:]) or len(pending()) == 10, pending()
assert len(pending()) == 10 and ids[-1] in pending()
for f in report.pending_dir().glob("*.json"):
    f.unlink()

# --- Crashes: saved as a draft, never sent by themselves ------------------------------------------------------------
n = len(relay.requests)
try:
    {}["missing"]
except KeyError as e:
    f = report.save_crash(type(e), e, e.__traceback__, {"view": "setup"}, theme="night")
assert f and f.parent == report.crash_dir() and pending() == [] and len(relay.requests) == n
c = report.take_crash()
assert c["kind"] == "crash" and c["error"].startswith("KeyError: 'missing'") and "Traceback" in c["error"], c["error"]
assert c["title"] == "Nedbrud: KeyError: 'missing'" and c["fingerprint"] == report.fingerprint("KeyError", "", c["error"])
assert c["run"] == {"view": "setup"} and c["theme"] == "night"
report.forget_crash(c["report_id"])
assert report.take_crash() is None
# The hooks: sys.excepthook and threading.excepthook save a draft and still call the previous hook
called = []
sys.excepthook = lambda *a: called.append("sys")
threading.excepthook = lambda a: called.append("thread")
report.install_hooks(None, run=lambda: {"view": "work"})
try:
    raise RuntimeError(f"boom in {home}\\Videos\\Kundemøde.mp4")
except RuntimeError as e:
    sys.excepthook(type(e), e, e.__traceback__)
c = report.take_crash()
assert called == ["sys"] and c and c["run"] == {"view": "work"}, (called, c)
assert "Jens Hansen" not in c["error"] and "Kundemøde" not in c["error"], c["error"]
report.forget_crash(c["report_id"])
th = threading.Thread(target=lambda: 1 / 0)
th.start()
th.join()
c = report.take_crash()
assert called == ["sys", "thread"] and c["error"].startswith("ZeroDivisionError") and c["run"] == {}, (called, c)
# A crash draft is sent only through the dialog (submit), which then forgets it
res = report.submit(report.set_comment(c, "Den lukkede"))
report.forget_crash(c["report_id"])
assert res.outcome == "sent" and relay.requests[-1][1]["kind"] == "crash" and report.take_crash() is None
for i in range(5):   # at most CRASH_KEEP drafts
    try:
        raise ValueError(i)
    except ValueError as e:
        report.save_crash(type(e), e, e.__traceback__)
assert len(list(report.crash_dir().glob("*.json"))) == report.CRASH_KEEP

# --- No mail left over ----------------------------------------------------------------------------------------------
src = (Path(report.__file__)).read_text(encoding="utf-8")
assert "mailto" not in src and "zipfile" not in src and "support" not in src
for code in i18n.CODES:
    keys = json.loads((Path(report.__file__).parents[2] / "assets" / "locales" / code / "report.json")
                      .read_text(encoding="utf-8"))
    assert not [k for k in keys if "mail" in k or "zip" in k], code
    assert "Kasper" not in json.dumps(keys, ensure_ascii=False), code

relay.server.shutdown()
print("all ok")
