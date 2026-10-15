"""Render the wordmark lockups and title screen to PNG with Chromium (fonts embedded locally)."""
import os
import sys
from playwright.sync_api import sync_playwright

OUT, FONTS = sys.argv[1], sys.argv[2]
ICON_SVG = open(os.path.join(OUT, "icon.svg")).read()
PS2P = "file://" + os.path.join(FONTS, "fontsource-press-start-2p-5.3.0/package/files/press-start-2p-latin-400-normal.woff2")
VT = "file://" + os.path.join(FONTS, "fontsource-vt323-5.3.0/package/files/vt323-latin-400-normal.woff2")

HEAD = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:'PS2P';src:url('{PS2P}') format('woff2')}}
@font-face{{font-family:'VT';src:url('{VT}') format('woff2')}}
html,body{{margin:0;background:transparent}}
#c{{display:inline-flex;align-items:center;font-family:'PS2P';-webkit-font-smoothing:none}}
#c svg{{display:block}}
.tag{{font-family:'VT';display:flex;align-items:center}}
.dot{{display:inline-block}}
</style></head><body>"""


def icon(px):
    return ICON_SVG.replace("<svg ", f'<svg width="{px}" height="{px}" ', 1)


def lockup(variant, icon_px, word_px, tag_px, gap, pad, bg, with_tag=True):
    dark = variant == "dark"
    a, b = ("#ffd400", "#22e4ff") if dark else ("#1a0840", "#d1186f")
    shadow = (f"{word_px//12}px {word_px//12}px 0 #ff2e88, {word_px//6}px {word_px//6}px 0 #4a148c"
              if dark else f"{word_px//12}px {word_px//12}px 0 #22e4ff")
    tagc = "#fff4d6" if dark else "#1a0840"
    dot = max(4, tag_px // 4)
    tag = (f'<div class="tag" style="font-size:{tag_px}px;letter-spacing:{tag_px//7}px;color:{tagc};'
           f'gap:{tag_px//2}px;margin-top:{word_px//2}px"><span>CUT</span>'
           f'<span class="dot" style="width:{dot}px;height:{dot}px;background:#ff2e88"></span><span>DUB</span>'
           f'<span class="dot" style="width:{dot}px;height:{dot}px;background:{"#22e4ff" if dark else "#22a6c9"}"></span>'
           f'<span>SUB</span></div>') if with_tag else ""
    return (HEAD + f'<div id="c" style="gap:{gap}px;padding:{pad}px;background:{bg}">{icon(icon_px)}'
            f'<div><div style="font-size:{word_px}px;line-height:1;text-shadow:{shadow};padding-right:{word_px//6}px;'
            f'padding-bottom:{word_px//6}px"><span style="color:{a}">SUBADUBA</span><span style="color:{b}">LINGO</span></div>'
            f'{tag}</div></div></body></html>')


TITLE = HEAD + f"""<div id="c" style="width:1200px;height:680px;position:relative;overflow:hidden;background:#1a0840;color:#fff4d6;display:block">
<div style="position:absolute;left:0;right:0;top:0;display:flex;justify-content:space-between;padding:28px 40px;font-size:16px;letter-spacing:2px">
<div><span style="color:#ff2e88">1UP</span>&nbsp;&nbsp;00</div><div><span style="color:#22e4ff">LANG</span>&nbsp;&nbsp;DA SV NO EN</div><div><span style="color:#ffd400">CREDIT</span>&nbsp;&nbsp;01</div></div>
<div style="position:absolute;left:140px;top:150px;width:6px;height:6px;background:#ffd400"></div>
<div style="position:absolute;left:1010px;top:120px;width:6px;height:6px;background:#22e4ff"></div>
<div style="position:absolute;left:1060px;top:420px;width:4px;height:4px;background:#fff4d6"></div>
<div style="position:absolute;left:90px;top:470px;width:4px;height:4px;background:#ff2e88"></div>
<div style="position:absolute;left:230px;top:300px;width:4px;height:4px;background:#fff4d6"></div>
<div style="position:absolute;left:930px;top:260px;width:4px;height:4px;background:#ffd400"></div>
<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:36px;padding-top:20px">
{icon(168)}
<div style="font-size:64px;line-height:1;text-shadow:5px 5px 0 #ff2e88,10px 10px 0 #4a148c"><span style="color:#ffd400">SUBADUBA</span><span style="color:#22e4ff">LINGO</span></div>
<div class="tag" style="gap:22px;font-size:44px;letter-spacing:6px;margin-top:6px"><span>CUT</span><span class="dot" style="width:10px;height:10px;background:#ff2e88"></span><span>DUB</span><span class="dot" style="width:10px;height:10px;background:#22e4ff"></span><span>SUB</span></div>
<div style="font-size:16px;letter-spacing:3px;color:#ffd400;margin-top:8px">INSERT VIDEO &#9656; PRESS START</div></div>
<div style="position:absolute;left:0;right:0;bottom:28px;text-align:center;font-size:12px;letter-spacing:3px;color:#9c8cc9">EVERY LANGUAGE &middot; NO CONTINUES</div>
<div style="position:absolute;inset:0;background:repeating-linear-gradient(to bottom,rgba(0,0,0,.22) 0 2px,transparent 2px 4px)"></div>
</div></body></html>"""

JOBS = [
    # name, html, device scale
    ("logo-dark.png", lockup("dark", 144, 48, 36, 40, 56, "#1a0840"), 1),
    ("logo-dark-transparent.png", lockup("dark", 144, 48, 36, 40, 24, "transparent"), 1),
    ("logo-light.png", lockup("light", 144, 48, 36, 40, 56, "#f9f9f9"), 1),
    ("logo-light-transparent.png", lockup("light", 144, 48, 36, 40, 24, "transparent"), 1),
    ("logo-light-transparent@2x.png", lockup("light", 144, 48, 36, 40, 24, "transparent"), 2),
    ("header-logo.png", lockup("light", 48, 24, 0, 16, 4, "transparent", with_tag=False), 1),
    ("header-logo@2x.png", lockup("light", 48, 24, 0, 16, 4, "transparent", with_tag=False), 2),
    ("header-logo-tagline.png", lockup("light", 48, 16, 20, 14, 4, "transparent"), 1),
    ("header-logo-tagline@2x.png", lockup("light", 48, 16, 20, 14, 4, "transparent"), 2),
    ("title-screen.png", TITLE, 1),
    ("title-screen@2x.png", TITLE, 2),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, html, scale in JOBS:
        page = browser.new_page(device_scale_factor=scale, viewport={"width": 1600, "height": 900})
        path = os.path.join(OUT, "_tmp.html")
        open(path, "w").write(html)
        page.goto("file://" + path)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(200)
        page.locator("#c").screenshot(path=os.path.join(OUT, name), omit_background=True)
        page.close()
        print(name)
    os.remove(os.path.join(OUT, "_tmp.html"))
    browser.close()
