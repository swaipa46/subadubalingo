"""Send fejlrapport (report.py) without the GUI: redaction, what goes in the zip (never texts, video or audio), the
zip's name, the support address and the mailto link. Runs in a temp home folder, never the real data folder.
  python tests/test_report.py"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

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

# --- redact -----------------------------------------------------------------------------------------------------
home = str(HOME)
text = (f"open {home}\\Videos\\a.mp4; json {home.replace(chr(92), chr(92) * 2)}\\\\x; fwd {home.replace(chr(92), '/')}/b;"
        f" {home.upper()}\\C; user jhansen, JHansen; not jhansens or xjhansen")
r = report.redact(text)
assert "Jens Hansen" not in r and "jens hansen" not in r.lower(), r
assert r.count("%USERPROFILE%") == 4, r
assert r.count("<user>") == 2 and "jhansens" in r and "xjhansen" in r, r
assert report.redact("ab cd", home="", user="ab") == "ab cd"   # too short to replace safely

# --- support address ----------------------------------------------------------------------------------------------
appd = T / "app"
appd.mkdir()
report.app_dir = lambda: appd
assert report.support_address() == ""
(T / "support.local.txt").write_text("# local\nsupport@example.org\n", encoding="utf-8")
assert report.support_address() == "support@example.org"     # from source: the repo's support.local.txt
(appd / "support.txt").write_text("﻿help@example.com\n", encoding="utf-8")
assert report.support_address() == "help@example.com"        # next to the exe wins (BOM ignored)

# --- contents -------------------------------------------------------------------------------------------------------
(data / "log.txt").write_text(f"2026 INFO start\n2026 INFO video {home}\\Videos\\Møde.mp4\n", encoding="utf-8")
(data / "settings.json").write_text(json.dumps({"theme": "night", "last_dir": f"{home}\\Videos"}), encoding="utf-8")
(data / "corrections.txt").write_text("hemmeligt ord\n", encoding="utf-8")
(data / "text_state.json").write_text("{}", encoding="utf-8")
old = data / "jobs" / "aaa"
new = data / "jobs" / "bbb"
for d in (old, new):
    (d / "parts").mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "error", "message": f"failed in {home}"}), encoding="utf-8")
os.utime(old / "status.json", (1, 1))
(new / "job.json").write_text(json.dumps({"video": f"{home}\\Videos\\Møde.mp4", "lang": "da"}), encoding="utf-8")
(new / "task.json").write_text(json.dumps({"codes": ["da", "sv"], "blocks": 12, "words": ["a", "b"],
                                           "rules": [["x", "y"]]}), encoding="utf-8")
(new / "cuts.json").write_text(json.dumps({"cuts": [{"start": 0, "end": 4000, "kind": "start",
                                                     "text": "HEMMELIG UNDERTEKST"}], "summary": "s"}), encoding="utf-8")
(new / "source.json").write_text(json.dumps([[0, 1000, "HEMMELIG UNDERTEKST"]]), encoding="utf-8")
(new / "complete.json").write_text(json.dumps({"codes": {"da": [[0, 1, "HEMMELIG UNDERTEKST"]]}}), encoding="utf-8")
(new / "parts" / "da.json").write_text(json.dumps({"1": "HEMMELIG UNDERTEKST"}), encoding="utf-8")
(new / "audio.wav").write_bytes(b"RIFF")
(new / "x.srt").write_text("HEMMELIG UNDERTEKST", encoding="utf-8")

state = {"view": "done", "result": ["error", f"ffmpeg failed: {home}\\Videos\\Møde.mp4"],
         "job": {"video": "Møde.mp4", "lang": "da"}}
files = report.contents(state)
assert set(files) == {"log.txt", "settings.json", "about.json", "last_run.json", "job/job.json", "job/status.json",
                      "job/task.json", "job/cuts.json"}, sorted(files)
blob = b"\n".join(files.values()).decode("utf-8")
assert "HEMMELIG" not in blob and "hemmeligt" not in blob, "a text went in"
assert "Jens Hansen" not in blob and "jhansen" not in blob, "the user's name went in"
assert "%USERPROFILE%\\\\Videos" in files["settings.json"].decode() or "%USERPROFILE%" in files["settings.json"].decode()
task = json.loads(files["job/task.json"])
assert task["codes"] == ["da", "sv"] and task["blocks"] == 12 and "left out" in task["words"], task
cuts = json.loads(files["job/cuts.json"])
assert cuts["cuts"][0]["kind"] == "start" and "left out" in cuts["cuts"][0]["text"], cuts
about = json.loads(files["about.json"])
assert about["version"] == common.APP_VERSION and about["windows"].startswith("Windows"), about
assert set(report.contents(None, job_id="aaa")) >= {"job/status.json"}
assert "job/job.json" not in report.contents(None, job_id="aaa")   # the job asked for, not the newest

# A long log: only the newest part, from a whole line
report.LOG_TAIL = 100
(data / "log.txt").write_text("".join(f"line {i:04d} xxxxxxxxxx\n" for i in range(50)), encoding="utf-8")
tail = report.contents()["log.txt"].decode()
assert tail.startswith("line ") and tail.rstrip().endswith("line 0049 xxxxxxxxxx") and len(tail) <= 100, tail

# --- zip ------------------------------------------------------------------------------------------------------------
dl = T / "Downloads"
z1 = report.write_zip(files, f"  Det gik galt i {home}  ", dl)
z2 = report.write_zip(files, "", dl)
assert z1.name.startswith("SubaDubaLingo fejlrapport ") and z1.suffix == ".zip", z1.name
assert z2.name == z1.stem + " (2).zip", z2.name
with zipfile.ZipFile(z1) as z:
    names = z.namelist()
    assert "comment.txt" in names and "job/cuts.json" in names, names
    assert z.read("comment.txt").decode() == "Det gik galt i %USERPROFILE%\n"
with zipfile.ZipFile(z2) as z:
    assert "comment.txt" not in z.namelist()
i18n.set_language("en")
assert report.zip_name("2026-10-16") == "SubaDubaLingo error report 2026-10-16.zip"
i18n.set_language("da")
assert report.zip_name("2026-10-16") == "SubaDubaLingo fejlrapport 2026-10-16.zip"

# --- mail -----------------------------------------------------------------------------------------------------------
body = report.mail_body("Den stoppede & frøs\nved 50 %", z1.name)
assert body.startswith("Den stoppede & frøs\nved 50 %") and z1.name in body and common.APP_VERSION in body, body
assert "(ingen kommentar)" in report.mail_body("   ", "f.zip")
assert len(report.mail_body("x" * 5000, "f.zip")) < report.MAIL_COMMENT_MAX + 400
url = report.mailto("help@example.com", "Fejlrapport fra SubaDubaLingo 2.0.0", body)
assert url.startswith("mailto:help@example.com?subject=Fejlrapport%20fra%20SubaDubaLingo%202.0.0&body="), url
assert " " not in url and "\n" not in url and "&" not in url.split("&body=")[1], url
assert unquote(url.split("&body=")[1]) == body.replace("\n", "\r\n")
assert report.mailto("", "s", "b") == "mailto:?subject=s&body=b"

print("all ok")
