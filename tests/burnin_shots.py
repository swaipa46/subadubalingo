"""Screenshots of Indstillinger's "Indbrændte undertekster" card in one theme: the defaults over a still of the test
video, and big text at the top in a box with a logo. The window opens on the monitor the user is not working on and
is captured with PrintWindow. Settings and data go to a temp folder.
  python tests/burnin_shots.py OUT_DIR --theme light|night [--lang da]"""
import ctypes
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subadubalingo import common

TMP = Path(tempfile.mkdtemp(prefix="burnin-shots-"))
common._DATA_DIR = TMP / "data"
common._DATA_DIR.mkdir()

import tkinter as tk

import _screen
from subadubalingo import gui, i18n, installer, theme

ctypes.windll.shcore.SetProcessDpiAwareness(1)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
installer._save_ui_language = lambda code: None
theme.load_fonts()
ROOT = Path(__file__).resolve().parent.parent
VIDEO = Path("C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4")
video = TMP / VIDEO.name
if VIDEO.exists():
    shutil.copyfile(VIDEO, video)


def pump(root, sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.03)


def shot(win, name):
    pump(win, 2.5)   # the sample is made by ffmpeg in a thread
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"burnin-{name}.png")
    print("saved", name)


root = tk.Tk()
app = gui.App(root)
_screen.place(root)
for name, values in (("settings", {"last_video": str(video)}),
                     ("settings-logo", {"last_video": str(video), "burnin_size": "l", "burnin_position": "top",
                                        "burnin_box": True, "burnin_logo_on": True,
                                        "burnin_logo": str(ROOT / "assets/branding/icon-256.png"),
                                        "burnin_logo_corner": "br"}),
                     ("settings-no-video", {"last_video": "", "burnin_size": "m", "burnin_position": "bottom",
                                            "burnin_box": False, "burnin_logo_on": False})):
    for k, v in values.items():
        setattr(app.settings, k, v)
    dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language)
    area = _screen.other_work_area()
    if area:
        dlg.geometry(f"+{area[0] + 40}+{area[1] + 20}")
    shot(dlg, name)
    dlg.destroy()
root.destroy()
shutil.rmtree(TMP, ignore_errors=True)
