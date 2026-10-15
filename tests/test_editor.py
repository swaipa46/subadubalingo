"""The subtitle editor without the GUI: SRT round trip and saving (backup, numbering, text state), split and merge,
moving edges, overlap fixing, the corrections diff and finding the video of a subtitle file.

  python tests/test_editor.py
"""
import copy
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common

tmp = Path(tempfile.mkdtemp(prefix="sdl-editor-test-"))
common._DATA_DIR = tmp / "data"          # never the real ~/.subadubalingo
common._DATA_DIR.mkdir()
from subadubalingo import editor, textstate  # noqa: E402
from subadubalingo.common import LANG_BY_CODE, Cue, format_srt, parse_srt_text, read_srt, srt_name  # noqa: E402

DA = LANG_BY_CODE["da"]
SRT = """1
00:00:02,060 --> 00:00:03,680
Til mit webinar.

2
00:00:03,740 --> 00:00:05,240
Er det ikke bare her, fedt?

3
00:00:08,600 --> 00:00:13,160
Nu skal vi se, om Anne,
hun kan læse undertekster.
"""

try:
    # --- Round trip: parse -> format -> parse gives the same lines
    cues = parse_srt_text(SRT)
    assert len(cues) == 3 and cues[2].text == "Nu skal vi se, om Anne,\nhun kan læse undertekster."
    again = parse_srt_text(format_srt(cues))
    assert [(c.start, c.end, c.text) for c in again] == [(c.start, c.end, c.text) for c in cues]

    # --- Split at the cursor: the time shared by characters, each part wrapped again
    c = cues[2]
    pos = c.text.index("hun")
    a, b = editor.split_cue(c, pos)
    assert a.text == "Nu skal vi se, om Anne," and b.text == "hun kan læse undertekster.", (a, b)
    assert a.start == c.start and b.end == c.end and a.end == b.start
    share = len("Nu skal vi se, om Anne,") / (len("Nu skal vi se, om Anne,") + len("hun kan læse undertekster."))
    assert abs((a.end - c.start) - share * (c.end - c.start)) <= 10, a.end
    assert editor.split_cue(c, 0) is None and editor.split_cue(c, len(c.text)) is None
    assert editor.split_cue(Cue(0, 300, "kort tekst"), 4) is None            # too short to split
    s1, s2 = editor.split_cue(Cue(0, 1000, "a meget lang tekst"), 1)          # a tiny first part still gets MIN_CUE_MS
    assert s1.end - s1.start >= editor.MIN_CUE_MS and s2.end - s2.start >= editor.MIN_CUE_MS

    # --- Merge with the next: from the first start to the later end, wrapped
    m = editor.merge_cues(cues[0], cues[1])
    assert (m.start, m.end) == (2060, 5240)
    assert m.text.replace("\n", " ") == "Til mit webinar. Er det ikke bare her, fedt?", repr(m.text)
    assert m.text.count("\n") == 1 and all(len(x) <= 42 for x in m.text.split("\n"))   # two lines, near the middle
    assert editor.merge_cues(a, b).text.replace("\n", " ") == "Nu skal vi se, om Anne, hun kan læse undertekster."

    # --- Moving an edge: never over a neighbour, never shorter than MIN_CUE_MS
    cs = copy.deepcopy(cues)
    assert editor.set_edge(cs, 1, "start", 1000) == 3680          # stops at the previous line's end
    assert editor.set_edge(cs, 1, "end", 9000) == 8600            # and at the next one's start
    assert editor.set_edge(cs, 1, "end", 0) == cs[1].start + editor.MIN_CUE_MS
    assert editor.set_edge(cs, 2, "end", 99_000, duration=18_000) == 18_000
    assert editor.set_edge(cs, 0, "start", -500) == 0

    # --- Overlap fixing: order, no overlaps, no empty lines, minimum length
    messy = [Cue(5000, 7000, "b"), Cue(1000, 6000, "a"), Cue(5000, 5100, "c"), Cue(8000, 8000, "d"),
             Cue(9000, 9500, "  \n "), Cue(10_000, 12_000, "e\n\n f ")]
    fixed, moved, dropped = editor.fix_overlaps(messy)
    assert dropped == 1 and [x.text for x in fixed] == ["a", "c", "b", "d", "e\nf"], [x.text for x in fixed]
    for x, y in zip(fixed, fixed[1:]):
        assert x.end <= y.start, (x, y)
    assert all(x.end - x.start >= editor.MIN_CUE_MS for x in fixed)
    assert moved >= 3
    assert messy[0].start == 5000 and messy[3].end == 8000          # the input is not changed
    ok, moved, dropped = editor.fix_overlaps(cues)
    assert (moved, dropped) == (0, 0) and [(x.start, x.end) for x in ok] == [(x.start, x.end) for x in cues]
    same, _, _ = editor.fix_overlaps([Cue(1000, 2000, "x"), Cue(1000, 1500, "y"), Cue(1050, 1300, "z")])
    for x, y in zip(same, same[1:]):
        assert x.end <= y.start and x.end - x.start >= editor.MIN_CUE_MS, same
    clamp, _, _ = editor.fix_overlaps([Cue(17_000, 18_400, "slut")], duration=18_000)
    assert clamp[0].end == 18_000

    # --- The corrections diff: misheard words, not rewrites
    before = [Cue(0, 1000, "Vi bruger memberkær til det."), Cue(1000, 2000, "Det er et fløv i Members Care."),
              Cue(2000, 3000, "fedt med flowet")]
    after = [Cue(0, 1000, "Vi bruger Membercare til det."), Cue(1000, 2000, "Det er et flow i Membercare."),
             Cue(2000, 3000, "Fedt med flowet, og noget helt nyt her til sidst")]
    pairs = editor.word_changes(before, after)
    assert pairs == [("memberkær", "Membercare"), ("fløv", "flow"), ("Members Care", "Membercare")], pairs
    assert editor.word_changes(before, after, known={"fløv"}) == [("memberkær", "Membercare"),
                                                                   ("Members Care", "Membercare")]
    # Inside-the-word case changes count (MemberCare -> Membercare), a first letter alone does not
    assert editor.word_changes([Cue(0, 1, "MemberCare er godt")], [Cue(0, 1, "Membercare er godt")]) == \
        [("MemberCare", "Membercare")]
    assert editor.word_changes([Cue(0, 1, "hej med dig")], [Cue(0, 1, "Hej med dig")]) == []
    # A long rewrite is not a word, numbers are not words
    assert editor.word_changes([Cue(0, 1, "a b c d e f")], [Cue(0, 1, "u v w x y z")]) == []
    assert editor.word_changes([Cue(0, 1, "kl 12 i dag")], [Cue(0, 1, "kl 13 i dag")]) == []
    # The same word corrected twice is offered once; a split line does not hide it
    two = editor.word_changes([Cue(0, 1, "memberkær og memberkær")], [Cue(0, 1, "Membercare og"), Cue(1, 2, "Membercare")])
    assert two == [("memberkær", "Membercare")], two
    assert editor.similar("memberkær", "Membercare") and not editor.similar("her", "godt")

    # --- Saving: the first original kept as .bak, numbering from 1, no overlaps, the text state kept current
    folder = tmp / "videos"
    folder.mkdir()
    video = folder / "Møde.mp4"
    video.write_bytes(b"not really a video")
    cut_video = folder / "Møde - klippet.mp4"
    cut_video.write_bytes(b"x")
    srt = srt_name(video, DA)
    srt.write_text(SRT, encoding="utf-8")
    textstate.record(srt, video)
    textstate.set_checked(srt, True)
    assert textstate.status(srt, video) == "ok"
    assert editor.video_of(srt) == video, editor.video_of(srt)
    assert editor.video_of(srt_name(cut_video, DA)) == cut_video
    assert editor.video_of(folder / "noget andet.srt") is None
    assert [(l.code, p) for l, p in editor.srts_for(video)] == [("da", srt)]

    doc = editor.Doc.load(DA, srt)
    assert not doc.dirty
    doc.cues[1].text = "Er det ikke bare godt?"
    doc.cues[1].end = 9000            # now overlaps line 3
    doc.cues.insert(0, Cue(0, 500, " "))   # an emptied line
    assert doc.dirty
    r = editor.save_doc(doc, duration=18_000)
    assert r.backup == editor.backup_path(srt) and r.backup.read_text(encoding="utf-8") == SRT
    assert (r.moved, r.dropped) == (1, 1), r
    saved = read_srt(srt)
    assert [c.text for c in saved] == ["Til mit webinar.", "Er det ikke bare godt?",
                                      "Nu skal vi se, om Anne,\nhun kan læse undertekster."]
    assert saved[1].end == saved[2].start == 8600
    assert srt.read_text(encoding="utf-8").startswith("1\n00:00:02,060") and "\n3\n00:00:08,600" in srt.read_text(encoding="utf-8")
    assert not doc.dirty
    assert textstate.status(srt, video) == "ok" and textstate.is_checked(srt)   # still current, still checked
    assert textstate._load()[textstate._key(srt)]["edited"] is True
    # A second save keeps the first original in the backup
    doc.cues[0].text = "Til mit store webinar."
    editor.save_doc(doc)
    assert editor.backup_path(srt).read_text(encoding="utf-8") == SRT
    assert read_srt(srt)[0].text == "Til mit store webinar."
    # A file the program did not make gets no text state entry
    other = folder / "egen.srt"
    other.write_text(SRT, encoding="utf-8")
    editor.save_doc(editor.Doc.load(None, other))
    assert textstate._key(other) not in textstate._load()

    # --- A session over the video's languages, with no media (the video is not a real one)
    sv = srt_name(video, LANG_BY_CODE["sv"])
    sv.write_text(SRT.replace("Til mit webinar.", "Till mitt webbinarium."), encoding="utf-8")
    s = editor.Session(srt=sv, source=DA)
    try:
        assert s.video == video and [d.lang.code for d in s.docs] == ["da", "sv"] and s.doc.path == sv
        assert s.source_doc().lang.code == "da" and not s.dirty
    finally:
        s.close()
    print("all ok")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
