"""Build the SubaDubaLingo pixel icon (PNG, ICO, SVG) from hand-drawn rect lists."""
import os
import sys
from PIL import Image

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

BG, SHADOW, CYAN, GLOSS, SCREEN, YELLOW, CREAM = (
    "#1a0840", "#ff2e88", "#22e4ff", "#b9f6ff", "#2a0f6b", "#ffd400", "#fff4d6")

# 24x24 master (same as the design canvas)
ICON24 = [
    (BG, [(1, 0, 22, 24), (0, 1, 24, 22)]),
    (SHADOW, [(5, 3, 16, 17), (4, 4, 18, 15), (3, 5, 20, 13), (7, 20, 4, 1), (7, 21, 3, 1), (7, 22, 2, 1)]),
    (CYAN, [(4, 2, 16, 17), (3, 3, 18, 15), (2, 4, 20, 13), (6, 19, 4, 1), (6, 20, 3, 1), (6, 21, 2, 1)]),
    (GLOSS, [(5, 2, 3, 1), (3, 4, 1, 2)]),
    (SCREEN, [(5, 4, 14, 13), (4, 5, 16, 11)]),
    (YELLOW, [(10, 5, 1, 7), (11, 6, 1, 5), (12, 7, 1, 3), (13, 8, 1, 1), (21, 1, 1, 3), (20, 2, 3, 1)]),
    (CREAM, [(6, 13, 6, 1), (13, 13, 5, 1), (8, 15, 8, 1)]),
]

# 16x16 small-size variant: same bubble, no shadow/subtitles, readable in the taskbar
ICON16 = [
    (BG, [(1, 0, 14, 16), (0, 1, 16, 14)]),
    (CYAN, [(3, 2, 10, 9), (2, 3, 12, 7), (4, 11, 2, 1), (4, 12, 1, 1)]),
    (SCREEN, [(4, 3, 8, 7), (3, 4, 10, 5)]),
    (YELLOW, [(6, 4, 1, 5), (7, 5, 1, 3), (8, 6, 1, 1), (14, 1, 1, 1)]),
    (CREAM, [(5, 9, 6, 1)]),
]


def draw(spec, grid, scale, pad=0, size=None):
    size = size or grid * scale + 2 * pad
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    off = (size - grid * scale) // 2
    for color, rects in spec:
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
        for x, y, w, h in rects:
            for yy in range(y * scale, (y + h) * scale):
                for xx in range(x * scale, (x + w) * scale):
                    px[off + xx, off + yy] = rgb
    return img


def svg(spec, grid):
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {grid} {grid}" '
             f'shape-rendering="crispEdges">']
    for color, rects in spec:
        parts.append(f'<g fill="{color}">' + "".join(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}"/>' for x, y, w, h in rects) + "</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# Every size is an integer multiple of a pixel grid, so the pixels stay sharp.
sizes = {
    16: draw(ICON16, 16, 1),
    24: draw(ICON24, 24, 1),
    32: draw(ICON16, 16, 2),
    48: draw(ICON24, 24, 2),
    64: draw(ICON16, 16, 4),
    128: draw(ICON24, 24, 5, size=128),
    256: draw(ICON24, 24, 10, size=256),
    512: draw(ICON24, 24, 21, size=512),
    1024: draw(ICON24, 24, 42, size=1024),
}
for s, img in sizes.items():
    img.save(os.path.join(OUT, f"icon-{s}.png"))

ico_sizes = [16, 24, 32, 48, 64, 128, 256]
sizes[256].save(os.path.join(OUT, "SubaDubaLingo.ico"), format="ICO",
                sizes=[(s, s) for s in ico_sizes],
                append_images=[sizes[s] for s in ico_sizes if s != 256])

with open(os.path.join(OUT, "icon.svg"), "w") as f:
    f.write(svg(ICON24, 24))
with open(os.path.join(OUT, "icon-small.svg"), "w") as f:
    f.write(svg(ICON16, 16))
print("ok")
