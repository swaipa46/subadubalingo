"""Screenshots of Send fejlrapport: the error done view with its button, Indstillinger with its button, and the dialog
(with the log shown, sending, sent, kept for later, and the crash offer). Runs in a temp home folder (never the real
settings or data) against a local mock of the relay (never swaipa.dk), opens on the monitor the user is not working on
and captures with PrintWindow.
  python tests/report_shots.py OUT_DIR --theme light|night [--lang da|sv|no|en]
Files: report-relay-<name>[-<lang>].png"""
import ctypes
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-report-shots-"))
(T / "home").mkdir()
os.environ["USERPROFILE"] = str(T / "home")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import claude, common, gui, i18n, installer, report, theme  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
assert common.data_dir() == T / "home" / ".subadubalingo"
(common.data_dir() / "settings.json").write_text(json.dumps({"skill_installed": claude.SKILL_VERSION,
                                                             "ui_language": forced_lang}), encoding="utf-8")
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
installer._save_ui_language = lambda code: None
theme.load_fonts()
suffix = "" if forced_lang == "da" else f"-{forced_lang}"
home = str(Path.home())
# A log as a failed dub leaves it (the home folder and the video's name are redacted in the dialog)
common.log.info("JOB video=%s\\Videos\\David e2e.mp4 lang=da speech=sv subs=no transcribe=True", home)
common.log.info("RUN D:\\SubaDubaLingo\\bin\\piper\\piper.exe --model sv_SE-nst-medium.onnx --json-input")
common.log.error("ToolError: piper exited with 1")

hold = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        hold.wait(5)   # "Sender …" stays on screen until the shot is taken
        body = json.dumps({"status": "created", "ref": 12}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
RELAY = f"http://127.0.0.1:{server.server_address[1]}/api/subadubalingo/v1/reports"
s = socket.socket()
s.bind(("127.0.0.1", 0))
DOWN = f"http://127.0.0.1:{s.getsockname()[1]}/api/subadubalingo/v1/reports"   # nothing listens: no network
s.close()


def pump(root, sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.03)


def shot(win, name):
    # Out of view the window only paints what changed; a 1 px resize makes it paint everything before the grab
    if win is root:   # the dialogs size themselves; a fixed geometry would cut off what they add later
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"{w + 1}x{h}")
        pump(win, 0.3)
        win.geometry(f"{w}x{h}")
    pump(win, 0.6)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"report-relay-{name}{suffix}.png")
    print("saved", name)


def to_other_monitor(dlg):
    area = _screen.other_work_area() or _screen.offscreen_area()
    if area:
        dlg.geometry(f"+{area[0] + 80}+{area[1] + 80}")


def wait_result(d):
    for _ in range(300):
        pump(d, 0.05)
        if d.result is not None:
            return
    raise AssertionError("no result")


COMMENT = {"da": "Den stoppede ved indtalingen på svensk. Jeg havde valgt norske undertekster.",
           "sv": "Den stannade vid inläsningen på svenska. Jag hade valt norska undertexter.",
           "no": "Den stoppet ved innlesingen på svensk. Jeg hadde valgt norske undertekster.",
           "en": "It stopped at the Swedish voice-over. I had picked Norwegian subtitles."}[forced_lang]

root = tk.Tk()
app = gui.App(root)
_screen.place(root)
root.geometry("1600x1000")
# The error state (CONTINUE?) as in the design's 5-done-error: the voice step failed, the subtitles were made
video = Path.home() / "Videos" / "David e2e.mp4"
L = gui.LANG_BY_CODE
app.job = {"video": video, "lang": L["da"], "speech": L["sv"], "subs": L["no"], "cut": True, "transcribe": True,
           "check": True, "translate": [L["sv"], L["no"]], "improve_audio": True}
app._review_result = [gui.cutting.Cut(0, 6000, "start")]
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"name": n, "state": "done" if i < len(names) - 2 else "active" if i == len(names) - 2 else "todo"}
                 for i, n in enumerate(names)]
app.created = [gui.srt_name(video, L["da"]), gui.srt_name(video, L["no"])]
for path in app.created:
    app.files.insert("end", path.name)
app._error_type = "ToolError"
app._show("done")
app._show_result("error", "piper exited with 1", replay=True)
shot(root, "done-error")

# Indstillinger with the button next to the version
dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language,
                         on_report=app.open_report)
to_other_monitor(dlg)
shot(dlg, "settings")
dlg.grab_release()
dlg.destroy()


def open_dialog():
    app.open_report()
    d = next(w for w in root.winfo_children() if isinstance(w, report.ReportDialog))
    to_other_monitor(d)
    d.comment.insert("1.0", COMMENT)
    return d


# The dialog from the error view, with a comment; then with the log shown
os.environ["SUBADUBALINGO_REPORT_URL"] = RELAY
d = open_dialog()
p = d.payload
assert p["kind"] == "error" and p["step"] == "voice" and p["title"].startswith("Fejl i 1-6 "), p["title"]
assert "David" not in json.dumps(p, ensure_ascii=False), "a video name went in"
shot(d, "dialog")
d._toggle_log()
shot(d, "dialog-log")
d._toggle_log()
# Sending, then sent (#12)
d._send()
shot(d, "dialog-sending")
hold.set()
wait_result(d)
assert d.result.outcome == "sent" and d.result.ref == 12, d.result
shot(d, "dialog-sent")
d.destroy()

# No network: kept for the next start
os.environ["SUBADUBALINGO_REPORT_URL"] = DOWN
d = open_dialog()
d._send()
wait_result(d)
assert d.result.outcome == "later" and any(report.pending_dir().glob("*.json")), d.result
shot(d, "dialog-queued")
d.destroy()

# A crash saved by the hooks, offered at the next start
try:
    raise KeyError("speaker")
except KeyError as e:
    report.save_crash(type(e), e, e.__traceback__, app._report_state(), theme=forced_theme)
app.offer_crash_report()
d = next(w for w in root.winfo_children() if isinstance(w, report.ReportDialog))
to_other_monitor(d)
assert d.payload["kind"] == "crash"
shot(d, "crash")
d._cancel()
assert report.take_crash() is None   # declined: not offered again
server.shutdown()
root.destroy()
