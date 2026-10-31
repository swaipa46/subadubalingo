"""The turn view (2026-10-31: Anne started a cut in the app, watched the percentage stand still and missed DIN TUR).
When the run waits for the user in the AI-chat round, the AI card takes the whole window (the steps, percentage and
Pause are hidden), the title bar says DIN TUR, the card shows three numbered steps and "venter på dig · trin n af m"
instead of a percentage, and everything is back when the round ends. The data folder is a temp folder.
  python tests/test_turn_view.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-turn-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

from subadubalingo import gui, i18n, theme  # noqa: E402
from subadubalingo.common import APP_NAME, Cue, write_srt  # noqa: E402
from subadubalingo.i18n import t  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.02)


root = tk.Tk()
# Mapped but transparent (a never-mapped root's clipboard misbehaves on Windows)
root.attributes("-alpha", 0.0)
app = gui.App(root)
app._flash = lambda: None   # no sound or taskbar flash from a test
L = common.LANG_BY_CODE
folder = tmp / "v"
folder.mkdir()
video = folder / "Webinar.mp4"
video.write_bytes(b"x")
srt = folder / "Webinar - danske undertekster.srt"
write_srt(srt, [Cue(i * 4000, i * 4000 + 3000, f"Blok {i + 1}.") for i in range(3)])

app._show("work")
app.job = {"video": video, "lang": L["da"], "speech": None, "subs": L["sv"], "cut": True, "transcribe": True,
           "check": True, "translate": [L["sv"]]}
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"text": tk.Label(root), "icon": tk.Label(root), "state": "todo"} for _ in names]
app._running = True
pump(0.3)
check(app.left.winfo_manager() == "grid" and root.title() == APP_NAME, "before the turn: the progress view, the plain title")

app.events.put(("claude", video, L["da"], srt, True, [L["sv"]], True, 12_000))
pump(0.8)
p = app._tdialog
check(p is not None and p.mode == "chat", "the AI-chat round has started")
check(not app.left.winfo_manager(), "the progress view (steps, percentage, Pause) is hidden")
info = app.claude_wrap.grid_info()
check(int(info["column"]) == 0 and int(info["columnspan"]) == 2, "the AI card takes the whole width")
check(root.title() == t("gui.turn.window_title", app=APP_NAME), f"the title bar says it ({root.title()})")
strip = " ".join(w.cget("text") for w in p.turn_strip.winfo_children() if isinstance(w, gui.ttk.Label))
check(strip == t("gui.claude.turn_status", n=app.claude_at + 1, total=len(names)),
      f"a waiting line instead of a percentage ({strip})")
colours = [str(lbl.cget("foreground")) for _, lbl in p.turn_steps]
check(len(p.turn_steps) == 3 and colours[0] != colours[1] and colours[1] == colours[2],
      "three numbered steps; the first is the one to do")

p._copy(0)
pump(0.3)
nums = [str(num.cget("foreground")) for num, _ in p.turn_steps]
check(nums[0] == str(theme.P.success) and nums[1] == nums[2] == str(theme.P.step_active),
      "copied: step 1 is done, 2 and 3 are next")

# The round ends (as _translation_finished does after the answer is in)
p.on_finish = None
app._close_claude_panel()
pump(0.3)
info = app.claude_wrap.grid_info()
check(app.left.winfo_manager() == "grid" and int(info["column"]) == 1 and int(info["columnspan"]) == 1,
      "afterwards the progress view and the narrow card are back")
check(root.title() == APP_NAME, "and the title bar is plain again")

# The reminder: only while she still has something to do
calls = []
app._flash = lambda: calls.append(1)
app._attention_gen = 5
app._remind(5)
check(not calls, "no reminder once the turn is over")

app._running = False
root.destroy()
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
