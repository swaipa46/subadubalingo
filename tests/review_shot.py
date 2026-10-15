"""The cut review on its own, for layout work: opens it for VIDEO with its "<navn> - danske undertekster.srt", the
silent pauses and a few made-up Claude suggestions, and saves screenshots.

  python tests/review_shot.py VIDEO OUTDIR [--wait]     (--wait: leave the window open to try it by hand)
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
ctypes.windll.shcore.SetProcessDpiAwareness(1)
import tkinter as tk

from PIL import ImageGrab

from subadubalingo import cutting, gui
from subadubalingo.common import FFMPEG, LANG_BY_CODE, find_srt, read_srt

video, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
cues = read_srt(find_srt(video, LANG_BY_CODE["da"]))
wav = Path(tempfile.mkdtemp()) / "a.wav"
subprocess.run([str(FFMPEG()), "-v", "error", "-y", "-i", str(video), "-ac", "1", "-ar", "16000", str(wav)], check=True)
env = cutting.envelope(wav)
dur = int(len(env) * cutting.ENVELOPE_MS)
claude_cuts, _ = cutting.parse_cut_lines(["1-3 start Folk kommer ind og tester lyd",
                                          f"{len(cues) // 2} offtopic Snak om vejret",
                                          f"{len(cues) // 2 + 6}-{len(cues) // 2 + 7} uklart Utydeligt, flere taler i munden på hinanden"],
                                         cues, dur)
sugg = cutting.combine_suggestions(cutting.find_pauses(cues, dur, env, min_ms=5000), claude_cuts)

from subadubalingo import theme  # noqa: E402
if "--theme" in sys.argv:   # light or night, whatever Windows and the settings say
    _forced = sys.argv[sys.argv.index("--theme") + 1]
    theme.resolve = lambda choice: _forced
theme.load_fonts()
import _screen  # noqa: E402
root = tk.Tk()
app = gui.App(root)
_screen.place(root)   # on the monitor the user is not working on
app.step_rows = []
app._start_review(video, dur, cues, sugg, env)


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
    _screen.grab((r.left, r.top, r.right, r.bottom)).save(out / name)
    root.attributes("-topmost", False)
    print("saved", out / name)


pump(1.5)
take("r1.png")
rv = app._review
rv._select(rv.cuts[1])
pump(3)
take("r2.png")
rv._toggle(rv.cuts[0])
rv._zoom(0.5)
pump(2)
take("r3.png")
if "--wait" in sys.argv:
    root.mainloop()
else:
    root.destroy()
