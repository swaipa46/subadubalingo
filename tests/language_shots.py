"""Screenshots of the language and theme choosers: the main window's header, Indstillinger, and the first start's
language page, in one theme and app language. Windows open on the monitor the user is not working on and are
captured with PrintWindow (works while the screen is locked).
  python tests/language_shots.py OUT_DIR --theme light|night --lang da|sv|no|en"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk

import _screen
from subadubalingo import gui, i18n, installer, skin, theme

ctypes.windll.shcore.SetProcessDpiAwareness(1)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
gui.Settings.save = lambda self: None          # never touch the real settings
installer._save_ui_language = lambda code: None
theme.load_fonts()
tag = f"{forced_theme}-{forced_lang}"


def pump(root, sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.03)


def shot(win, name):
    pump(win, 0.6)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"{name}-{tag}.png")
    print("saved", name)


# Main window: the header with both pickers and the cog, then Indstillinger over it
root = tk.Tk()
app = gui.App(root)
_screen.place(root)
shot(root, "main")
# The done view, as after a run (STAGE CLEAR, the new video, the run's files, what next)
video = Path.home() / "Videos" / "Webinar.mp4"
result = gui.dub_name(video, gui.LANG_BY_CODE["sv"], gui.LANG_BY_CODE["no"])
app.created = [gui.srt_name(video, gui.LANG_BY_CODE["da"]), gui.srt_name(video, gui.LANG_BY_CODE["no"]), result]
for path in app.created:
    app.files.insert("end", path.name)
# What the run knew: Swedish speech, Norwegian subtitles, a cut of 10 s, and a new length of 0:08 (the file does not
# exist, so ffmpeg is not asked)
L = gui.LANG_BY_CODE
app.job = {"video": video, "lang": L["da"], "speech": L["sv"], "subs": L["no"], "cut": True, "transcribe": True,
           "check": True, "translate": [L["sv"], L["no"]]}
app._review_result = [gui.cutting.Cut(0, 6000, "start"), gui.cutting.Cut(12000, 16000, "pause")]
gui.engine.probe = lambda runner, v: gui.engine.MediaInfo(8000, True)
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"name": n, "state": "done"} for n in names]
app._show("done")
app._show_result("done", result, replay=True)
shot(root, "done")
# The error state (CONTINUE?): the voice step failed, the subtitles were made
for i, row in enumerate(app.step_rows):
    row["state"] = "done" if i < len(names) - 2 else "active" if i == len(names) - 2 else "todo"
app.files.delete(0, "end")
app.created = app.created[:2]
for path in app.created:
    app.files.insert("end", path.name)
app._show_result("error", "[FEJLBESKED FRA PROGRAMMET]", replay=True)
shot(root, "done-error")
app._show("setup")
dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language)
area = _screen.other_work_area()
if area:
    dlg.geometry(f"+{area[0] + 60}+{area[1] + 60}")
shot(dlg, "settings")
dlg.destroy()
root.destroy()

# First start: the install window's language page, as installer.offer_install builds it
root = tk.Tk()
root.title(gui.APP_NAME)
skin.apply(root, forced_theme, root.winfo_fpixels("1i") / 96)
skin.dialog(root)
installer._language_page(root, {"ui_language": forced_lang}, lambda: None)
_screen.place(root)
shot(root, "first-start")
root.destroy()
