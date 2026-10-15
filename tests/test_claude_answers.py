"""Claude's answer can arrive in several shapes; all of them must be read. Run: python tests/test_claude_answers.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import claude
from subadubalingo.common import Cue, format_srt

part = [Cue(1000, 3000, "Velkommen til memberkær."), Cue(3100, 5000, "Det er nyt.")]
da = [Cue(c.start, c.end, c.text.replace("memberkær", "Membercare")) for c in part]
sv = [Cue(1000, 3000, "Välkommen till Membercare, här är det härligt och nöjt."), Cue(3100, 5000, "Det är nytt för större företag.")]
no = [Cue(1000, 3000, "Velkommen til Membercare."), Cue(3100, 5000, "Det er nytt.")]
codes = ["da", "sv", "no"]
srt = lambda c: format_srt(c).rstrip()

cases = {
    "English protocol (skill 4)": f"```\n=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== words ===\nmemberkær => Membercare\n```",
    "one block with markers": f"```\n=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== ordliste ===\nmemberkær => Membercare\n```",
    "one block, copied without fences": f"=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== ordliste ===\nmemberkær => Membercare",
    "labelled fences (old skill)": f"```srt-da\n{srt(da)}\n```\n\n```srt-sv\n{srt(sv)}\n```\n\n```srt-no\n{srt(no)}\n```\n\n```ordliste\nmemberkær => Membercare\n```",
    "headings + plain fences": f"**Dansk (rettet)**\n```srt\n{srt(da)}\n```\n### Svensk\n```\n{srt(sv)}\n```\nNorsk:\n```\n{srt(no)}\n```\n**Ordliste**\n```\nmemberkær → Membercare\n```",
}
ok = True
for name, text in cases.items():
    have, words, msg = claude.check_answer(part, codes, text)
    good = msg is None and have["da"][0].text.endswith("Membercare.") and have["sv"][0].text.startswith("Välkommen") \
        and words == [("memberkær", "Membercare")]
    ok &= good
    print(("OK  " if good else "FAIL"), name, "|", msg, words)

# A subtitle line that just says a language name is text, not a marker
no2 = [Cue(1000, 3000, "Hvilket sprog?\nNorsk"), Cue(3100, 5000, "Det er nytt.")]
have, _, msg = claude.check_answer(part, ["no"], f"=== no ===\n{srt(no2)}")
good = msg is None and have["no"][0].text.endswith("Norsk")
ok &= good
print(("OK  " if good else "FAIL"), "language word inside a subtitle |", msg)

# Three bare blocks pasted one at a time (each code block's own copy button, in shown order)
have = {}
for i, block in enumerate([da, sv, no]):
    have, words, msg = claude.check_answer(part, codes, srt(block), have)
    print("     paste", i + 1, sorted(have), "|", msg)
good = msg is None and have["sv"][0].text.startswith("Välkommen") and have["no"][0].text == "Velkommen til Membercare."
ok &= good
print(("OK  " if good else "FAIL"), "bare blocks one at a time")

# Swedish pasted first is still recognised as Swedish
have, _, msg = claude.check_answer(part, codes, srt(sv))
good = list(have) == ["sv"] and "dansk" in msg
ok &= good
print(("OK  " if good else "FAIL"), "bare Swedish first |", msg)

# Truncated answer is reported
have, _, msg = claude.check_answer(part, codes, f"=== da ===\n{srt(da)}\n=== sv ===\n{srt(sv[:1])}")
good = msg is not None and "Svensk" in msg
ok &= good
print(("OK  " if good else "FAIL"), "truncated |", msg)
# The answer file: the new protocol and the Danish one of the app's earlier name are both read
for header, cuts, end in ((claude.ANSWER_HEADER, "cuts", "end"), ("VIDEOSPROG-SVAR", "klip", "slut")):
    text = (f"{header} id=job1 skill=4\n=== da ===\n{srt(da)}\n=== sv ===\n{srt(sv)}\n=== no ===\n{srt(no)}\n"
            f"=== {cuts} ===\n1-2 start Folk kommer ind\n=== words ===\nmemberkær => Membercare\n=== {end} ===\n")
    have, words, msg = claude.check_file_answer(part, codes, text, cut=True)
    good = (msg is None and claude.answer_id(text) == "job1" and claude.answer_cuts(text) == ["1-2 start Folk kommer ind"]
            and words == [("memberkær", "Membercare")])
    ok &= good
    print(("OK  " if good else "FAIL"), f"answer file {header} |", msg)
_, _, msg = claude.check_file_answer(part, codes, f"{claude.ANSWER_HEADER} id=j\n=== da ===\n{srt(da)}\n=== sv ===\n{srt(sv)}\n=== no ===\n{srt(no)}\n")
good = msg is not None and "end" in msg
ok &= good
print(("OK  " if good else "FAIL"), "answer file without === end === |", msg)
# Skill 6: chapters and a summary (optional sections), and filler cuts; a version 5 answer has neither and is fine
long = [Cue(i * 20_000, i * 20_000 + 3000, f"Blok {i + 1}.") for i in range(12)]   # 4 minutes
lsrt = srt(long)
text6 = (f"{claude.ANSWER_HEADER} id=job6 skill=6\n=== da ===\n{lsrt}\n=== cuts ===\n3 filler Øh, øhm\n"
         "=== chapters ===\n0:00 Velkommen\n00:01:00,000 Sådan opretter du et medlem\n- 2:20 - Spørgsmål\n"
         "=== summary ===\nEt webinar om Membercare.\n\nTo afsnit.\n=== words ===\nmemberkær => Membercare\n=== end ===\n")
have, words, msg = claude.check_file_answer(long, ["da"], text6, cut=True)
o = claude.answer_outline(text6, 240_000)
good = (msg is None and words == [("memberkær", "Membercare")] and claude.answer_cuts(text6) == ["3 filler Øh, øhm"]
        and [(c.start, c.title) for c in o.chapters] == [(0, "Velkommen"), (60_000, "Sådan opretter du et medlem"),
                                                         (140_000, "Spørgsmål")]
        and o.summary == "Et webinar om Membercare.\n\nTo afsnit." and len(have["da"]) == 12)
ok &= good
print(("OK  " if good else "FAIL"), "skill 6 answer with chapters, summary, filler |", msg, o)
text5 = f"{claude.ANSWER_HEADER} id=job5 skill=5\n=== da ===\n{lsrt}\n=== cuts ===\n=== words ===\n=== end ===\n"
have, _, msg = claude.check_file_answer(long, ["da"], text5, cut=True)
o = claude.answer_outline(text5)
good = msg is None and not o and len(have["da"]) == 12
ok &= good
print(("OK  " if good else "FAIL"), "skill 5 answer without chapters |", msg)
# The chat answer (parts mode) with chapters in the block and Danish section names
chat = (f"```\n=== da ===\n{srt(da)}\n\n=== kapitler ===\n0:00 Intro\n0:30 Demo\n1:10 Slut\n\n=== beskrivelse ===\n"
        "Kort om det.\n\n=== ordliste ===\nmemberkær => Membercare\n```")
have, words, msg = claude.check_answer(part, ["da"], chat)
o = claude.answer_outline(chat)
good = msg is None and len(o.chapters) == 3 and o.summary == "Kort om det." and words == [("memberkær", "Membercare")]
ok &= good
print(("OK  " if good else "FAIL"), "chat answer with kapitler/beskrivelse |", msg)
# The prompts ask for chapters only when told, in the given language; the task file says so in its first line
L = claude.LANG_BY_CODE
wf = claude.work_file_text(L["da"], True, [L["sv"]], part, "j", "a.txt", cut=True, chapters=L["sv"])
good = ("chapters=sv" in wf.split("\n", 1)[0] and "=== chapters ===" in wf and "in Swedish" in wf
        and "filler" in wf)
wf0 = claude.work_file_text(L["da"], True, [], part, "j", "a.txt")
good &= "chapters" not in wf0 and "=== summary ===" not in wf0
p1 = claude.prompt(L["da"], True, [], part, 1, 0, 2, chapters=L["da"])
p2 = claude.prompt(L["da"], True, [], part, 1, 1, 2, chapters=L["da"])
good &= "=== chapters ===" in p1 and "=== summary ===" not in p1.split("Example of the format")[1] and "=== summary ===" in p2
ok &= good
print(("OK  " if good else "FAIL"), "prompts ask for chapters")
# The skill describes the new kind and sections
assert "`filler`" in claude.SKILL_MD and "=== chapters ===" in claude.SKILL_MD and "save_chapters" in claude.SKILL_MD
# The skill text and the number the program expects must agree
assert f"skill={claude.SKILL_VERSION}" in claude.SKILL_MD and f"Skill version: {claude.SKILL_VERSION}" in claude.SKILL_MD
assert f"name: {claude.SKILL_NAME}" in claude.SKILL_MD
assert claude.answer_skill(f"{claude.ANSWER_HEADER} id=x skill={claude.SKILL_VERSION}\n") == claude.SKILL_VERSION
assert claude.answer_skill(f"{claude.ANSWER_HEADER} id=x\n") is None
print("OK   skill version")

sys.exit(0 if ok else 1)
