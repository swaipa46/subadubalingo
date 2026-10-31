"""The waiting animations (assets/ui/README.md, "Waiting animations (T13)"): every frame exists in both themes at 1x
and @2x (exactly twice the size), and skin.Anim shows frame 0, moves after start() and is back on frame 0 after
stop(); the idle card's gamepad moves while the app waits.
  python tests/test_wait_anims.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-anims-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

from PIL import Image  # noqa: E402

from subadubalingo import gui, i18n, skin, theme  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


ANIMS = {"loading": 8, "ai-working": 6, "download": 6, "player2-idle": 2}
ui = Path(__file__).resolve().parent.parent / "assets" / "ui"
for folder in ("a-light", "b-night"):
    missing, sizes = [], set()
    for name, n in ANIMS.items():
        for i in range(n):
            one, two = ui / folder / "icons" / f"{name}-{i}.png", ui / folder / "icons" / f"{name}-{i}@2x.png"
            if not (one.exists() and two.exists()):
                missing.append(one.name)
                continue
            a, b = Image.open(one).size, Image.open(two).size
            sizes.add(b == (a[0] * 2, a[1] * 2))
    check(not missing and sizes == {True}, f"{folder}: every frame at 1x and @2x ({missing or 'all there'})")

root = tk.Tk()
root.attributes("-alpha", 0.0)
root.update()
theme.load_fonts()
skin.apply(root, theme.resolve("light"), 1.0)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.02)


a = skin.Anim(root, "loading", 8, 100)
a.pack()
first = str(a.cget("image"))
pump(0.2)
check(str(a.cget("image")) == first and not a.running, "frame 0 until start()")
seen = set()
a.start()
for _ in range(10):
    pump(0.06)
    seen.add(str(a.cget("image")))
check(len(seen) > 2 or skin.reduced_motion(), f"it moves after start() ({len(seen)} frames seen)")
a.stop()
pump(0.3)
check(str(a.cget("image")) == first and not a.running, "frame 0 again after stop()")
a.start()
a.destroy()
pump(0.3)
check(True, "destroyed while running: no error")

app = gui.App(root)
pump(0.3)
check(app.idle_anim.running or skin.reduced_motion(), "the idle card's gamepad blinks while the app waits")
root.destroy()
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
