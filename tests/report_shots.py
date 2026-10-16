"""Screenshots of Send fejlrapport: the error done view with its button, Indstillinger with its button, and the
dialog before and after "Lav mailen". Runs in a temp home folder (never the real settings or data), opens on the
monitor the user is not working on and captures with PrintWindow. "Lav mailen" writes the zip to a temp folder and
neither opens Explorer nor a mail draft.
  python tests/report_shots.py OUT_DIR --theme light|night [--lang da|sv|no|en]"""
import ctypes
import json
import os
import sys
import tempfile
import time
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
sent = []


def fake_send(comment, app_state=None, job_id=None):
    dest = report.write_zip(report.contents(app_state, job_id), comment, T / "Downloads")
    sent.append(dest)
    return dest


report.send = fake_send


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
    _screen.grab_window(hwnd).save(out / f"report-{name}{suffix}.png")
    print("saved", name)


def to_other_monitor(dlg):
    area = _screen.other_work_area() or _screen.offscreen_area()
    if area:
        dlg.geometry(f"+{area[0] + 80}+{area[1] + 80}")


root = tk.Tk()
app = gui.App(root)
_screen.place(root)
root.geometry("1600x1000")
# The error state (CONTINUE?) as in the design's 5-done-error: the voice step failed, the subtitles were made
video = Path.home() / "Videos" / "David e2e.mp4"
L = gui.LANG_BY_CODE
app.job = {"video": video, "lang": L["da"], "speech": L["sv"], "subs": L["no"], "cut": True, "transcribe": True,
           "check": True, "translate": [L["sv"], L["no"]]}
app._review_result = [gui.cutting.Cut(0, 6000, "start")]
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"name": n, "state": "done" if i < len(names) - 2 else "active" if i == len(names) - 2 else "todo"}
                 for i, n in enumerate(names)]
app.created = [gui.srt_name(video, L["da"]), gui.srt_name(video, L["no"])]
for path in app.created:
    app.files.insert("end", path.name)
app._show("done")
app._show_result("error", "[FEJLBESKED FRA PROGRAMMET]", replay=True)
shot(root, "done-error")

# Indstillinger with the button next to Åbn logfil
dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language,
                         on_report=app.open_report)
to_other_monitor(dlg)
shot(dlg, "settings")
dlg.grab_release()
dlg.destroy()

# The dialog, from the error view, with a comment
d = report.ReportDialog(root, app._report_state(), None, scale=app.scale)
to_other_monitor(d)
d.comment.insert("1.0", {"da": "Den stoppede ved indtalingen på svensk. Jeg havde valgt norske undertekster.",
                         "sv": "Den stannade vid inläsningen på svenska. Jag hade valt norska undertexter.",
                         "no": "Den stoppet ved innlesingen på svensk. Jeg hadde valgt norske undertekster.",
                         "en": "It stopped at the Swedish voice-over. I had picked Norwegian subtitles."}[forced_lang])
shot(d, "dialog")
d._make()
shot(d, "dialog-done")
assert sent and sent[0].is_file(), sent
d.destroy()
root.destroy()
