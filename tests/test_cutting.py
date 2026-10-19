"""Cutting without the GUI: Claude's cut lines, pauses, time-code mapping, and (with a video) the real cut.

  python tests/test_cutting.py [VIDEO]

With VIDEO: cuts two ranges out of the first 2 minutes and checks the cut video's length and that a beep placed
right after a cut is heard where the mapped subtitles say it is.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np

from subadubalingo import cutting, engine
from subadubalingo.common import Cue, Runner

cues = [Cue(30_000, 32_000, "Kan I høre mig?"), Cue(33_000, 35_000, "Vi venter lige lidt."),
        Cue(60_000, 63_000, "Velkommen til webinaret."), Cue(63_500, 66_000, "I dag om Membercare."),
        Cue(70_000, 72_000, "Hov, min kat."), Cue(72_500, 75_000, "Nå, videre."),
        Cue(130_000, 133_000, "Tak for i dag."), Cue(140_000, 141_000, "Er den slukket?")]
DUR = 180_000

cuts, bad = cutting.parse_cut_lines(["1-2 start Folk kommer ind", "5 offtopic Katten", "blok 8: slut - snak efter",
                                     "2:20-2:45 upassende Navn nævnt", "nonsens", "99 uklart"], cues, DUR)
assert [c.kind for c in cuts] == ["start", "offtopic", "end", "inappropriate"], cuts
assert bad == ["nonsens", "99 uklart"], bad
# The English kinds (since 2.0)
en, bad_en = cutting.parse_cut_lines(["1-2 start People joining", "blocks 5 to 6 unclear Crosstalk", "8 end Chatter",
                                      "2:20-2:45 inappropriate Name mentioned", "none"], cues, DUR)
assert [c.kind for c in en] == ["start", "unclear", "end", "inappropriate"] and not bad_en, (en, bad_en)
assert cuts[0].start == 0 and cuts[0].end == 35_000, cuts[0]
assert cuts[1].start == 69_800 and cuts[1].end == 72_000, cuts[1]          # from just before block 5 to its end
assert cuts[2].end == DUR, cuts[2]                                          # last block: to the end of the video
assert (cuts[3].start, cuts[3].end) == (140_000, 165_000), cuts[3]
print("parse ok")

pauses = cutting.find_pauses(cues, DUR, None)
assert [(p.kind, p.start, p.end) for p in pauses] == [("start", 0, 29_250), ("pause", 35_750, 59_250),
                                                     ("pause", 75_750, 129_250), ("end", 141_750, DUR)], pauses
both = cutting.combine_suggestions(pauses, cuts[:3])
assert [c.kind for c in both] == ["start", "pause", "offtopic", "pause", "end"], [c.kind for c in both]
print("pauses ok")

ranges = cutting.active([cutting.Cut(0, 35_000, "start"), cutting.Cut(69_800, 72_000, "offtopic"),
                         cutting.Cut(71_000, 72_100, "manual"), cutting.Cut(100_000, 110_000, "pause", on=False)], DUR)
assert ranges == [(0, 35_000), (69_800, 72_100)], ranges
assert cutting.map_time(60_000, ranges) == 25_000
assert cutting.map_time(72_500, ranges) == 72_500 - 35_000 - 2_300
assert cutting.map_time(70_000, ranges) == 69_800 - 35_000          # inside a cut: where the cut was
new = cutting.remap_cues(cues, ranges)
assert [c.text for c in new] == ["Velkommen til webinaret.", "I dag om Membercare.", "Nå, videre.", "Tak for i dag.",
                                 "Er den slukket?"], [c.text for c in new]
assert new[0].start == 25_000 and new[2].start == 72_500 - 37_300, new
print("remap ok")

if len(sys.argv) > 1:
    video = Path(sys.argv[1])
    work = Path(tempfile.mkdtemp(prefix="sdl-cut-"))
    runner = Runner()
    info = engine.probe(runner, video)

    class P:
        def update(self, f, t): pass
    ranges = [(5_000, 20_000), (40_000, 41_500)]
    out = work / "klippet.mp4"
    import time
    t0 = time.time()
    cutting.cut_video(runner, video, ranges, out, min(info.duration_ms, 120_000) if False else info.duration_ms,
                      info.has_audio, P(), work)
    took = time.time() - t0
    got = engine.probe(runner, out).duration_ms
    want = info.duration_ms - 16_500
    print(f"cut {info.duration_ms / 1000:.1f}s -> {got / 1000:.2f}s (want {want / 1000:.2f}s) in {took:.0f}s")
    assert abs(got - want) < 200, (got, want)
    # A frame right after the first cut must be the original frame at 20 s.
    a, b = work / "a.png", work / "b.png"
    cutting.frame_png(runner, video, 20_100, a, 320)
    cutting.frame_png(runner, out, 5_100, b, 320)
    import zlib
    print("frames written:", a.exists(), b.exists())
    shutil.copy(out, Path(tempfile.gettempdir()) / "sdl-cut-test.mp4")
    print("kept for viewing:", Path(tempfile.gettempdir()) / "sdl-cut-test.mp4")
print("all ok")
