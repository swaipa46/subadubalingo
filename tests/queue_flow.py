"""The queue in the real app, end to end, with the 18 s test video copied to a temp folder (three "videos"), a temp data
folder and the user's answers made up:

  main    one video dropped on the window (the setup view stays as for one video), two more dropped (the queue shows),
          the third set to Norwegian subtitles, Start: A has checked Danish subtitles (only burned in), B is listened to
          (Whisper) and C has unchecked subtitles; the Claude rounds answer themselves with the Danish text. Checks the
          order of the rounds, the result files and the summary, and saves the setup, work and done screenshots.
  cancel  a queue of two; the first is stopped during its run and "drop the rest" is answered yes.
  resume  a queue.json left by a crash: the app asks, resumes and shows the summary.

  python tests/queue_flow.py OUT_DIR [--theme light|night] [--scenario main|cancel|resume] [--lang da]
Windows open on the monitor the user is not working on and are captured with PrintWindow. No sound is played."""
import os
import ctypes
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subadubalingo import common

TMP = Path(tempfile.mkdtemp(prefix="sdl-queue-flow-"))
common._DATA_DIR = TMP / "data"          # never the real ~/.subadubalingo
common._DATA_DIR.mkdir()
import tkinter as tk

import _screen
from subadubalingo import gui, i18n, queue_view, textstate, theme
from subadubalingo.common import LANG_BY_CODE, read_srt

ctypes.windll.shcore.SetProcessDpiAwareness(1)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
THEME, SCENARIO, LANG = arg("--theme", "light"), arg("--scenario", "main"), arg("--lang", "da")
theme.resolve = lambda choice: THEME
i18n.resolve = lambda choice: LANG
theme.load_fonts()
SRC = Path(os.environ.get("SDL_TEST_VIDEO", "C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4"))
SRC_SRT = SRC.with_name(SRC.stem + " - danske undertekster.srt")
DA, NO = LANG_BY_CODE["da"], LANG_BY_CODE["no"]

asked, told = [], []


def yes(*a, **kw):
    asked.append(a[1] if len(a) > 1 else kw.get("message", ""))
    return True


gui.boxes = type("B", (), {"askyesno": staticmethod(yes),
                           "showinfo": staticmethod(lambda *a, **kw: told.append(a)),
                           "showwarning": staticmethod(lambda *a, **kw: told.append(a)),
                           "showerror": staticmethod(lambda *a, **kw: told.append(a))})()
gui.upgrade_notice.show = lambda app: None   # the one-time notice after an update is not part of this


def pump(root, sec, until=None):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return False


def shot(root, name):
    pump(root, 0.8)
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    path = OUT / f"queue-{name}.png"
    _screen.grab_window(hwnd).save(path)
    print("saved", path)


def video(name: str, srt: str | None = None) -> Path:
    """A copy of the test video; srt: None, "checked" or "unchecked" Danish subtitles next to it."""
    d = TMP / "videos"
    d.mkdir(exist_ok=True)
    v = d / f"{name}.mp4"
    shutil.copy2(SRC, v)
    if srt:
        s = gui.srt_name(v, DA)
        shutil.copy2(SRC_SRT, s)
        textstate.record(s, v)
        textstate.set_checked(s, srt == "checked")
    return v


def drop(root, paths):
    """Drop files on the window as Explorer does: WM_DROPFILES with a DROPFILES block in global memory."""
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    names = ("\0".join(str(p) for p in paths) + "\0\0").encode("utf-16-le")
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    h = k32.GlobalAlloc(0x0042, 20 + len(names))   # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = k32.GlobalLock(h)
    header = (20).to_bytes(4, "little") + bytes(12) + (1).to_bytes(4, "little")   # pFiles, pt, fNC, fWide
    ctypes.memmove(p, header + names, 20 + len(names))
    k32.GlobalUnlock(h)
    u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u32.PostMessageW(gui.frame_hwnd(root), 0x0233, h, 0)
    pump(root, 0.6)


