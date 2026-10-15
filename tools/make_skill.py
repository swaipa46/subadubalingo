"""Write the Claude skill zip with the program's default word list (for the release folder).

The user gets their own, growing word list into the skill with Indstillinger -> "Gem Claude-skill...".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from vu import claude, engine  # noqa: E402

rules = []
for line in engine.DEFAULT_CORRECTIONS.splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        w, r = (x.strip() for x in line.split("=", 1))
        rules.append((w, r))
claude.word_rules = lambda: rules
print(claude.skill_zip(Path(sys.argv[1])))
