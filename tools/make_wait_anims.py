"""Draw the small waiting animations (assets/ui/<theme>/icons/{loading,ai-working,download,player2-idle}-<n>.png).

Every frame is drawn on a coarse art grid (one art pixel = 2 screen px at 1x), in the colours of each theme's
tokens.json, on a transparent background, and saved at 1x and as an exact nearest-neighbour @2x. Holes in a shape
are transparent, so the card colour shows through in both themes.

    python tools/make_wait_anims.py [--preview out.png]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "assets" / "ui"
THEMES = ("a-light", "b-night")
PX = 2   # screen px per art pixel at 1x


def rgb(h: str) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255


class Grid:
    """A w x h art-pixel canvas; save() writes 1x and @2x."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.px: dict[tuple[int, int], tuple] = {}

    def put(self, x: int, y: int, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            if c is None:
                self.px.pop((x, y), None)
            else:
                self.px[(x, y)] = c

    def rect(self, x0: int, y0: int, x1: int, y1: int, c):
        """Inclusive rectangle."""
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.put(x, y, c)

    def image(self) -> Image.Image:
        im = Image.new("RGBA", (self.w * PX, self.h * PX), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for (x, y), c in self.px.items():
            d.rectangle((x * PX, y * PX, x * PX + PX - 1, y * PX + PX - 1), fill=c)
        return im

    def save(self, folder: Path, name: str) -> Image.Image:
        im = self.image()
        im.save(folder / f"{name}.png")
        im.resize((im.width * 2, im.height * 2), Image.NEAREST).save(folder / f"{name}@2x.png")
        return im


# --- loading: 16 px ring of 8 dots, a bright head running clockwise -------------------------------

RING = [(3, 0), (5, 1), (6, 3), (5, 5), (3, 6), (1, 5), (0, 3), (1, 1)]   # top-left of each 2x2 dot, clockwise


def loading(T: dict) -> list[Grid]:
    frames = []
    for f in range(8):
        g = Grid(8, 8)
        for i, (x, y) in enumerate(RING):
            age = (f - i) % 8   # 0 = the head
            c = rgb(T["accent"]) if age == 0 else rgb(T["heading"]) if age in (1, 2) else rgb(T["step_pending_icon"])
            g.rect(x, y, x + 1, y + 1, c)
        frames.append(g)
    return frames


# --- ai-working: 24 px chat bubble whose three dots hop in turn ("the AI is typing") -------------

def ai_working(T: dict) -> list[Grid]:
    hops = [None, 0, 1, 2, None, None]
    frames = []
    for hop in hops:
        g = Grid(12, 12)
        body = rgb(T["heading"])
        g.rect(1, 0, 10, 8, body)
        g.rect(0, 1, 11, 7, body)
        # the tail, bottom left
        g.rect(2, 9, 4, 9, body)
        g.rect(2, 10, 2, 10, body)
        for i, x in enumerate((2, 5, 8)):
            y = 3 if hop == i else 4
            g.rect(x, y, x + 1, y + 1, None)   # holes: the card shows through
        frames.append(g)
    return frames


# --- download: 24 px arrow dropping into a tray -------------------------------------------------

ARROW = [(5, 6), (5, 6), (5, 6), (3, 8), (4, 7), (5, 6)]   # x ranges per row, top to bottom


def download(T: dict) -> list[Grid]:
    frames = []
    for off in (-4, -2, 0, 1, 2, 3):
        g = Grid(12, 12)
        tray = rgb(T["heading"])
        g.rect(0, 8, 1, 11, tray)
        g.rect(10, 8, 11, 11, tray)
        g.rect(0, 10, 11, 11, tray)
        for r, (x0, x1) in enumerate(ARROW):
            y = r + off
            if 0 <= y <= 9:
                g.rect(x0, y, x1, y, rgb(T["accent"]))
        frames.append(g)
    return frames


# --- player2-idle: 32x20 gamepad on the idle assistant card, its start light blinking ---------------

def player2_idle(T: dict) -> list[Grid]:
    frames = []
    for lit in (True, False):
        g = Grid(16, 10)
        body = rgb(T["hint"])
        g.rect(1, 1, 14, 7, body)
        g.rect(0, 2, 15, 6, body)
        g.rect(2, 8, 4, 8, body)    # grips
        g.rect(11, 8, 13, 8, body)
        # d-pad (hole)
        g.rect(3, 3, 3, 5, None)
        g.rect(2, 4, 4, 4, None)
        # two buttons (holes)
        g.rect(11, 3, 11, 3, None)
        g.rect(13, 4, 13, 4, None)
        # the start light in the middle
        g.rect(7, 4, 8, 4, rgb(T["accent"]) if lit else None)
        frames.append(g)
    return frames


ANIMS = [   # name, maker, frame ms
    ("loading", loading, 100),
    ("ai-working", ai_working, 150),
    ("download", download, 120),
    ("player2-idle", player2_idle, 700),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args()
    tokens = json.loads((UI / "tokens.json").read_text(encoding="utf-8"))["themes"]
    made: dict[str, list[tuple[str, list[Image.Image]]]] = {}
    for theme in THEMES:
        T = tokens[theme]
        folder = UI / theme / "icons"
        for name, maker, _ms in ANIMS:
            ims = [g.save(folder, f"{name}-{n}") for n, g in enumerate(maker(T))]
            made.setdefault(theme, []).append((name, ims))
    if a.preview:
        preview(made, tokens, a.preview)


def preview(made, tokens, out: Path, zoom: int = 6):
    """Each theme on its card colour: one row per animation, frames left to right, zoomed by `zoom`."""
    cell = 36 * zoom
    rows = len(ANIMS)
    cols = max(len(ims) for _, ims in made[THEMES[0]])
    sheet = Image.new("RGBA", (cols * cell * 2 + 40, rows * cell + 20), (128, 128, 128, 255))
    for t, theme in enumerate(THEMES):
        bg = rgb(tokens[theme]["card"])
        x0 = t * (cols * cell + 20) + 10
        for r, (_name, ims) in enumerate(made[theme]):
            for c, im in enumerate(ims):
                tile = Image.new("RGBA", (cell - 8, cell - 8), bg)
                big = im.resize((im.width * zoom, im.height * zoom), Image.NEAREST)
                tile.alpha_composite(big, ((tile.width - big.width) // 2, (tile.height - big.height) // 2))
                sheet.paste(tile, (x0 + c * cell, 10 + r * cell))
    sheet.save(out)


if __name__ == "__main__":
    main()
