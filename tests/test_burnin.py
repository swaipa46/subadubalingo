"""The look of burned-in subtitles (burnin.py): the force_style string and filter chain per option, old settings
files, and a real ffmpeg burn of 3 s of the test video with each option (checked by looking at the pixels where the
text and the logo should be). Writes only to a temp folder.
  python tests/test_burnin.py [--keep]   (--keep leaves the burned clips and stills in the temp folder)"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common

TMP = Path(tempfile.mkdtemp(prefix="burnin-test-"))
common._DATA_DIR = TMP / "data"          # never Kasper's ~/.subadubalingo
common._DATA_DIR.mkdir()

from PIL import Image

from subadubalingo import burnin, engine
from subadubalingo.common import FFMPEG, Runner

ROOT = Path(__file__).resolve().parent.parent
VIDEO = Path(os.environ.get("SDL_TEST_VIDEO", "C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4"))
LOGO = ROOT / "assets" / "branding" / "icon-256.png"
L = burnin.Look

# --- force_style per option ----------------------------------------------------------------------------------

# Defaults are exactly the look from before the setting (engine.SUB_STYLE, unchanged since 1.0)
OLD = ("FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1.6,"
       "Shadow=0.6,MarginV=28")
assert burnin.force_style(L()) == OLD, burnin.force_style(L())
assert burnin.force_style(None) == OLD
assert engine.SUB_STYLE == OLD


def style(**kw) -> dict:
    return dict(p.split("=", 1) for p in burnin.force_style(L(**kw)).split(","))


assert style(size="s")["FontSize"] == "16" and style(size="m")["FontSize"] == "20" and style(size="l")["FontSize"] == "26"
assert "Alignment" not in style(position="bottom")
assert style(position="top")["Alignment"] == "6"   # SSA top centre
box = style(box=True)
assert box["BorderStyle"] == "3" and box["OutlineColour"] == burnin.BOX_COLOUR and box["Shadow"] == "0", box
assert style(box=False)["BorderStyle"] == "1"
both = style(size="l", position="top", box=True)
assert both["FontSize"] == "26" and both["Alignment"] == "6" and both["BorderStyle"] == "3"
# The logo does not change the text's style
assert burnin.force_style(L(logo=str(LOGO), logo_corner="bl")) == OLD
# No commas or quotes inside values: the style sits in '...' inside an ffmpeg filter graph
for kw in ({}, {"size": "l", "position": "top", "box": True}):
    assert "'" not in burnin.force_style(L(**kw))

# Bad values (hand-edited settings) fall back to the defaults
bad = L(size="xl", position="middle", box=0, logo=None, logo_corner="c")
assert (bad.size, bad.position, bad.box, bad.logo, bad.logo_corner) == ("m", "bottom", False, "", "br")

# Settings objects from before the feature (no burnin_* fields) give the defaults
class OldSettings:
    whisper_model = "large"
assert L.from_settings(OldSettings()) == L()
class NewSettings:
    burnin_size, burnin_position, burnin_box = "l", "top", True
    burnin_logo_on, burnin_logo, burnin_logo_corner = True, "x.png", "bl"
assert L.from_settings(NewSettings()) == L("l", "top", True, "x.png", "bl")
NewSettings.burnin_logo_on = False   # the logo is remembered but not used
assert L.from_settings(NewSettings()) == L("l", "top", True, "", "bl")

# gui.Settings: the new fields have defaults and an old settings file still loads
import json
(common._DATA_DIR / "settings.json").write_text(json.dumps({"whisper_model": "turbo", "ui_language": "da"}), encoding="utf-8")
from subadubalingo import gui
s = gui.Settings()
assert s.whisper_model == "turbo" and L.from_settings(s) == L() and s.last_video == ""

# --- the filter chain ------------------------------------------------------------------------------------------

chain = burnin.filter_chain(L(), "s.srt", 1920, None)
assert chain == f"subtitles=s.srt:charenc=UTF-8:force_style='{OLD}'", chain
chain = burnin.filter_chain(L(logo="x", logo_corner="br"), "s.srt", 1920, "logo.png")
assert chain.startswith("movie=logo.png,scale=134:-1,format=rgba[logo];[in][logo]overlay=main_w-overlay_w-58:"
                        "main_h-overlay_h-58:format=auto,subtitles=s.srt"), chain
assert "overlay=58:58" in burnin.filter_chain(L(logo="x", logo_corner="tl"), "s.srt", 1920, "logo.png")
assert "scale=50:-1" in burnin.filter_chain(L(logo="x"), "s.srt", 720, "logo.png")   # 7 % of the width, even
# A logo that is gone is left out (the run goes on without it)
assert L(logo=str(TMP / "gone.png")).logo_path() is None

# --- real burns: 3 s of the test video with each option -------------------------------------------------------

runner = Runner()
if not VIDEO.exists() or not FFMPEG().exists():
    print("test video or ffmpeg missing; skipped the burns")
else:
    work = TMP / "work"
    work.mkdir()
    video = TMP / "clip.mp4"
    shutil.copyfile(VIDEO, video)
    srt = TMP / "clip.srt"
    # One line on screen the whole 3 s, so every still has text
    srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nTEKST TEKST TEKST TEKST\n", encoding="utf-8")
    w, h, dur = burnin.video_size(runner, video)
    assert w > 0 and h > 0 and dur > 3000, (w, h, dur)
    info = engine.MediaInfo(3000, True)

    def burn(name: str, look) -> Image.Image:
        out = TMP / f"{name}.mp4"
        prog = engine.Progress(lambda *a: None, [("burn", 1.0)])
        prog.start("burn")
        engine.burn(runner, video, srt, out, info, prog, work, look=look)
        got_w, got_h, got_dur = burnin.video_size(runner, out)
        assert (got_w, got_h) == (w, h) and 2900 <= got_dur <= 3200, (name, got_w, got_h, got_dur)
        still = TMP / f"{name}.png"
        code, _ = runner.run([FFMPEG(), "-hide_banner", "-y", "-ss", "1.5", "-i", out, "-frames:v", "1", still],
                             quiet=True)
        assert code == 0
        return Image.open(still).convert("RGB")

    def white(img, box) -> int:
        """Near-white pixels (the subtitle text) inside box, as fractions of the frame."""
        x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
        return sum(1 for p in img.crop((x0, y0, x1, y1)).get_flattened_data() if min(p) > 235)

    def dark(img, box) -> float:
        x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
        px = list(img.crop((x0, y0, x1, y1)).get_flattened_data())
        return sum(1 for p in px if max(p) < 32) / len(px)   # the video is dark grey already

    bottom_band, top_band = (0.2, 0.78, 0.8, 0.97), (0.2, 0.03, 0.8, 0.22)
    burn("default", None)
    base = burn("default-look", L())
    assert white(base, bottom_band) > 200 and white(base, bottom_band) > 2 * white(base, top_band), (white(base, bottom_band), white(base, top_band))
    sizes = {s: white(burn(f"size-{s}", L(size=s)), bottom_band) for s in burnin.SIZES}
    assert sizes["s"] < sizes["m"] < sizes["l"], sizes
    top = burn("top", L(position="top"))
    assert white(top, top_band) > 200, white(top, top_band)
    boxed = burn("box", L(box=True))
    assert dark(boxed, (0.3, 0.85, 0.7, 0.93)) > dark(base, (0.3, 0.85, 0.7, 0.93)) + 0.1
    # The logo: the app icon (mostly not the colour of the video's corner) in each corner
    corners = {"tl": (0.0, 0.0, 0.2, 0.2), "tr": (0.8, 0.0, 1.0, 0.2), "bl": (0.0, 0.8, 0.2, 1.0),
               "br": (0.8, 0.8, 1.0, 1.0)}

    def diff(a, b, box) -> float:
        x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
        pa, pb = list(a.crop((x0, y0, x1, y1)).get_flattened_data()), list(b.crop((x0, y0, x1, y1)).get_flattened_data())
        return sum(1 for p, q in zip(pa, pb) if sum(abs(i - j) for i, j in zip(p, q)) > 60) / len(pa)

    for c in burnin.CORNERS:
        img = burn(f"logo-{c}", L(logo=str(LOGO), logo_corner=c))
        changed = {k: diff(img, base, b) for k, b in corners.items()}
        assert changed[c] > 0.05 and all(v < 0.01 for k, v in changed.items() if k != c), (c, changed)
    # Everything at once, and a logo that is gone (burns without it)
    burn("all", L("l", "top", True, str(LOGO), "bl"))
    gone = burn("logo-gone", L(logo=str(TMP / "gone.png")))
    assert max(diff(gone, base, b) for b in corners.values()) < 0.01

    # The dub path: a -filter_complex for the sound next to the -vf with the logo's movie source
    prog = engine.Progress(lambda *a: None, [("burn", 1.0)])
    prog.start("burn")
    dubbed = TMP / "dub.mp4"
    engine.encode(runner, [video, video], "[1:a]aresample=48000,aformat=channel_layouts=stereo[a]", srt,
                  ["-map", "0:v:0", "-map", "[a]"], engine.AUDIO_AAC, dubbed, 3000, prog, work,
                  L(box=True, logo=str(LOGO)))
    assert burnin.video_size(runner, dubbed)[:2] == (w, h)

    # The live sample in Indstillinger: a still of the video, and the gradient without one
    frame = TMP / "sample" / "frame.png"
    frame.parent.mkdir()
    assert burnin.sample_frame(runner, video, frame, 640)
    assert Image.open(frame).size == (640, 360)
    out = TMP / "sample" / "sample.png"
    assert burnin.render_sample(runner, L(box=True, logo=str(LOGO)), frame, "Sådan ser underteksterne ud", out)
    assert Image.open(out).size == (640, 360)
    frame2 = TMP / "sample2" / "frame.png"
    frame2.parent.mkdir()
    assert burnin.sample_frame(runner, None, frame2, 640) and Image.open(frame2).size == (640, 360)
    assert burnin.render_sample(runner, L(), frame2, "Æble øl å", frame2.parent / "s.png")

if "--keep" in sys.argv:
    print("kept", TMP)
else:
    shutil.rmtree(TMP, ignore_errors=True)
print("all ok")
