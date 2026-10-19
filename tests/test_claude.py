"""Filler cuts and chapters (feature "claude"), without the GUI. Run: python tests/test_claude.py

- the cut kind "filler": its aliases, short blocks cut cleanly (into the silence around them, never into the next
  word), and filler cuts with a word between them are not merged into one;
- chapters: reading Claude's lines, YouTube's rules (tidy), moving the times after a cut, the file's text and name in
  every UI language;
- the MCP tools: get_text asks for chapters, save_chapters stores them, and the job is only complete with them.
The data folder is a temp folder (never ~/.subadubalingo).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-claude-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
from subadubalingo import chapters, claude, cutting, i18n, jobs, mcp_server  # noqa: E402
from subadubalingo.common import Cue  # noqa: E402
from subadubalingo.chapters import Chapter, Outline  # noqa: E402

# --- Filler ---------------------------------------------------------------------------------------
assert "filler" in cutting.KINDS and cutting.KINDS["filler"][1].startswith("#")
for word in ("filler", "fyldord", "utfyllingsord", "utfyllnadsord", "fyllord", "øh"):
    assert cutting.KIND_ALIASES[word] == "filler", word
for code in i18n.CODES:
    assert i18n.has("cutting.kind.filler", code), code
cues = [Cue(0, 2000, "Velkommen."), Cue(2100, 2300, "Øh."), Cue(2400, 4000, "Vi skal se på det."),
        Cue(4050, 4250, "Øhm."), Cue(4300, 6000, "Det er nyt.")]
cuts, bad = cutting.parse_cut_lines(["2 filler Øh", "4 fyldord Øhm"], cues, 6000)
assert not bad and [c.kind for c in cuts] == ["filler", "filler"], (cuts, bad)
a, b = cuts[0].start, cuts[0].end
# 200 ms of "Øh" grows to 300 ms, but only into the silence between the blocks around it
assert b - a >= cutting.MIN_CUT_MS and a >= cues[0].end and b <= cues[2].start, (a, b)
a, b = cuts[1].start, cuts[1].end
assert a >= cues[2].end and b <= cues[4].start, (a, b)
ranges = cutting.active(cuts, 6000)
assert len(ranges) == 2, ranges   # the word between them stays
# A filler cut right next to another cut is still joined when they touch
touch = cutting.merge([cutting.Cut(1000, 2000, "filler"), cutting.Cut(2010, 3000, "offtopic")])
assert len(touch) == 1, touch
# Other kinds still join over a small gap
assert len(cutting.merge([cutting.Cut(1000, 2000, "pause"), cutting.Cut(2300, 3000, "offtopic")])) == 1
# The remapped subtitles: the filler blocks go, the rest keep their words
kept = cutting.remap_cues(cues, ranges)
assert [c.text for c in kept] == ["Velkommen.", "Vi skal se på det.", "Det er nyt."], kept
assert all(k.end - k.start >= 300 for k in kept)
assert "filler" in " ".join(claude.cut_rules()) and "filler" in mcp_server.TOOLS[[t["name"] for t in mcp_server.TOOLS].index("suggest_cuts")]["description"]
print("filler ok")

# --- Chapters ---------------------------------------------------------------------------------------
lines = ["0:00 Velkommen", "1:05 - Demo", "* 00:02:30,000 | Spørgsmål", "[1:02:03] Lang", "  ", "ingen tid her", "3:00"]
got, bad = chapters.parse_chapter_lines(lines)
assert [(c.start, c.title) for c in got] == [(0, "Velkommen"), (65_000, "Demo"), (150_000, "Spørgsmål"),
                                              (3_723_000, "Lang")], got
assert bad == ["ingen tid her", "3:00"], bad
# tidy: first at 0:00, at least 10 s apart, inside the video, at least three
t = chapters.tidy([Chapter(4000, "A"), Chapter(30_000, "B"), Chapter(35_000, "B2"), Chapter(80_000, "C"),
                   Chapter(500_000, "after the end")], duration=120_000)
assert [(c.start, c.title) for c in t] == [(0, "A"), (30_000, "B"), (80_000, "C")], t
assert chapters.tidy([Chapter(0, "A"), Chapter(30_000, "B")], 120_000) == []   # two are not enough for YouTube
# Times move with the cuts: 20-40 s cut away; a chapter inside the cut starts where the cut was
o = Outline([Chapter(0, "A"), Chapter(30_000, "B"), Chapter(60_000, "C"), Chapter(90_000, "D")], "Om videoen.")
m = chapters.remap(o, [(20_000, 40_000)], 100_000)
assert [(c.start, c.title) for c in m.chapters] == [(0, "A"), (20_000, "B"), (40_000, "C"), (70_000, "D")], m
assert m.summary == "Om videoen."
assert chapters.remap(o, [], 100_000) is o
# The file: description, blank line, the chapters (hours on every line once the video is an hour long)
assert chapters.file_text(o) == "Om videoen.\n\n0:00 A\n0:30 B\n1:00 C\n1:30 D\n", chapters.file_text(o)
h = Outline([Chapter(0, "A"), Chapter(1_800_000, "B"), Chapter(3_700_000, "C")])
assert chapters.chapters_text(h) == "0:00:00 A\n0:30:00 B\n1:01:40 C"
assert chapters.file_text(Outline([], "Kun en beskrivelse.")) == "Kun en beskrivelse.\n"
assert not Outline() and Outline([], "x")
assert Outline.from_json(o.to_json()) == o and Outline.from_json(None) == Outline()
# The file name in every UI language, recognised in any; saving replaces one made in another language
video = tmp / "Webinar.mp4"
names = {}
for code in i18n.CODES:
    i18n.set_language(code)
    names[code] = chapters.chapters_name(video).name
    for key in ("chapters.title", "chapters.copy_chapters", "chapters.copy_summary", "chapters.hint", "chapters.task"):
        assert i18n.has(key, code), (key, code)
assert names["da"] == "Webinar - kapitler.txt" and names["en"] == "Webinar - chapters.txt", names
assert all(chapters.is_chapters_file(tmp / n) for n in names.values())
assert not chapters.is_chapters_file(tmp / "Webinar - noter.txt")
i18n.set_language("en")
chapters.save(video, o)
assert chapters.find_chapters(video).name == "Webinar - chapters.txt"
i18n.set_language("da")
out = chapters.save(video, o)
assert out.name == "Webinar - kapitler.txt" and not (tmp / "Webinar - chapters.txt").exists()
assert out.read_text(encoding="utf-8") == chapters.file_text(o)
assert chapters.save(video, Outline()) is None
print("chapters ok")

# --- MCP ---------------------------------------------------------------------------------------------
job = "testjob1"
src = [Cue(i * 20_000, i * 20_000 + 3000, f"Blok {i + 1}.") for i in range(9)]
d = jobs.job_dir(job)
jobs.write_json(d / "source.json", jobs.cues_to_json(src))
jobs.write_json(d / "task.json", {"src": "da", "check": True, "targets": [], "codes": ["da"], "blocks": len(src),
                                  "words": [], "cut": False, "duration": 180_000, "chapters": "sv", "rules": []})
text = mcp_server.tool_get_text(job)
assert "save_chapters" in text and "in Swedish" in text and "chapters and summary (save_chapters)" in text, text
r = mcp_server.tool_save_text(job, "da", common.format_srt(src))
assert "Still missing: chapters and summary" in r and not (d / "complete.json").exists(), r
r = mcp_server.tool_save_chapters(job, "0:00 Välkommen\n1:00 Demo\n2:20 Frågor\nfel rad", "Ett webbinarium.")
assert "Saved 3 chapters" in r and "fel rad" in r and (d / "complete.json").exists(), r
done = jobs.read_json(d / "complete.json")
o = Outline.from_json(done["outline"])
assert [c.title for c in o.chapters] == ["Välkommen", "Demo", "Frågor"] and o.summary == "Ett webbinarium.", o
# A job that does not ask for chapters is complete without them, and save_chapters says so
job2 = "testjob2"
d2 = jobs.job_dir(job2)
jobs.write_json(d2 / "source.json", jobs.cues_to_json(src))
jobs.write_json(d2 / "task.json", {"src": "da", "check": True, "targets": [], "codes": ["da"], "blocks": len(src),
                                   "words": [], "cut": False, "duration": 180_000, "rules": []})
assert "save_chapters" not in mcp_server.tool_get_text(job2).split("Rules:")[0]
mcp_server.tool_save_text(job2, "da", common.format_srt(src))
assert (d2 / "complete.json").exists() and jobs.read_json(d2 / "complete.json")["outline"] is None
assert "does not ask" in mcp_server.tool_save_chapters(job2, "0:00 A")
assert "save_chapters" in [t["name"] for t in mcp_server.TOOLS] and "save_chapters" in mcp_server.HANDLERS
print("mcp ok")
print("all ok")
