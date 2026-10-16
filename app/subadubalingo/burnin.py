"""How burned-in subtitles look (Indstillinger -> "Indbrændte undertekster"): text size, top or bottom, a background
box or an outline, and an optional logo in a corner. engine.encode asks video_filter() for the ffmpeg -vf chain;
the settings dialog shows a live sample made with the same chain (render_sample).

The subtitles are an ASS style given to ffmpeg's subtitles filter as force_style. Font sizes are in libass' script
units (an .srt is laid out on 288 lines and scaled to the video), so a size looks the same on every resolution.
The defaults give exactly the look from before this setting existed (DEFAULT_STYLE)."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .common import FFMPEG, Runner, log

# The look before 2.1: white Arial with a thin black outline, near the bottom
DEFAULT_STYLE = ("FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,"
                 "Outline=1.6,Shadow=0.6,MarginV=28")

# FontSize per choice (script units). The capitals are about 4, 5 and 6.5 % of the video's height (design T8)
SIZES = {"s": 16, "m": 20, "l": 26}
POSITIONS = ("bottom", "top")
CORNERS = ("tl", "tr", "bl", "br")
LOGO_WIDTH = 0.07                                # the logo's width as a share of the video's width
LOGO_MARGIN = 0.03                               # space from the edges, as a share of the video's width
BOX_COLOUR = "&H47000000"                        # black at 72 % opacity (ASS alpha: 00 opaque, FF clear)
LOGO_TYPES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


@dataclass
class Look:
    size: str = "m"            # s, m, l
    position: str = "bottom"   # bottom or top
    box: bool = False          # a box behind the text instead of an outline
    logo: str = ""             # an image file, "" for none
    logo_corner: str = "br"    # tl, tr, bl, br

    def __post_init__(self):
        # Settings files are edited by hand now and then; unknown values fall back to the defaults
        if self.size not in SIZES:
            self.size = "m"
        if self.position not in POSITIONS:
            self.position = "bottom"
        if self.logo_corner not in CORNERS:
            self.logo_corner = "br"
        self.box = bool(self.box)
        self.logo = str(self.logo or "")

    @classmethod
    def from_settings(cls, s) -> "Look":
        logo = getattr(s, "burnin_logo", "") if getattr(s, "burnin_logo_on", False) else ""
        return cls(getattr(s, "burnin_size", "m"), getattr(s, "burnin_position", "bottom"),
                   getattr(s, "burnin_box", False), logo, getattr(s, "burnin_logo_corner", "br"))

    def logo_path(self) -> Path | None:
        """The logo file, or None when there is none or it is gone (a missing logo never stops a run)."""
        if not self.logo:
            return None
        p = Path(self.logo)
        if not p.is_file():
            log.warning("burn-in logo not found: %s", p)
            return None
        return p


def force_style(look: Look | None) -> str:
    """The ASS style for ffmpeg's subtitles filter (force_style=...)."""
    look = look or Look()
    parts = ["FontName=Arial", f"FontSize={SIZES[look.size]}", "PrimaryColour=&H00FFFFFF"]
    if look.box:
        # BorderStyle 3 is an opaque box in OutlineColour; Outline is the space around the text
        parts += [f"OutlineColour={BOX_COLOUR}", "BorderStyle=3", "Outline=3", "Shadow=0"]
    else:
        parts += ["OutlineColour=&H00000000", "BorderStyle=1", "Outline=1.6", "Shadow=0.6"]
    parts.append("MarginV=28")
    if look.position == "top":
        # ffmpeg's subtitles filter reads an .srt as an SSA (v4) script, so this is SSA's alignment, not the
        # numpad one: 6 = top centre (1-3 bottom, +4 top, +8 middle). Measured: 8 lands in the middle on the left.
        parts.append("Alignment=6")
    return ",".join(parts)


def _overlay_xy(corner: str, margin: int) -> str:
    x = str(margin) if corner in ("tl", "bl") else f"main_w-overlay_w-{margin}"
    y = str(margin) if corner in ("tl", "tr") else f"main_h-overlay_h-{margin}"
    return f"{x}:{y}"


