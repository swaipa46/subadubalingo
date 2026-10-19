"""Sizes read back from Tk are screen distances, not always pixels: a canvas made without a height gives Tk's default
("7c") from cget, so int(cget(...)) broke the queue summary on the CI runner. The app reads them with winfo_pixels.
  python tests/test_tk_sizes.py"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app" / "subadubalingo"

# No int() of a widget's height or width option anywhere in the app (a Text's height in lines is fine: it is a count)
BAD = re.compile(r"""int\(\s*[\w.]+\.cget\(\s*["'](height|width)["']\s*\)\s*\)""")
found = [f"{p.name}:{n}: {line.strip()}" for p in sorted(APP.glob("*.py"))
         for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
         if BAD.search(line) and "self.text.cget" not in line]
assert not found, "use winfo_pixels(cget(...)):\n  " + "\n  ".join(found)

# What Tk gives back, and that winfo_pixels reads every form of it
import tkinter as tk  # noqa: E402

root = tk.Tk()
root.withdraw()
default = tk.Canvas(root)
assert default.winfo_pixels(default.cget("height")) > 0, default.cget("height")
assert default.winfo_pixels("7c") == default.winfo_pixels(default.cget("height"))
for given in (40, 33.0, "2c"):
    c = tk.Canvas(root, height=given)
    assert c.winfo_pixels(c.cget("height")) == c.winfo_pixels(given), (given, c.cget("height"))
root.destroy()
print("all ok")
