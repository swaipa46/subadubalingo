"""One-go run through the real App object with a fake Claude that corrects/translates by prefixing the text
and reports one corrected word.

Default is the file round: the fake Claude reads "<video> - til Claude.txt" and writes the answer file into a
temporary Downloads folder, where the program must find it by itself. --parts uses the copy/paste fallback.

  python tests/one_go.py VIDEO [--speech Svensk] [--subs Norske] [--redo] [--cpu] [--close] [--parts] [--broken]
                         [--cut [--recut] [--skip-cut] [--shots DIR]]

--cut: the fake Claude also suggests cuts (the first three blocks as "start", and one block in the middle as
off-topic); the fake user moves an edge on the timeline, switches the pause suggestions off and approves.
"""
import ctypes
import ctypes.wintypes as wt
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import tkinter as tk
from tkinter import messagebox

from subadubalingo import claude, cutting, engine, gui
from subadubalingo.common import format_srt, read_srt

said = []
for name in ("showwarning", "showerror", "showinfo"):
    setattr(messagebox, name, lambda *a, n=name, **k: said.append((n, a[1] if len(a) > 1 else a)))
messagebox.askyesno = lambda *a, **k: True
downloads = Path(tempfile.mkdtemp(prefix="sdl-downloads-"))
gui.downloads_dir = lambda: downloads


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


video = Path(sys.argv[1])
root = tk.Tk()
app = gui.App(root)
app.settings.use_gpu = "--cpu" not in sys.argv
app.video.set(str(video))
app._refresh()
app.speech.set(arg("--speech", "Svensk"))
app.subs.set(arg("--subs", "Norske"))
app.redo.set("--redo" in sys.argv)
app.cut.set("--cut" in sys.argv)
app.recut.set("--recut" in sys.argv)
app._refresh()
root.update()
print("plan:", app.plan.cget("text").replace("\n", " | "))
app._start()


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        root.update()
        time.sleep(0.05)


def fake_cuts(n_total, first, count):
    """Cut lines for blocks first..first+count-1 (one part or the whole text)."""
    lines = []
    if first == 1 and n_total >= 6:
        lines.append("1-3 start Folk kommer ind og tester lyd")
    mid = n_total // 2
    if first <= mid < first + count and n_total >= 6:
        lines.append(f"{mid} offtopic Snak om vejret")
    return lines


def fake_answer(codes, src_code, cues, first=1, header=None, end=True, cut=False, n_total=None):
    blocks = []
    for code in codes:
        prefix = "" if code == src_code else f"{code.upper()}: "
        fixed = [engine.Cue(c.start, c.end, prefix + c.text.replace("Testingen", "Testing")) for c in cues]
        blocks.append(f"=== {code} ===\n" + format_srt(fixed, first))
    text = ("" if header is None else header + "\n") + "\n".join(blocks)
    if cut:
        text += "=== cuts ===\n" + "".join(l + "\n" for l in fake_cuts(n_total or len(cues), first, len(cues)))
    text += "=== words ===\nTestingen => Testing\n"
    return text + ("=== end ===\n" if end else "")


def fake_claude_file(d: gui.ClaudePanel):
    work = d.work_file.read_text(encoding="utf-8")
    job = re.search(r"id=(\S+)", work.splitlines()[0]).group(1)
    src = read_srt(d.src_srt)
    d._copy_file()
    pump(1)
    if "--broken" in sys.argv:
        (downloads / d.answer_name).write_text(fake_answer(d.codes, d.src.code, src[:-1],
                                                           header=f"{claude.ANSWER_HEADER} id={job}"), encoding="utf-8")
        pump(3)
        print("  broken answer ->", d.result.cget("text").splitlines()[0][:110])
    (downloads / ("x" + d.answer_name)).write_text(fake_answer(d.codes, d.src.code, src,
                                                               header=f"{claude.ANSWER_HEADER} id={job} skill={claude.SKILL_VERSION}", cut=d.cut),
                                                   encoding="utf-8")
    pump(4)
    print("  work file removed:", not d.work_file.exists())


def fake_claude_parts(d: gui.ClaudePanel):
    d._to_parts()
    for i, part in enumerate(d.parts):
        d._copy(i)
        root.clipboard_clear()
        root.clipboard_append(fake_answer(d.codes, d.src.code, part, d._first_number(i), cut=d.cut,
                                          n_total=len(d.cues)))
        root.update()
        d._paste(i)
        print(f"  part {i + 1} {d.codes}: {d.state_labels[i].cget('text')}")
    d._save_parts()


shots = Path(arg("--shots", "")) if "--shots" in sys.argv else None


def take(name):
    if not shots:
        return
    shots.mkdir(parents=True, exist_ok=True)
    from PIL import ImageGrab
    root.lift()
    root.attributes("-topmost", True)
    pump(1.0)
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(shots / name)
    root.attributes("-topmost", False)
    print("  screenshot", shots / name)


def fake_review(rv):
    print("review:", rv.summary.cget("text"))
    for n, c in enumerate(rv.cuts, 1):
        print(f"  {n}. {cutting.fmt_clock(c.start)}-{cutting.fmt_clock(c.end)} {c.kind} ({c.source}) {c.reason}")
    pump(1.5)
    take("review-1.png")
    if "--skip-cut" in sys.argv:
        rv._skip()
        return
    # switch off the pauses the program found, move the first cut's end 0.5 s earlier, select it
    for c in rv.cuts:
        if c.source == "auto" and c.kind == "pause":
            c.on = False
    first = rv.cuts[0]
    first.end -= 500
    rv._changed(select=first)
    pump(2.5)   # frames load
    take("review-2.png")
    print("approve:", rv.summary.cget("text"))
    rv._approve()


t0 = time.time()
answered = False
reviewed = False
while app._busy() and time.time() - t0 < 1800:
    root.update()
    time.sleep(0.05)
    if app._review is not None and not reviewed:
        reviewed = True
        fake_review(app._review)
        continue
    d = app._tdialog
    if d is not None and not answered and d.winfo_exists():
        print("claude turn while:", app.status.cget("text")[:60])
        answered = True
        if "--close" in sys.argv:
            d._close()
        elif "--parts" in sys.argv:
            fake_claude_parts(d)
        else:
            fake_claude_file(d)
print("status:", app.status.cget("text"))
print("result:", app.d_title.cget("text"), "|", app.d_msg.cget("text")[:200].replace("\n", " "))
take("done.png")
print("files:", app.files.get(0, "end"))
print("messages:", said)
root.destroy()
