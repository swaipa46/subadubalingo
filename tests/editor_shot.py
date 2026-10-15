"""The subtitle editor in the real main window: opens it from the done view for a copy of VIDEO and its subtitles (in
a temp folder, with a temp data folder), clicks, edits, splits, merges, drags an edge and saves, checks the files,
and saves screenshots in one theme.

  python tests/editor_shot.py VIDEO OUTDIR [--theme light|night] [--lang da|sv|no|en] [--wait]

The window opens on the monitor the user is not working on and is captured with PrintWindow. No sound is played
(winsound is replaced; the snippets asked for are counted).
"""
import ctypes
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
ctypes.windll.shcore.SetProcessDpiAwareness(1)
from subadubalingo import common

tmp = Path(tempfile.mkdtemp(prefix="sdl-editor-shot-"))
common._DATA_DIR = tmp / "data"          # never Kasper's ~/.subadubalingo (settings, corrections, text state)
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import editor, gui, i18n, textstate, theme  # noqa: E402
from subadubalingo.common import LANG_BY_CODE, find_srt, read_srt  # noqa: E402

arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
src_video, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
theme.resolve = lambda choice: forced_theme
i18n.resolve = lambda choice: forced_lang
gui.Settings.save = lambda self: None
theme.load_fonts()
played = []


class _Quiet:   # winsound without the sound
    SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 0x20000, 0x1, 0x2

    @staticmethod
    def PlaySound(sound, flags):
        played.append(sound)


editor.winsound = _Quiet

# A copy of the video and its subtitles (the originals are never written)
folder = tmp / "video"
folder.mkdir()
video = folder / src_video.name
shutil.copy2(src_video, video)
DA, EN = LANG_BY_CODE["da"], LANG_BY_CODE["en"]
srts = {}
for lang in common.LANGUAGES:
    p = find_srt(src_video, lang)
    if p:
        shutil.copy2(p, folder / p.name)
        srts[lang.code] = folder / p.name
assert "da" in srts, "VIDEO needs its Danish subtitles next to it"
textstate.record(srts["da"], video)
textstate.set_checked(srts["da"], True)
original = srts["da"].read_text(encoding="utf-8")
# A misheard word to correct, so saving offers it to the word list
cues = read_srt(srts["da"])
common.write_srt(srts["da"], [common.Cue(c.start, c.end, c.text.replace("webinar", "vebinar")) for c in cues])
original = srts["da"].read_text(encoding="utf-8")

root = tk.Tk()
app = gui.App(root)
_screen.place(root)
tag = forced_theme


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.03)


def take(name, win=None):
    win = win or root
    win.lift()
    win.attributes("-topmost", True)
    pump(0.8)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"editor-{name}.png")
    win.attributes("-topmost", False)
    print("saved", out / f"editor-{name}.png")


def wait_for(cond, sec=30):
    t0 = time.time()
    while not cond() and time.time() - t0 < sec:
        pump(0.2)
    return cond()


# The done view after a run that burned Danish subtitles into the video (and made English ones): Afspil, Vis i mappe,
# Ret undertekster on the result card; the hovered .srt row shows its small "Ret" button
burned = gui.burned_name(video, DA)
shutil.copy2(video, burned)
app.job = {"video": video, "lang": DA, "speech": None, "subs": DA, "cut": False, "transcribe": True, "check": True,
           "translate": []}
app.created = [srts[c] for c in ("da", "en") if c in srts] + [burned]
for p in app.created:
    app.files.insert("end", p.name)
names, app.claude_at = app._step_names(app.job)
app.step_rows = [{"name": n, "state": "done"} for n in names]
app._show("done")
app._show_result("done", burned, replay=True)
assert app.d_edit.winfo_manager() == "pack" and "Accent" not in str(app.d_edit.cget("style"))
app.files._set_hover(app.files.rows[1]["frame"])
assert app.files.rows[1]["edit"].winfo_manager() == "grid" and app.files.rows[0]["edit"].winfo_manager() == ""
assert app.files.rows[2]["edit"] is None   # the video row has none
take(f"done-{tag}" if "--lang" not in sys.argv else f"done-{tag}-{forced_lang}")
app.files._set_hover(None)

# Double-click on the .srt row opens the editor, not another program
opened = []
app._open = lambda p: opened.append(p)
app.files._open(0)
ed = app._editor
assert ed is not None and app.view == "editor" and not opened, (app.view, opened)
assert ed.s.doc.path == srts["da"] and ed.s.docs[0].lang.code == "da" and ed.s.remake == {DA}
assert wait_for(lambda: ed.media.env is not None and ed.media.duration), "no sound/duration"
i_anne = next(n for n, c in enumerate(ed.s.doc.cues) if "hun kan" in c.text)
ed.select(i_anne)
assert wait_for(lambda: ed._img is not None), "no picture"
pump(1)
take(tag if "--lang" not in sys.argv else f"{tag}-{forced_lang}")
if "--lang" in sys.argv:   # a language check only
    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0)

# Play the line (no sound: counted), with the button and with Space outside the text
ed.play_line()
pump(0.5)
assert played and str(played[-1]).endswith(".wav"), played
n = len(played)
ed.media.playing_until = 0
ed.lines.c.focus_set()
pump(0.2)
ed.lines.c.event_generate("<space>")
pump(0.5)
assert len(played) > n, "Space did not play"

# Edit the misheard word in the row's field
ed.select(0)
ed.field.delete("1.0", "end")
ed.field.insert("1.0", ed.s.doc.cues[0].text.replace("vebinar", "webinar"))
ed._text_changed()
pump(0.3)
assert "webinar" in ed.s.doc.cues[0].text and ed.s.doc.dirty
assert ed.b_close.cget("text") == gui.t("editor.cancel")
# Esc on another line puts it back
ed.select(1)
old = ed.s.doc.cues[1].text
ed.field.insert("end", " xyz")
ed._text_changed()
ed._revert_line()
assert ed.s.doc.cues[1].text == old

