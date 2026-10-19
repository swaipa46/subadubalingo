"""Answers from any AI chat, not only Claude (version 2.2.0): ChatGPT, Gemini and others ignore parts of the asked-for
format. The parser must still read them, and the prompt must stand on its own (no skill, no product name).
Run: python tests/test_chat_answers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import claude  # noqa: E402
from subadubalingo.common import Cue, format_srt  # noqa: E402

part = [Cue(1000, 3000, "Velkommen til memberkær."), Cue(3100, 5000, "Det er nyt.")]
da = [Cue(1000, 3000, "Velkommen til Membercare."), Cue(3100, 5000, "Det er nyt.")]
sv = [Cue(1000, 3000, "Välkommen till Membercare."), Cue(3100, 5000, "Det är nytt för större företag.")]
no = [Cue(1000, 3000, "Velkommen til Membercare."), Cue(3100, 5000, "Det er nytt.")]
codes = ["da", "sv", "no"]
srt = lambda c: format_srt(c).rstrip()
WORDS = [("memberkær", "Membercare")]
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def read(text, want_words=True, cs=codes):
    have, words, msg = claude.check_answer(part, cs, text)
    good = msg is None and all(have[c][-1].text == {"da": da, "sv": sv, "no": no}[c][-1].text for c in cs)
    good &= have.get("da", da)[0].text == "Velkommen til Membercare." if "da" in cs else True
    return good and (words == WORDS if want_words else True), msg

# --- Shapes seen from chat models --------------------------------------------------------------------------------

cases = {
    "ChatGPT: markdown headings, a srt block per language, prose around it": (
        "Here are the corrected subtitles and the translations.\n\n"
        f"### Danish (corrected)\n```srt\n{srt(da)}\n```\n\n### Swedish\n```srt\n{srt(sv)}\n```\n\n"
        f"### Norwegian\n```\n{srt(no)}\n```\n\n### Corrected words\n- memberkær → Membercare\n\n"
        "Let me know if you need anything else!", True),
    "Gemini: bold labels with codes, no words section": (
        f"**Danish (da):**\n```\n{srt(da)}\n```\n\n**Swedish (sv):**\n```\n{srt(sv)}\n```\n\n"
        f"**Norwegian (no):**\n```\n{srt(no)}\n```\n", False),
    "numbered headings of several words": (
        f"## 1. Corrected Danish text\n```\n{srt(da)}\n```\n## 2. Swedish translation\n```\n{srt(sv)}\n```\n"
        f"## 3. Norwegian translation\n```\n{srt(no)}\n```\n## 4. Words I corrected\n```\nmemberkær => Membercare\n```",
        True),
    "markers with English names": (
        f"```\n=== Danish ===\n{srt(da)}\n\n=== Swedish ===\n{srt(sv)}\n\n=== Norwegian ===\n{srt(no)}\n\n"
        "=== words ===\nmemberkær -> Membercare\n```", True),
    "one block per language, unlabelled, in the asked order": (
        f"```srt\n{srt(da)}\n```\n\n```srt\n{srt(sv)}\n```\n\n```srt\n{srt(no)}\n```", False),
    "bold codes alone": (
        f"**da**\n```\n{srt(da)}\n```\n**sv**\n```\n{srt(sv)}\n```\n**no**\n```\n{srt(no)}\n```\n"
        "**words**\n```\nmemberkær => Membercare\n```", True),
    "the asked-for block without the trailing words section": (
        f"```\n=== da ===\n{srt(da)}\n\n=== sv ===\n{srt(sv)}\n\n=== no ===\n{srt(no)}\n```", False),
}
for name, (text, want_words) in cases.items():
    good, msg = read(text, want_words)
    check(good, f"{name} | {msg}")

# Chat text after a closing fence is not taken into the last subtitle
text = f"### Norwegian\n```\n{srt(no)}\n```\nI kept the time codes as they were.\nHope this helps."
have, _, msg = claude.check_answer(part, ["no"], text)
check(msg is None and have["no"][-1].text == "Det er nytt.", "prose after a closing fence stays out of the subtitles")

# A sentence that names a language is not a heading (summary text)
text = (f"```\n=== da ===\n{srt(da)}\n=== summary ===\nA webinar in Danish about Membercare.\n\n"
        "Swedish and Norwegian versions follow.\n=== words ===\nmemberkær => Membercare\n```")
o = claude.answer_outline(text)
have, words, msg = claude.check_answer(part, ["da"], text)
check(msg is None and o.summary.startswith("A webinar in Danish") and "Swedish and Norwegian" in o.summary
      and words == WORDS, "a language name in a summary sentence is text")

# Cuts and chapters under markdown headings
text = (f"### Danish\n```\n{srt(da)}\n```\n### Cut suggestions\n```\n1-2 start Folk kommer ind\n```\n"
        "### Chapters\n- 0:00 Intro\n- 0:30 Demo\n- 1:10 Spørgsmål\n\n### Summary\nKort om det.\n")
check(claude.answer_cuts(text) == ["1-2 start Folk kommer ind"] and len(claude.answer_outline(text).chapters) == 3
      and claude.answer_outline(text).summary == "Kort om det.", "cuts, chapters and summary under headings")

# A heading naming two languages decides nothing (no guess)
check(claude._heading_label("### Translation from Danish to Swedish") is None, "an ambiguous heading is no marker")
check(claude._heading_label("**Swedish translation (sv):**") == "sv", "a code in parentheses decides")
check(claude._heading_label("00:00:01,000 --> 00:00:03,000") is None, "a time line is no heading")

# --- The prompt stands on its own ---------------------------------------------------------------------------------
L = claude.LANG_BY_CODE
for args in ((L["da"], True, [L["sv"], L["no"]], part, 1, 0, 1, True, L["sv"]),
             (L["da"], False, [L["en"]], part, 1, 1, 2, False, None)):
    p = claude.prompt(*args)
    low = p.lower()
    check("claude" not in low and "skill" not in low and "chatgpt" not in low and "gemini" not in low,
          "the prompt names no skill and no product")
    check("Keep the numbering and time codes exactly" in p and "=== words ===" in p and p.count("-->") >= 2
          and "ONE code block" in p, "the prompt carries the rules, the format and the subtitles")
p = claude.prompt(L["da"], False, [L["en"]], part, 1, 0, 1)
check("Only correct what was misheard" not in p and "=== en ===" in p, "no correcting rule when only translating")

print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
