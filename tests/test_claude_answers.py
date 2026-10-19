"""The chat's answer can arrive in several shapes (ChatGPT, Gemini, Claude, ...); all of them must be read, and the
prompt must work in any chat. Run: python tests/test_claude_answers.py"""
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
    "the asked-for format": f"```\n=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== words ===\nmemberkær => Membercare\n```",
    "one block with markers": f"```\n=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== ordliste ===\nmemberkær => Membercare\n```",
    "one block, copied without fences": f"=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n\n=== ordliste ===\nmemberkær => Membercare",
    "labelled fences": f"```srt-da\n{srt(da)}\n```\n\n```srt-sv\n{srt(sv)}\n```\n\n```srt-no\n{srt(no)}\n```\n\n```ordliste\nmemberkær => Membercare\n```",
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
# Chapters and a summary (optional sections) and filler cuts; an answer without them is fine too
long = [Cue(i * 20_000, i * 20_000 + 3000, f"Blok {i + 1}.") for i in range(12)]   # 4 minutes
lsrt = srt(long)
text6 = (f"```\n=== da ===\n{lsrt}\n=== cuts ===\n3 filler Øh, øhm\n"
         "=== chapters ===\n0:00 Velkommen\n00:01:00,000 Sådan opretter du et medlem\n- 2:20 - Spørgsmål\n"
         "=== summary ===\nEt webinar om Membercare.\n\nTo afsnit.\n=== words ===\nmemberkær => Membercare\n```\n")
have, words, msg = claude.check_answer(long, ["da"], text6)
o = claude.answer_outline(text6, 240_000)
good = (msg is None and words == [("memberkær", "Membercare")] and claude.answer_cuts(text6) == ["3 filler Øh, øhm"]
        and [(c.start, c.title) for c in o.chapters] == [(0, "Velkommen"), (60_000, "Sådan opretter du et medlem"),
                                                         (140_000, "Spørgsmål")]
        and o.summary == "Et webinar om Membercare.\n\nTo afsnit." and len(have["da"]) == 12)
ok &= good
print(("OK  " if good else "FAIL"), "answer with chapters, summary, filler |", msg, o)
text5 = f"=== da ===\n{lsrt}\n=== cuts ===\n=== words ===\n"
have, _, msg = claude.check_answer(long, ["da"], text5)
o = claude.answer_outline(text5)
good = msg is None and not o and len(have["da"]) == 12 and claude.answer_cuts(text5) == []
ok &= good
print(("OK  " if good else "FAIL"), "answer without chapters |", msg)
# The chat answer (parts mode) with chapters in the block and Danish section names
chat = (f"```\n=== da ===\n{srt(da)}\n\n=== kapitler ===\n0:00 Intro\n0:30 Demo\n1:10 Slut\n\n=== beskrivelse ===\n"
        "Kort om det.\n\n=== ordliste ===\nmemberkær => Membercare\n```")
have, words, msg = claude.check_answer(part, ["da"], chat)
o = claude.answer_outline(chat)
good = msg is None and len(o.chapters) == 3 and o.summary == "Kort om det." and words == [("memberkær", "Membercare")]
ok &= good
print(("OK  " if good else "FAIL"), "chat answer with kapitler/beskrivelse |", msg)
# The prompts ask for chapters only when told, in the given language
L = claude.LANG_BY_CODE
p1 = claude.prompt(L["da"], True, [], part, 1, 0, 2, chapters=L["da"])
p2 = claude.prompt(L["da"], True, [], part, 1, 1, 2, chapters=L["da"])
good = "=== chapters ===" in p1 and "=== summary ===" not in p1.split("Example of the format")[1] and "=== summary ===" in p2
ok &= good
print(("OK  " if good else "FAIL"), "prompts ask for chapters")

sys.exit(0 if ok else 1)
