"""The UI texts (assets/locales/<code>/<area>.json): every area has the same keys in da, sv, no and en, the texts of the
removed Claude skill and file round are gone, and the Danish texts say "AI-assistent"/"AI-chat" wherever any assistant
is meant: "Claude" only where it really is Claude (version 2.2.0). Run: python tests/test_locales.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "assets" / "locales"
CODES = ("da", "sv", "no", "en")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


texts: dict[str, dict[str, dict]] = {}
for code in CODES:
    for f in sorted((LOCALES / code).glob("*.json")):
        texts.setdefault(f.stem, {})[code] = json.loads(f.read_text(encoding="utf-8-sig"))

for area, by_code in sorted(texts.items()):
    missing = [c for c in CODES if c not in by_code]
    check(not missing, f"{area}: a file in every language" + (f" (missing {missing})" if missing else ""))
    if missing:
        continue
    da = set(by_code["da"])
    diff = {c: sorted(set(by_code[c]) ^ da) for c in CODES[1:] if set(by_code[c]) != da}
    check(not diff, f"{area}: the same keys as da" + (f" {diff}" if diff else ""))
    # The same {placeholders} in every language (stat_speech: the code passes both, Swedish words it differently)
    bad = [(c, k) for c in CODES[1:] for k in da & set(by_code[c]) if k != "gui.done.stat_speech"
           if set(re.findall(r"{(\w+)}", by_code[c][k])) != set(re.findall(r"{(\w+)}", by_code["da"][k]))]
    check(not bad, f"{area}: the same placeholders" + (f" {bad}" if bad else ""))

every = {k: v for by_code in texts.values() for k, v in by_code["da"].items()}
gone = [k for k in every if k.startswith("gui.skill.") or k in (
    "file.task", "file.answer", "gui.settings.save_skill", "gui.settings.skill_saved", "mcp.button.save_skill",
    "gui.claude.to_file", "gui.claude.to_parts", "gui.claude.copy_file", "gui.claude.save_continue",
    "gui.claude.step_download", "claude.answer_no_end", "gui.parts.all_ready")]
check(not gone, "no texts of the skill or the file round" + (f" {gone}" if gone else ""))
check(not [k for k, v in every.items() if re.search(r"til Claude\.txt|fra Claude\.txt|skill", v, re.I)
           and not k.startswith("oldskill.")], "no task/answer files or skills in the Danish texts")

# "Claude" only where it is Claude: connecting Claude desktop, the MCP client list, the installer's Claude desktop
# texts, and the one-time notice about the old skill in Claude
ALLOW = ("gui.settings.connect", "mcpclients.", "installer.", "oldskill.")
claude = sorted(k for k, v in every.items() if "Claude" in v and not k.startswith(ALLOW))
check(not claude, "no \"Claude\" in Danish texts meant for any assistant" + (f" {claude}" if claude else ""))

print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