# The Claude round answers itself (the Danish text, also as the translations), after a screenshot of the user's turn
claude_rounds = []
_Panel = gui.ClaudePanel


class AutoPanel(_Panel):
    def __init__(self, master, app, video, src, src_srt, check, targets, **kw):
        super().__init__(master, app, video, src, src_srt, check, targets, **kw)
        claude_rounds.append((video.name, check, [x.code for x in targets]))
        cues = read_srt(src_srt)
        self.after(1500 if len(claude_rounds) == 1 else 300,
                   lambda: (len(claude_rounds) == 1 and SCENARIO == "main" and shot(app.root, f"claude-{THEME}"),
                            self._finish({c: cues for c in self.codes})))


gui.ClaudePanel = AutoPanel
rounds = []   # (video, round) as each round's run starts
_begin = queue_view.VideoQueue.begin


def begin(self, item, phase):
    rounds.append((Path(item.video).stem, queue_view.PHASES[phase]))
    _begin(self, item, phase)
    if len(rounds) == 1 and SCENARIO == "main":   # the work view while the first round listens
        root.after(1200, work_shot)


def work_shot():
    # A (checked subtitles) needed no listening: "Lyttet igennem, venter på AI-assistenten" with step-done, C waits with
    # step-pending (design review round 4, item 8)
    a, _, c = app.vq.q.items
    status = app.vq._status
    assert status(a, False)[0] == i18n.t("queue.state.listened") and status(a, False)[2] == "step-done", status(a, False)
    assert status(c, False)[2] == "step-pending", status(c, False)
    shot(root, f"work-{THEME}")


queue_view.VideoQueue.begin = begin

root = tk.Tk()
app = gui.App(root)
_screen.place(root)
pump(root, 1.0)
assert app.vq.drop is not None, "drop target not registered"

