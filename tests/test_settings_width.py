"""Indstillinger keeps its width when every card is folded (2.2.0 error report: open Programmets sprog, close it again,
and all the folded cards were half a centimetre wide at the left edge, because the scroll box took the width the
folded cards asked for). The data folder is a temp folder.
  python tests/test_settings_width.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-settings-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

from subadubalingo import gui, i18n, skin, theme  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.02)


root = tk.Tk()
# Mapped but transparent and tiny (a never-mapped root's clipboard and grabs misbehave on Windows)
root.geometry("1x1+0+0")
root.attributes("-alpha", 0.0)
root.update()
theme.load_fonts()
skin.apply(root, theme.resolve("light"), 1.0)
gui.Settings.save = lambda self: None
gui.SettingsDialog.last_open = "text"
dlg = gui.SettingsDialog(root, gui.Settings())
dlg.attributes("-alpha", 0.0)
pump(1.0)
canvas, _, outer = dlg._body
cards = dlg._cards
w0 = int(canvas.cget("width"))
check(w0 > 200, f"the cards have a real width at first ({w0} px)")

# The report's steps: Programmets sprog open (Tekst og stemmer folds), then closed again: every card folded
cards["text"].show(False)
cards["language"].show(True)
dlg._fit_body()
pump(0.3)
check(int(canvas.cget("width")) == w0, f"opening the widest card does not widen it ({canvas.cget('width')} px)")
cards["language"].show(False)
dlg._fit_body()
pump(0.5)
check(not any(getattr(c, "opened", False) for c in cards.values()), "every card is folded")
check(int(canvas.cget("width")) == w0, f"the scroll box keeps one width ({canvas.cget('width')} px, was {w0})")
check(outer.winfo_width() >= w0 - 2, f"the folded cards fill it ({outer.winfo_width()} px)")
check(all(c.winfo_width() >= w0 - 2 for c in cards.values()), "and each card is as wide as the box")

dlg.destroy()
root.destroy()
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
