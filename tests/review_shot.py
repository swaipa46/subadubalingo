"""The cut review on its own, for layout work: opens it for VIDEO with its "<navn> - danske undertekster.srt", the
silent pauses and a few made-up Claude suggestions, and saves screenshots.

  python tests/review_shot.py VIDEO OUTDIR [--theme light|night] [--scrub] [--wait]   (--wait: leave the window open
  to try it by hand)

--scrub: instead of r1-r3, waits for the scrubbing thumbnails, drags the end of a cut backwards (with made-up mouse
events) and saves review-scrub.png with the live picture. The sound is not played; the snippets asked for are
counted.
"""
import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
ctypes.windll.shcore.SetProcessDpiAwareness(1)
import tkinter as tk

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
scrubbing = "--scrub" in sys.argv
played = []
if scrubbing:
    from subadubalingo import scrub

    class _Quiet:   # winsound without the sound
        SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 0x20000, 0x1, 0x2

        @staticmethod
        def PlaySound(sound, flags):
            played.append((time.time(), sound))
    scrub.winsound = _Quiet
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
    pump(0.8)
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    _screen.grab_window(hwnd).save(out / name)   # PrintWindow: also works while the screen is locked
    print("saved", out / name)


class Ev:
    def __init__(self, x):
        self.x, self.y = x, 0


def scrub_shot():
    rv = app._review
    t0 = time.time()
    while not rv.scrub.thumbs_done and time.time() - t0 < 120:
        pump(0.2)
    print(f"scrub: audio {rv.scrub.seconds.get('audio', 0):.2f} s, thumbnails {rv.scrub.seconds.get('thumbs', 0):.2f} s,",
          len(list(rv.scrub.thumbs.glob("*.png"))), "thumbnails")
    cut = next(c for c in rv.cuts if c.end < dur - 2000)   # one whose end is inside the video
    rv._select(cut)
    pump(1)
    x = rv.x_of(cut.end)
    rv._on_press(Ev(x))
    t1 = time.time()
    for i in range(1, 31):   # back 1.5 s over about 0.6 s, like a hand would
        rv._on_drag(Ev(x - i * (rv.x_of(cut.end) - rv.x_of(cut.end - 50))))
        pump(0.02)
    print(f"scrub: {len(played)} snippets asked for during {time.time() - t1:.2f} s of dragging;",
          "caption:", rv.cap_a.cget("text"))
    take("review-scrub.png")
    rv._on_release(Ev(rv.x_of(cut.end)))
    pump(0.5)
    print("after release:", rv.cap_a.cget("text"), "; stop asked:", played[-1][1] is None)


pump(1.5)
if scrubbing:
    scrub_shot()
    if "--wait" in sys.argv:
        root.mainloop()
    else:
        root.destroy()
    sys.exit(0)
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