if SCENARIO == "main":
    a, b, c = video("Møde A", "checked"), video("Møde B"), video("Møde C", "unchecked")
    # One video dropped: the setup view as always
    drop(root, [a])
    assert app._video() == a and not app.vq.active and not app.vq.box.winfo_manager()
    assert app.start_btn.cget("text") == i18n.t("gui.setup.start")
    # Two more (and a text file, which is left out): the queue, with A first and the current choices for all
    drop(root, [b, c, TMP / "videos" / "notes.txt"])
    q = app.vq.q
    assert [Path(i.video).name for i in q.items] == ["Møde A.mp4", "Møde B.mp4", "Møde C.mp4"], q.items
    assert app.vq.active and app.vq.box.winfo_manager() and not app.v_card.winfo_manager()
    assert app.start_btn.cget("text") == i18n.t("queue.start")
    assert all(i.choices() == ("da", None, "da", False, False, False) for i in q.items)
    # The rows: C gets Norwegian subtitles with its own combo, B Swedish speech (and Swedish spoken is not offered
    # as speech for a Swedish video)
    rows = app.vq._rows
    assert len(rows) == 3 and str(rows[0]["subs"].get()) == i18n.t("queue.subs_item", name=DA.word("choice"))
    rows[2]["subs"].current([lb for _, lb in app.vq._subs_items()].index(
        i18n.t("queue.subs_item", name=NO.word("choice"))))
    rows[2]["subs"].event_generate("<<ComboboxSelected>>")
    rows[1]["speech"].current(2)   # Ingen, Svensk, Norsk, Engelsk (not Danish, which is spoken): Norsk
    rows[1]["speech"].event_generate("<<ComboboxSelected>>")
    pump(root, 0.3)
    assert q.items[2].subs == "no" and q.items[0].subs == "da" and q.items[1].subs == "da", [i.subs for i in q.items]
    sp = q.items[1].speech
    assert sp and sp != "da", sp
    rows[1]["speech"].current(0)   # back to no new speech (B is only subtitled)
    rows[1]["speech"].event_generate("<<ComboboxSelected>>")
    pump(root, 0.3)
    assert q.items[1].speech is None
    # Reordering: drag C's grip onto the first row, then Alt+Down twice puts it back last
    app.vq._drag_start(2)
    app.vq._drag_end(type("E", (), {"y_root": app.vq._rows[0]["frame"].winfo_rooty() + 5})())
    assert [Path(i.video).stem for i in q.items] == ["Møde C", "Møde A", "Møde B"], q.items
    app.vq.move(0, 1)
    app.vq.move(1, 1)
    assert [Path(i.video).stem for i in q.items] == ["Møde A", "Møde B", "Møde C"]
    assert q.items[2].subs == "no"
    # Card 2 is "for all the videos": the per-video choices are hidden there
    speech_row = app.c_speech.master   # the combo and Hør stemmen's button (voice_preview) share a frame
    assert not speech_row.winfo_manager() and not app.plan_row.winfo_manager() and app.vq.opts.winfo_manager()
    assert pump(root, 10, until=lambda: all(app.vq._durations.get(i.video) for i in q.items)), app.vq._durations
    pump(root, 0.5)
    assert i18n.t("queue.start_count", n=3, length=queue_view.length_text(3 * 18000)) in app.start_hint.cget("text")
    # Forbedr lyden speaks of all the videos in the queue (design review round 4, decision 6)
    assert app.cb_improve.hint.cget("text") == i18n.t("audio.queue_hint"), app.cb_improve.hint.cget("text")
    shot(root, f"setup-{THEME}")
    # Start: every video is checked first (no questions here: nothing exists yet), then the rounds run
    app._start()
    assert app.vq.running and app._running and not told, told
    deadline = time.time() + 900
    while app.vq.running:
        assert time.time() < deadline, "the queue did not finish in 15 minutes"
        pump(root, 0.2)
    pump(root, 1.0)
    print("rounds:", rounds)
    print("claude rounds:", claude_rounds)
    assert rounds == [("Møde B", "listen"), ("Møde B", "claude"), ("Møde C", "claude"),
                      ("Møde A", "make"), ("Møde B", "make"), ("Møde C", "make")], rounds
    assert claude_rounds[0] == ("Møde B.mp4", True, []) and claude_rounds[1] == ("Møde C.mp4", True, ["no"])
    items = app.vq.summary_items
    assert [i.state for i in items] == ["done"] * 3, [(i.state, i.message) for i in items]
    for i, sub in zip(items, (DA, DA, NO)):
        want = gui.burned_name(Path(i.video), sub)
        assert Path(i.result) == want and want.exists() and want.stat().st_size > 10000, (i.result, want)
    assert app.view == "done" and app._last_result == ("queue", None)
    assert app.d_title.cget("text") == i18n.t("queue.done_title")
    assert len(app.created) >= 5 and not app.d_files.winfo_manager(), app.created
    assert app.vq.summary is not None and app.d_new.cget("text") == i18n.t("queue.new_queue")
    assert not (common._DATA_DIR / "queue.json").exists()
    assert not told, told
    shot(root, f"done-{THEME}")
    # A theme switch draws the summary again
    app._apply_theme("night" if THEME == "light" else "light")
    pump(root, 0.5)
    assert app.view == "done" and app.d_title.cget("text") == i18n.t("queue.done_title")
    # Dropping videos on the summary starts a new choice with just those (not the last video of the queue)
    d1, d2 = video("Ny 1"), video("Ny 2")
    drop(root, [d1, d2])
    assert app.view == "setup" and [Path(i.video).stem for i in app.vq.q.items] == ["Ny 1", "Ny 2"], app.vq.q.items
    app.vq.remove(1)
    assert not app.vq.active and app._video() == d1 and app.start_btn.cget("text") == i18n.t("gui.setup.start")

elif SCENARIO == "cancel":
    a, b = video("Stop A", "checked"), video("Stop B", "checked")
    app.vq.add([a, b])
    app._start()
    assert pump(root, 60, until=lambda: app._running and app.runner)
    app.runner.cancel()      # as Annullér after its question
    assert pump(root, 60, until=lambda: not app.vq.running)
    pump(root, 0.5)
    items = app.vq.summary_items
    assert [i.state for i in items] == ["cancelled", "waiting"], [i.state for i in items]
    assert any(i18n.t("queue.drop_rest") == m for m in asked), asked
    assert app.d_title.cget("text") == i18n.t("queue.stopped_title")
    assert str(app.d_again.cget("text")) == i18n.t("queue.retry", n=2)
    shot(root, f"cancelled-{THEME}")

