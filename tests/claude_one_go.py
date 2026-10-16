"""End-to-end run of skill version 6 through the real App: a fake Claude answers the task file with a filler cut,
chapters and a summary; the fake user approves the cuts; the run must cut the filler block cleanly, save
"<video> - kapitler.txt" (with the summary; an 18 s video is too short for chapters, so they are left out) and show
the "Kapitler og beskrivelse" card in the done view.

The data folder and Downloads are temp folders, and the video is copied to a temp folder first, so nothing of the
user's is touched. The window opens on the monitor the user is not working on.
  python tests/claude_one_go.py VIDEO [--parts]"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-claude-e2e-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()
import ctypes  # noqa: E402
import re  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import chapters, claude, engine, gui  # noqa: E402
from subadubalingo.common import format_srt, read_srt  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
said = []
for name in ("showwarning", "showerror", "showinfo"):
    setattr(messagebox, name, lambda *a, n=name, **k: said.append((n, a[1] if len(a) > 1 else a)))
messagebox.askyesno = lambda *a, **k: True
downloads = tmp / "Downloads"
downloads.mkdir()
gui.downloads_dir = lambda: downloads
gui.Settings.save = lambda self: None

src_video = Path(sys.argv[1])
(tmp / "video").mkdir()
video = tmp / "video" / src_video.name
shutil.copy2(src_video, video)
L = gui.LANG_BY_CODE

root = tk.Tk()
app = gui.App(root)
_screen.place(root)
app.video.set(str(video))
app._refresh()
app.speech.set(app._speech_items(L["da"])[0][1])
app.subs.set(L["no"].word("choice"))
app.cut.set(True)
app._refresh()
root.update()
app._start()


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.05)


def answer_body(d, cues, first=1, last=True):
    out = []
    for code in d.codes:
        prefix = "" if code == d.src.code else f"{code.upper()}: "
        out.append(f"=== {code} ===\n" + format_srt([engine.Cue(c.start, c.end, prefix + c.text) for c in cues], first))
    text = "\n".join(out)
    # Block 3 is "Testing, Testing." in the test video (a repetition)
    filler = [f"{i} filler Gentagelse" for i in range(first, first + len(cues)) if i == 3]
    text += "=== cuts ===\n" + "".join(l + "\n" for l in filler)
    text += "=== chapters ===\n0:00 Velkommen\n0:08 Undertekster\n"
    if last:
        text += "=== summary ===\nEn kort test af undertekster i et Teams-møde.\n"
    return text + "=== words ===\n"


def fake_claude(d: gui.ClaudePanel):
    work = d.work_file.read_text(encoding="utf-8")
    head = work.splitlines()[0]
    print("task:", head)
    assert f"chapters={d.chapter_lang.code}" in head and "filler" in work, head
    src = read_srt(d.src_srt)
    print("text:", [c.text for c in src])
    if "--parts" in sys.argv:
        d._to_parts()
        for i, part in enumerate(d.parts):
            d._copy(i)
            assert "=== chapters ===" in root.clipboard_get()
            root.clipboard_clear()
            root.clipboard_append("```\n" + answer_body(d, part, d._first_number(i), i + 1 == len(d.parts)) + "```")
            root.update()
            d._paste(i)
        d._save_parts()
        return
    job = re.search(r"id=(\S+)", head).group(1)
    (downloads / d.answer_name).write_text(f"{claude.ANSWER_HEADER} id={job} skill={claude.SKILL_VERSION}\n"
                                           + answer_body(d, src) + "=== end ===\n", encoding="utf-8")


t0 = time.time()
answered = reviewed = False
while app._busy() and time.time() - t0 < 900:
    root.update()
    time.sleep(0.05)
    if app._review is not None and not reviewed:
        reviewed = True
        rv = app._review
        for n, c in enumerate(rv.cuts, 1):
            print(f"  cut {n}: {c.start}-{c.end} ms {c.kind} ({c.source}) {c.reason}")
        fillers = [c for c in rv.cuts if c.kind == "filler"]
        assert fillers, "no filler cut in the review"
        for c in rv.cuts:
            c.on = c.kind == "filler"
        rv._changed()
        pump(1.5)
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        shot_dir = Path(r"D:\SubaDubaLingo\design\implemented")
        for theme_dir in ("light", "night"):
            (shot_dir / theme_dir).mkdir(parents=True, exist_ok=True)
        _screen.grab_window(hwnd).save(shot_dir / gui.theme.P.name / "claude-review-filler-e2e.png")
        rv._approve()
        continue
    d = app._tdialog
    if d is not None and not answered and d.winfo_exists():
        answered = True
        fake_claude(d)
pump(1.5)
print("status:", app.status.cget("text"))
print("files:", app.files.get(0, "end"))
f = chapters.find_chapters(video)
ok = f is not None and f.read_text(encoding="utf-8") == "En kort test af undertekster i et Teams-møde.\n"
ok &= any(chapters.is_chapters_file(Path(n)) for n in app.files.get(0, "end"))
ok &= bool(app.d_chapters.winfo_ismapped()) and not app.outline.chapters
cut = gui.find_cut(video)
if cut:
    info = engine.probe(engine.Runner(), cut)
    print(f"cut video: {info.duration_ms} ms (original 18.5 s, minus the filler block)")
    ok &= 15_000 < info.duration_ms < 18_000
else:
    ok = False
print("messages:", said)
root.destroy()
print("all ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