# Split the Anne line at "hun" with the field's cursor, merge it back, undo both
n_before = len(ed.s.doc.cues)
ed.select(i_anne)
ed.field.mark_set("insert", f"1.0+{ed.s.doc.cues[i_anne].text.index('hun')}c")
ed.split()
assert len(ed.s.doc.cues) == n_before + 1 and ed.s.doc.cues[i_anne + 1].text.startswith("hun"), ed.s.doc.cues
assert ed.s.sel == i_anne + 1
ed.select(i_anne)
ed.merge()
assert len(ed.s.doc.cues) == n_before
ed.undo()   # the merge undone: split again
assert len(ed.s.doc.cues) == n_before + 1
ed.undo()   # and the split
assert len(ed.s.doc.cues) == n_before

# Alt+arrow: 100 ms, and blocked at the neighbour
ed.select(1)
s0 = ed.s.doc.cues[1].start
ed.nudge("start", -100)
assert ed.s.doc.cues[1].start == max(ed.s.doc.cues[0].end, s0 - 100)

# Drag the end of line 2 on the timeline, with made-up mouse events


class Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


pump(0.5)
q = ed.s.doc.cues[1]
_, _, y0, y1 = ed._rows()
x = ed.x_of(q.end)
n_played = len(played)
ed._on_press(Ev(x, (y0 + y1) // 2))
for step in range(1, 16):
    ed._on_drag(Ev(x + step * (ed.x_of(q.end + 40) - ed.x_of(q.end)), (y0 + y1) // 2))
    pump(0.03)
take("drag-" + tag)
ed._on_release(Ev(x, 0))
assert len(played) > n_played, "no sound snippets while dragging"
assert ed.s.doc.cues[1].end <= ed.s.doc.cues[2].start

# Switch to English (if made) and back, with the picker
codes = [d.lang.code for d in ed.s.docs]
assert codes == [c for c in ("da", "sv", "no", "en") if c in srts], codes
if "en" in codes:
    ed.picker.pick(str(codes.index("en")))
    pump(0.5)
    assert ed.s.cur == codes.index("en") and ed.l_file.cget("text") == srts["en"].name
    take("language-" + tag)
    ed.picker.pick("0")

# Save: backup, numbering, words offered; the dialog is shot and answered "add the words"
shots = []
real_dialog = editor.save_dialog


def dialog(parent, files, notes, pairs, remake):
    real_wait = parent.wait_window

    def fake_wait(win):
        pump(0.6)
        take("save-" + tag, win)
        win.destroy()
    parent.wait_window = fake_wait
    try:
        real_dialog(parent, files, notes, pairs, remake)
    finally:
        parent.wait_window = real_wait
    shots.append((files, notes, pairs, remake))
    return [p for p in pairs if not editor.is_joining(*p)], False


editor.save_dialog = dialog
assert ed.save()
files, notes, pairs, remake = shots[0]
assert ("vebinar", "webinar") in pairs and remake and files == [srts["da"].name], shots
assert any(".bak" in n for n in notes), notes
assert editor.backup_path(srts["da"]).read_text(encoding="utf-8") == original
saved = read_srt(srts["da"])
assert "webinar" in saved[0].text
text = srts["da"].read_text(encoding="utf-8")
assert text.startswith("1\n") and all(f"\n{n}\n" in text for n in range(2, len(saved) + 1))
for a, b in zip(saved, saved[1:]):
    assert a.end <= b.start
assert "vebinar = webinar" in (common._DATA_DIR / "corrections.txt").read_text(encoding="utf-8")
assert textstate.status(srts["da"], video) == "ok" and textstate.is_checked(srts["da"])
assert not ed.s.dirty and ed.b_close.cget("text") == gui.t("editor.close")
take("saved-" + tag)

# "Lav videoen igen": the editor closes and the run starts again with the same choices
ed.s.doc.cues[0].text += "!"
started = []
app._start = lambda *a, **kw: started.append(True)
editor.save_dialog = lambda parent, files, notes, pairs, remake: ([], True)
assert ed.save()
pump(0.3)
assert started and app._editor_session is None and app.view == "done"
editor.save_dialog = real_dialog

# The setup view offers the editor when the chosen video has subtitles
app._show("setup")
app.video.set(str(video))
pump(0.3)
assert app.v_edit.winfo_manager() == "grid"
take("setup-" + tag)
app.v_edit.invoke()
ed = app._editor
assert ed is not None and ed.s.video == video and ed.s.source.code == "da" and ed.s.back == "setup"
assert not ed.s.remake

# A theme switch keeps the editor and its work (one way only: switching there and back in one process loses the
# 9-slice frames on main too, see the report)
other = "night" if forced_theme == "light" else "light"
ed.s.doc.cues[0].text += " (x)"
theme.resolve = lambda choice: other
app.set_theme(other)
pump(0.5)
assert app._editor is not ed and app._editor.s is ed.s and app.view == "editor" and app._editor.s.dirty
# Closing with unsaved edits asks; "No" throws them away and goes back to setup
asked = []
gui.boxes.askyesnocancel = lambda *a, **kw: asked.append(a) or False
assert editor.close(app) and asked and app.view == "setup"
assert "(x)" not in read_srt(srts["da"])[0].text
print("snippets asked for:", len(played))
if "--wait" in sys.argv:
    root.mainloop()
else:
    root.destroy()
shutil.rmtree(tmp, ignore_errors=True)
print("all ok")
