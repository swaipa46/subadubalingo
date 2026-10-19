"""The one-time "delete the old Claude skill" notice after an update (upgrade_notice.py, version 2.2.0): shown once to
users who come from a version with the skill, never to fresh installs, never in a session an assistant started.
The data folder is a temp folder (never ~/.subadubalingo). Run: python tests/test_upgrade_notice.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-upgrade-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
from subadubalingo import gui, upgrade_notice as un  # noqa: E402

SETTINGS = common._DATA_DIR / "settings.json"
gui.Settings.path = SETTINGS
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def write(data):
    if data is None:
        SETTINGS.unlink(missing_ok=True)
    else:
        SETTINGS.write_text(json.dumps(data), encoding="utf-8")


def start(from_assistant=False):
    """One start of the app as App.__init__ does it: read the settings, decide (and save)."""
    s = gui.Settings()
    return un.decide_at_start(s, from_assistant), s


# A settings file as 2.1.1 wrote it (Settings.save dumped every attribute)
V211 = {"whisper_model": "large", "use_gpu": True, "skill_installed": 6, "theme": "system", "ui_language": "da",
        "update_checked": 1760000000.0}

# --- Who sees it (the pure decision) -------------------------------------------------------------------------------
check(not un.needs_old_skill_notice(None), "no settings file: a fresh install, never")
check(not un.needs_old_skill_notice({"ui_language": "sv"}),
      "only the installer's first-start language: a fresh install, never")
check(un.needs_old_skill_notice(V211), "a 2.1.1 settings file (skill 6, no last_version): shown")
check(un.needs_old_skill_notice(dict(V211, skill_installed=0)), "skill_installed 0 (unknown) and no last_version: shown")
check(un.needs_old_skill_notice({"last_version": "2.1.1"}), "last_version 2.1.1: shown")
check(not un.needs_old_skill_notice({"last_version": un.SKILL_REMOVED_IN}), "last_version without the skill: not shown")
check(not un.needs_old_skill_notice({"last_version": "2.10.0"}), "versions compare as numbers (2.10.0 > 2.2.0)")
check(not un.needs_old_skill_notice(dict(V211, old_skill_notice_done=True)), "done: never again")

# --- Fresh install: never, also not at later starts ------------------------------------------------------------------
write(None)
show, s = start()
saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
check(not show and saved.get("old_skill_notice_done") is True and saved.get("last_version") == common.APP_VERSION,
      "fresh install: not shown, settled and the version recorded at the first start")
show, _ = start()
check(not show, "fresh install: not at the next start either")
write({"ui_language": "no"})
show, _ = start()
check(not show and gui.Settings().ui_language == "no", "fresh install through the installer: not shown, language kept")

# --- Upgrade: once --------------------------------------------------------------------------------------------------
write(V211)
show, s = start()
check(show, "upgrade from 2.1.1: shown at the first start")
saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
check("skill_installed" not in saved and "_saved" not in saved and saved.get("old_skill_notice_pending") is True,
      "save() writes neither _saved nor skill_installed; the notice is pending until shown")
check(s._saved is not None and s._saved.get("skill_installed") == 6 and not hasattr(s, "skill_installed"),
      "the old value is read as an upgrade signal, then dropped as a setting")
show, s = start()
check(show, "not shown yet (e.g. the app was closed at once): still due at the next start")
un.mark_shown(s)
show, _ = start()
check(not show, "shown once: never again")

# --- Started by an assistant: waits for the next normal start -----------------------------------------------------
write(dict(V211, skill_installed=0))
show, _ = start(from_assistant=True)
saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
check(not show and not saved.get("old_skill_notice_done"), "an assistant started the app: not shown, flag unset")
show, _ = start()
check(show, "the next normal start shows it")
check(common.started_by_assistant(["x", "--from-assistant"]) and common.started_by_assistant(["x", "--from-claude"])
      and not common.started_by_assistant(["x"]), "both launch flags count (2.1.x servers pass --from-claude)")

# --- Old settings still load --------------------------------------------------------------------------------------
write(dict(V211, model="turbo", _saved="junk"))
s = gui.Settings()
check(s.whisper_model == "large" and not hasattr(s, "model") and isinstance(s._saved, dict), "an old file still loads")

# --- The texts --------------------------------------------------------------------------------------------------------
keys = {}
for code in ("da", "sv", "no", "en"):
    d = json.loads((ROOT / "assets" / "locales" / code / "oldskill.json").read_text(encoding="utf-8"))
    keys[code] = set(d)
    check("{names}" in d["oldskill.step_delete"] and "{app}" in d["oldskill.intro"], f"oldskill {code}: placeholders")
check(all(k == keys["da"] for k in keys.values()), "oldskill: the same keys in all four languages")
check(un.OLD_SKILL_NAMES == ("subadubalingo", "videosprog"), "both old skill names")

# --- The dialog: on screen once, the flag saved the moment it shows ------------------------------------------------
try:
    import tkinter as tk
    from subadubalingo import skin, theme
    root = tk.Tk()
    root.withdraw()
except Exception as e:   # no display
    print("SKIP the dialog:", e)
    root = None
if root is not None:
    theme.load_fonts()
    skin.apply(root, theme.resolve("light"), 1.0)
    write(V211)
    show, s = start()

    class FakeApp:
        pass
    app = FakeApp()
    app.root, app.settings, app.scale, app._notice = root, s, 1.0, None
    un.show(app)
    root.update()
    saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    check(app._notice is not None and app._notice.winfo_exists() and saved.get("old_skill_notice_done") is True,
          "the dialog shows and the flag is saved at once")
    first = app._notice
    un.show(app)
    check(app._notice is first, "one notice at a time")
    first.event_generate("<Escape>")
    root.update()
    check(not first.winfo_exists(), "Escape closes it")
    root.destroy()

print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
