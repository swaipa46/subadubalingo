"""The assistant panel (gui.ClaudePanel, version 2.2.0, docs/assistant-panel.md) without the rest of the app: the chat
view is the default for a job started in the app (copy the instructions into any AI chat, copy the answer back), the
part states, finishing by itself, the take-over phrase is never an answer, and the MCP view's way into the chat view.
The data folder and the video's folder are temp folders. Run: python tests/test_assistant_panel.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-panel-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

from subadubalingo import claude, gui, i18n, jobs, skin, theme  # noqa: E402
from subadubalingo.common import Cue, format_srt, write_srt  # noqa: E402
from subadubalingo.i18n import t  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


root = tk.Tk()
root.withdraw()
theme.load_fonts()
skin.apply(root, theme.resolve("light"), 1.0)
L = common.LANG_BY_CODE


class FakeApp:
    """What the panel uses of gui.App."""

    def __init__(self):
        self.root, self.scale, self.bg, self.job = root, 1.0, theme.P.window, {"lang": L["da"], "subs": L["sv"]}
        self.main = tk.Frame(root)
        self.callout = tk.Frame(root)
        self._pulsing = False
        self.cut_lines, self.created, self.learned_words = [], [], []
        self.outline, self.chapters_file = None, None

    def _wrap(self, label, parent, pad=20):
        pass

    def add_file(self, path):
        self.created.append(path)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.02)


def set_clip(text):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()


def make_video(name, n):
    folder = tmp / name
    folder.mkdir()
    video = folder / "Webinar.mp4"
    video.write_bytes(b"x")
    cues = [Cue(i * 4000, i * 4000 + 3000, f"Blok {i + 1} om memberkær.") for i in range(n)]
    srt = folder / "Webinar - danske undertekster.srt"
    write_srt(srt, cues)
    return video, srt, cues


def answer(cues, first, codes, words=True):
    fixed = [Cue(c.start, c.end, c.text.replace("memberkær", "Membercare")) for c in cues]
    out = "```\n" + "".join(f"=== {c} ===\n{format_srt(fixed, first)}\n" for c in codes)
    return out + ("=== words ===\nmemberkær => Membercare\n" if words else "") + "```"


# --- Chat view: the default for a job started in the app ------------------------------------------------------------
set_clip("an old answer 00:00:01,000 --> 00:00:02,000")
video, srt, cues = make_video("one", 3)
results = []
app = FakeApp()
p = gui.ClaudePanel(root, app, video, L["da"], srt, True, [L["sv"]], on_finish=results.append)
p.grid()
root.update()
check(p.mode == "chat" and not p.switch_btn.winfo_manager(), "an app job opens the chat view, without a switch button")
check(sorted(f.name for f in video.parent.iterdir()) == sorted([video.name, srt.name]),
      "no task file next to the video")
row = p.part_rows[0]
check(len(p.parts) == 1 and not row["title"].winfo_manager(), "one part: no part title")
check(str(row["copy"].cget("style")) == "Accent.TButton" and row["state"] == "active"
      and p.next_lbl.cget("text") == t("gui.parts.next_copy", copy=t("gui.parts.copy")),
      "not copied, next: the copy button is the primary one")
check(p.watching is None, "the old clipboard is not watched before anything is copied")

row["copy"].invoke()
prompt = root.clipboard_get()
check("=== da ===" in prompt and "-->" in prompt and "skill" not in prompt.lower(), "copy puts the instructions there")
check(str(row["copy"].cget("text")) == t("gui.parts.copy_again") and str(row["paste"].cget("style")) == "Accent.TButton"
      and row["state_lbl"].cget("text") == t("gui.parts.copied"), "copied, waiting: \"Kopiér igen\", paste is primary")
p._watch_clipboard()
check(p.answers[0] is None and not p.problem[0], "the instructions on the clipboard are not an answer")

# The take-over phrase is never an answer
p._copy_phrase()
check(root.clipboard_get() == t("gui.parts.mcp_phrase", app=common.APP_NAME), "the phrase is copied bare")
p._watch_clipboard()
check(p.answers[0] is None and not p.problem[0], "copying the take-over phrase is not taken as an answer")

# A partial answer (only Danish) is a problem that keeps what came
set_clip(answer(cues, 1, ["da"]))
p._watch_clipboard()
check(p.problem[0] and row["state"] == "failed" and "da" in p.partial[0]
      and str(row["paste"].cget("style")) == "Accent.TButton"
      and p.next_lbl.cget("text") == t("gui.parts.problem", part=t("gui.parts.the_text")),
      "a partial answer: problem, the partial kept, paste is primary")
# The rest (Swedish) completes it, and the panel finishes by itself
set_clip(answer(cues, 1, ["sv"], words=False))
p._watch_clipboard()
check(p.answers[0] is not None and row["state"] == "done" and "✓" in row["state_lbl"].cget("text"),
      "the rest completes the part")
check(p.next_lbl.cget("text") == t("gui.claude.answer_ok_blocks", n=3) and not results, "all in: no click needed ...")
pump(1.6)
check(results == [True], "... the round finishes by itself")
sv = common.srt_name(video, L["sv"])
check(sv.exists() and "Membercare" in sv.read_text(encoding="utf-8") and sv in app.created,
      "the subtitles are written")
check(("memberkær", "Membercare") in claude.word_rules(), "the corrected word is learned")
p.destroy()

# --- Several parts --------------------------------------------------------------------------------------------------
video, srt, cues = make_video("many", 250)
results = []
p = gui.ClaudePanel(root, FakeApp(), video, L["da"], srt, True, [], on_finish=results.append)
p.grid()
root.update()
n = len(p.parts)
check(n > 1 and all(r["title"].winfo_manager() for r in p.part_rows), f"{n} parts, each with its title")
check([r["state"] for r in p.part_rows] == ["active"] + ["todo"] * (n - 1), "the first part is next, the rest later")
p._paste(0, "")
check(not p.problem[0] and p.answers[0] is None, "\"Indsæt svar\" with nothing usable is no problem yet")
for i in range(n):
    p._copy(i)
    first = p._first_number(i)
    set_clip(answer(p.parts[i], first, ["da"]))
    p._watch_clipboard()
check(all(p.answers) and not results, "every part answered")
pump(1.6)
check(results == [True], "several parts finish by themselves too")
p.destroy()

# --- MCP view: the assistant works through the tools; the user can switch to an AI chat ---------------------------
video, srt, cues = make_video("mcp", 3)
job = "testjob1"
jobs.job_dir(job)
results = []
app = FakeApp()
p = gui.ClaudePanel(root, app, video, L["da"], srt, True, [L["sv"]], on_finish=results.append, mcp_job=job)
p.grid()
root.update()
check(p.mode == "mcp" and p.switch_btn.winfo_manager() and p.switch_btn.cget("text") == t("gui.claude.to_chat")
      and (jobs.root() / job / "task.json").exists(), "an MCP job: progress view, task published, switch button")
check(not (jobs.root() / job / "skill.json").exists(), "no skill.json any more")
p.switch_btn.invoke()
root.update()
check(p.mode == "chat" and not p.switch_btn.winfo_manager() and app._pulsing and p.part_rows,
      "the switch button opens the chat view")
# The assistant finishes after all: complete.json still ends the round from the chat view
fixed = [Cue(c.start, c.end, c.text.replace("memberkær", "Membercare")) for c in cues]
jobs.write_json(jobs.root() / job / "complete.json",
                {"codes": {"da": jobs.cues_to_json(fixed), "sv": jobs.cues_to_json(fixed)}, "words": [], "cuts": []})
pump(1.2)
check(results == [True], "complete.json finishes the round in the chat view too")
p.destroy()

root.destroy()
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
