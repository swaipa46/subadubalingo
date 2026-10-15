"""Screenshots of the main window in each state: setup, working, Claude's turn (file round), answer found, done.
The answer file is faked into a temporary Downloads folder. Needs Pillow in the dev venv (not part of the program).
  python tests/screenshot.py VIDEO OUT_DIR"""
import ctypes
import ctypes.wintypes as wt
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import tkinter as tk
from tkinter import messagebox

from PIL import ImageGrab

from subadubalingo import claude, engine, gui
from subadubalingo.common import format_srt, read_srt

messagebox.askyesno = lambda *a, **k: True
messagebox.showinfo = messagebox.showerror = messagebox.showwarning = lambda *a, **k: None
downloads = Path(tempfile.mkdtemp(prefix="sdl-downloads-"))
gui.downloads_dir = lambda: downloads
out = Path(sys.argv[2])
ctypes.windll.shcore.SetProcessDpiAwareness(1)
root = tk.Tk()
app = gui.App(root)


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.03)


def take(name):
    root.lift()
    root.attributes("-topmost", True)
    pump(0.8)
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(out / name)
    root.attributes("-topmost", False)
    print("saved", name)


app.video.set(sys.argv[1])
app._refresh()
app.speech.set("Svensk")
app.subs.set("Norske")
app.redo.set(True)
app._refresh()
take("1-setup.png")
app._start()
pump(2.5)
take("2-work.png")
t0 = time.time()
while app._tdialog is None and time.time() - t0 < 300:
    pump(0.1)
take("3-claude.png")
d = app._tdialog
d._copy_file()
pump(3.0)
take("4-claude-copied.png")
job = re.search(r"id=(\S+)", d.work_file.read_text(encoding="utf-8").splitlines()[0]).group(1)
src = read_srt(d.src_srt)
blocks = []
for code in d.codes:
    prefix = "" if code == d.src.code else f"{code.upper()}: "
    blocks.append(f"=== {code} ===\n" + format_srt([engine.Cue(c.start, c.end, prefix + c.text) for c in src]))
(downloads / d.answer_name).write_text(f"{claude.ANSWER_HEADER} id={job}\n" + "\n".join(blocks)
                                       + "=== words ===\n=== end ===\n", encoding="utf-8")
pump(1.3)
take("5-claude-found.png")
while app._busy():
    pump(0.2)
take("6-done.png")
root.destroy()
