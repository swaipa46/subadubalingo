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
# The skill text and the number the program expects must agree
assert f"skill={claude.SKILL_VERSION}" in claude.SKILL_MD and f"Skill-version: {claude.SKILL_VERSION}" in claude.SKILL_MD
assert claude.answer_skill(f"{claude.ANSWER_HEADER} id=x skill={claude.SKILL_VERSION}\n") == claude.SKILL_VERSION
assert claude.answer_skill(f"{claude.ANSWER_HEADER} id=x\n") is None
print("OK   skill version")

sys.exit(0 if ok else 1)
