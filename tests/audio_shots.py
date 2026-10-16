"""Screenshots of Forbedr lyden: setup card 2 with the check box (on, and greyed when nothing would use the original
speech) and Indstillinger's "Lyd" card. Windows open on the monitor the user is not working on and are captured
with PrintWindow. The app's data folder is a temp folder.
  python tests/audio_shots.py OUT_DIR --theme light|night [--lang da|sv|no|en] [--video VIDEO]"""
import ctypes
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk

import _screen
from subadubalingo import common

tmp = Path(tempfile.mkdtemp(prefix="audio-shots-"))
common._DATA_DIR = tmp / "data"           # never Kasper's real settings or data
common._DATA_DIR.mkdir()
from subadubalingo import gui, i18n, theme  # noqa: E402  (after the data folder is set)

ctypes.windll.shcore.SetProcessDpiAwareness(1)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
src = Path(arg("--video", "C:/Users/karyl/Cowork/Video subtitles test/"
                          "Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4"))
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
gui.Settings.save = lambda self: None
theme.load_fonts()
suffix = "" if forced_lang == "da" else f"-{forced_lang}"


def pump(root, sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.03)


def shot(win, name):
    pump(win, 0.6)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"audio-{name}{suffix}.png")
    print("saved", name)


try:
    video = tmp / "David e2e.mp4"
    if src.is_file():
        shutil.copyfile(src, video)
    root = tk.Tk()
    app = gui.App(root)
    root.geometry("1600x1000")
    _screen.place(root)
    L = gui.LANG_BY_CODE
    app.video.set(str(video))
    app.cut.set(True)
    app._refresh(lang_changed=True)
    app.speech.set(L["sv"].name)
    app.subs.set(L["no"].word("choice"))
    app._refresh()
    shot(root, "setup")
    # A dub without the original kept, nothing cut: the original speech is not in the new video
    app.cut.set(False)
    app.settings.keep_original_audio = False
    app._refresh()
    shot(root, "setup-off")
    gui.SettingsDialog.last_open = "sound"   # one card open at a time: this one
    dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language)
    area = _screen.other_work_area()
    if area:
        dlg.geometry(f"+{area[0] + 60}+{area[1] + 20}")
    shot(dlg, "settings")
    dlg.destroy()
    root.destroy()
finally:
    shutil.rmtree(tmp, ignore_errors=True)