def filter_chain(look: Look | None, srt_name: str, video_width: int, logo_name: str | None) -> str:
    """The -vf chain: the logo (if any) first, then the subtitles on top of it. File names are relative to ffmpeg's
    working folder: the subtitles and movie filters break on drive colons, backslashes and quotes in paths."""
    look = look or Look()
    subs = f"subtitles={srt_name}:charenc=UTF-8:force_style='{force_style(look)}'"
    if not logo_name:
        return subs
    w = max(2, round(video_width * LOGO_WIDTH) // 2 * 2)
    margin = round(video_width * LOGO_MARGIN)
    return (f"movie={logo_name},scale={w}:-1,format=rgba[logo];"
            f"[in][logo]overlay={_overlay_xy(look.logo_corner, margin)}:format=auto,{subs}")


def video_size(runner: Runner, video: Path) -> tuple[int, int, int]:
    """(width, height, duration in ms) from ffmpeg -i; zeros for what it does not say."""
    _, lines = runner.run([FFMPEG(), "-hide_banner", "-i", video], quiet=True)
    w = h = dur = 0
    for line in lines:
        m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", line)
        if m:
            hh, mi, s, frac = m.groups()
            dur = ((int(hh) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])
        m = re.search(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})", line)
        if m and not w:
            w, h = int(m.group(1)), int(m.group(2))
    return w, h, dur


def video_filter(runner: Runner, look: Look | None, video: Path, workdir: Path, srt_name: str = "s.srt") -> str:
    """The -vf chain for burning srt_name (already in workdir) into video. A logo is copied to workdir first."""
    look = look or Look()
    logo = look.logo_path()
    logo_name = None
    width = 0
    if logo:
        width = video_size(runner, video)[0]
        if width:
            logo_name = "logo" + logo.suffix.lower()
            shutil.copyfile(logo, workdir / logo_name)
        else:
            log.warning("could not read the video's width; burning without the logo")
    return filter_chain(look, srt_name, width, logo_name)


# --- The live sample in Indstillinger ---------------------------------------------------------------

SAMPLE_WIDTH = 640


def sample_srt(text: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:30,000\n{text}\n"


def sample_frame(runner: Runner, video: Path | None, out: Path, width: int = SAMPLE_WIDTH) -> bool:
    """A 16:9 still of `video` a third in (at most 60 s), `width` pixels wide (a portrait video gets bars), as a
    PNG; without a video (or if ffmpeg cannot read it) a calm gradient in the same size, so the sample always has
    something behind the text."""
    height = width * 9 // 16 // 2 * 2
    if video and Path(video).is_file():
        _, _, dur = video_size(runner, Path(video))
        at = min(60.0, dur / 3000) if dur else 0.0
        fit = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
        code, _ = runner.run([FFMPEG(), "-hide_banner", "-y", "-ss", f"{at:.2f}", "-i", video, "-frames:v", "1",
                              "-vf", fit, out], quiet=True)
        if code == 0 and out.exists():
            return True
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-y", "-f", "lavfi", "-i",
                          f"gradients=s={width}x{height}:c0=0x3a6186:c1=0x89253e:c2=0x2b5876:n=3:speed=0:seed=7",
                          "-frames:v", "1", out], quiet=True)
    return code == 0 and out.exists()


def render_sample(runner: Runner, look: Look | None, frame: Path, text: str, out: Path) -> bool:
    """Burn the sample line (and the logo) into the still `frame` (from sample_frame) and write out (PNG)."""
    workdir = frame.parent
    (workdir / "sample.srt").write_text(sample_srt(text), encoding="utf-8")
    vf = video_filter(runner, look, frame, workdir, "sample.srt")
    tmp = workdir / "render.tmp.png"
    code, _ = runner.run([FFMPEG(), "-hide_banner", "-y", "-i", frame.name, "-vf", vf, "-frames:v", "1", tmp.name],
                         cwd=workdir, quiet=True)
    if code == 0 and tmp.exists():
        tmp.replace(out)
        return True
    tmp.unlink(missing_ok=True)
    return False