elif SCENARIO == "resume":
    a, b = video("Igen A", "checked"), video("Igen B", "checked")
    # Left by a crash in the middle of round 1 of B; A had finished (nothing to make: text only)
    q = queue_view.VideoQueue()
    q.add([a, b], ("da", None, None, False, False, False))
    q.items[0].state, q.items[0].phase, q.items[0].result = "done", 3, str(gui.srt_name(a, DA))
    q.items[1].state = "running"
    q.running = True
    q.current = (q.items[1].id, 0)
    q.save()
    root.destroy()
    asked.clear()
    root = tk.Tk()
    app = gui.App(root)
    _screen.place(root)
    assert pump(root, 20, until=lambda: app.view == "done"), "no summary after resume"
    assert any("2" in m for m in asked if m == i18n.t("queue.resume_ask", n=2, left=1)), asked
    assert [i.state for i in app.vq.summary_items] == ["done", "done"]
    assert not (common._DATA_DIR / "queue.json").exists()

elif SCENARIO == "auto":
    # Cut first with Claude's suggestions taken as they are (no review), and "Sluk pc'en" when done (not for real)
    a, b = video("Auto A", "checked"), video("Auto B", "checked")
    reviews, powered = [], []
    gui.App._start_review = lambda self, *a, **kw: reviews.append(a)
    queue_view.power_off = lambda kind: powered.append(kind)
    queue_view.AfterDialog.SECONDS = 2
    app.vq.add([a, b])
    app.cut.set(True)
    app._refresh()
    assert app.vq.cb_auto.instate(["!disabled"])
    app.vq.cb_auto.invoke()   # as a click: remembered in the settings
    assert app.vq.auto_cuts.get()
    app.vq.after_box.current(1)
    app._start()
    assert pump(root, 300, until=lambda: not app.vq.running), "the queue did not finish"
    assert pump(root, 6, until=lambda: powered), "no shutdown after the countdown"
    assert powered == ["shutdown"] and not reviews, (powered, reviews)
    assert [r for r in rounds] == [("Auto A", "claude"), ("Auto B", "claude"), ("Auto A", "make"), ("Auto B", "make")], rounds
    assert all(i.state == "done" and i.no_cut for i in app.vq.summary_items)
    assert app.settings.queue_auto_cuts is True

elif SCENARIO == "summary":
    # A long queue's summary with every kind of ending: the list scrolls instead of running off the window
    items = []
    for n in range(1, 13):
        v = TMP / "videos" / f"Webinar del {n}.mp4"
        it = queue_view.Item(str(v), "da", "sv" if n % 3 == 0 else None, "no")
        it.state = {4: "error", 7: "skipped", 11: "cancelled"}.get(n, "done")
        it.result = str(gui.dub_name(v, LANG_BY_CODE["sv"], NO) if n % 3 == 0 else gui.burned_name(v, NO))
        it.message = {4: "ffmpeg kunne ikke læse videoen."}.get(n, "")
        items.append(it)
    items[-1].state = "waiting"
    app.vq.summary_items, app.vq.summary_stopped = items, True
    app._show_result("queue", None)
    pump(root, 1.0)
    canvas = app.vq.summary.winfo_children()[0]
    bar = app.vq.summary.winfo_children()[1]
    assert bar.winfo_manager() and canvas.winfo_pixels(canvas.cget("height")) < canvas.winfo_children()[0].winfo_reqheight()
    assert str(app.d_again.cget("text")) == i18n.t("queue.retry", n=4)
    shot(root, f"done-mixed-{THEME}")

root.destroy()
shutil.rmtree(TMP, ignore_errors=True)
print("all ok")
