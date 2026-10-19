"""Screenshots of feature "claude" in one theme: the done view with the assistant's chapters and description
(claude-done-chapters.png, and claude-done-summary-only.png for a video too short for chapters), the assistant panel's
chat view (claude-chat-view.png, claude-chat-copied.png), the one-time old-skill notice (claude-oldskill-notice.png),
and the cut review with a filler cut in the legend and on the timeline (claude-review-filler.png). The data folder is a temp folder,
the video is copied to a temp folder; windows open on the monitor the user is not working on (PrintWindow).
  python tests/claude_shots.py VIDEO OUT_ROOT --theme light|night [--lang da|sv|no|en]
Saves to OUT_ROOT/<theme>/claude-*.png."""
import ctypes
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-claude-shots-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import chapters, cutting, gui, i18n, theme  # noqa: E402
from subadubalingo.chapters import Chapter, Outline  # noqa: E402
from subadubalingo.common import FFMPEG, LANG_BY_CODE, find_srt, read_srt  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
gui.Settings.save = lambda self: None
theme.load_fonts()
src_video = Path(sys.argv[1])
out = Path(sys.argv[2]) / forced_theme
out.mkdir(parents=True, exist_ok=True)
(tmp / "video").mkdir()
video = tmp / "video" / src_video.name
shutil.copy2(src_video, video)
srt = find_srt(src_video, LANG_BY_CODE["da"])
shutil.copy2(srt, video.with_name(srt.name.replace(src_video.stem, video.stem)))


def pump(root, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.03)


def shot(root, name):
    root.lift()
    root.attributes("-topmost", True)
    pump(root, 0.8)
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    _screen.grab_window(hwnd).save(out / name)
    root.attributes("-topmost", False)
    print("saved", out / name)


root = tk.Tk()
app = gui.App(root)
_screen.place(root)
L = gui.LANG_BY_CODE
# The done view after a webinar was cut, subtitled in Norwegian and dubbed to Swedish, with Claude's chapters
web = Path.home() / "Videos" / "Superbrugerwebinar.mp4"
result = gui.dub_name(web, L["sv"], L["no"])
app.outline = Outline([Chapter(0, "Välkommen och agenda"), Chapter(95_000, "Det nya anmälningsflödet"),
                       Chapter(312_000, "Så skapar du ett arrangemang"), Chapter(608_000, "Betalning och kvitton"),
                       Chapter(903_000, "Medlemssidan på mobilen"), Chapter(1_140_000, "Frågor från deltagarna"),
                       Chapter(1_460_000, "Sammanfattning")],
                      "Ett webbinarium för superanvändare om det nya anmälningsflödet till arrangemang på Membersite: "
                      "hur medlemmar anmäler sig, hur du skapar arrangemanget och vad som händer med betalningen.")
app.chapters_file = chapters.chapters_name(web)
app.created = [gui.srt_name(web, L["da"]), gui.srt_name(web, L["no"]), app.chapters_file, result]
for path in app.created:
    app.files.insert("end", path.name)
app.job = {"video": web, "lang": L["da"], "speech": L["sv"], "subs": L["no"], "cut": True, "transcribe": True,
           "check": True, "translate": [L["sv"], L["no"]]}
app._review_result = [cutting.Cut(0, 60_000, "start"), cutting.Cut(700_000, 704_000, "filler")]
gui.engine.probe = lambda runner, v: gui.engine.MediaInfo(1_560_000, True)
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"name": n, "state": "done"} for n in names]
app._show("done")
app._show_result("done", result, replay=True)
shot(root, "claude-done-chapters.png")
app.outline = Outline([], "En kort test af undertekster i et Teams-møde.")
app._show_result("done", result, replay=True)
shot(root, "claude-done-summary-only.png")

# The assistant's turn in the chat view (a job started in the app, docs/assistant-panel.md): before and after copying
# the instructions, then the one-time "delete the old skill" notice an upgrade from 2.1.x shows
app._show("work")
app.job = {"video": video, "lang": L["da"], "subs": L["sv"], "cut": True, "transcribe": True, "check": True,
           "translate": [L["sv"]]}
app.claude_idle.grid_remove()
app._set_claude_width(True)
panel = gui.ClaudePanel(app.claude_box, app, video, L["da"], find_srt(video, L["da"]), True, [L["sv"]],
                        on_finish=lambda ok: None, cut=True, duration=18_000)
app._tdialog = panel
panel.grid(row=0, column=0, sticky="nsew")
app.callout.grid()
app.claude_box.configure(style="TurnA.TLabelframe")
gui.skin.skin_cards(app.main)
shot(root, "claude-chat-view.png")
panel._copy(0)
shot(root, "claude-chat-copied.png")
panel.on_finish = None
app._close_claude_panel()
gui.upgrade_notice.show(app)
notice = app._notice
notice.lift()
notice.attributes("-topmost", True)
pump(root, 0.8)
_screen.grab_window(ctypes.windll.user32.GetParent(notice.winfo_id())).save(out / "claude-oldskill-notice.png")
print("saved", out / "claude-oldskill-notice.png")
notice.destroy()

# The cut review with a filler cut (block 3, "Testing, testing") next to a Claude start cut and the pauses
app._show("setup")
app.step_rows = []
cues = read_srt(find_srt(video, L["da"]))
wav = tmp / "a.wav"
subprocess.run([str(FFMPEG()), "-v", "error", "-y", "-i", str(video), "-ac", "1", "-ar", "16000", str(wav)], check=True)
env = cutting.envelope(wav)
dur = int(len(env) * cutting.ENVELOPE_MS)
claude_cuts, bad = cutting.parse_cut_lines(["1 start Folk kommer ind", "3 filler Gentagelse: tæst, tæst",
                                            "6 offtopic Hilsner"], cues, dur)
assert not bad, bad
sugg = cutting.combine_suggestions(cutting.find_pauses(cues, dur, env, min_ms=5000), claude_cuts)
app._start_review(video, dur, cues, sugg, env)
pump(root, 1.5)
rv = app._review
rv._select(next(c for c in rv.cuts if c.kind == "filler"))
pump(root, 3)
shot(root, "claude-review-filler.png")
# Back on an ordinary cut, the frames and all four buttons return; "Behold alle fyldord" keeps the fillers
rv._select(rv.cuts[0])
pump(root, 0.5)
assert rv.img_a.winfo_ismapped() and rv.b_cut.winfo_ismapped() and rv.b_toggle.grid_info()["row"] == 1
assert rv.b_fillers.winfo_ismapped()
rv.b_fillers.invoke()
pump(root, 0.3)
assert not any(c.on for c in rv.cuts if c.kind == "filler") and not rv.b_fillers.winfo_ismapped()
print("review states ok")
root.destroy()
