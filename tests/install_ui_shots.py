"""The install window's T12 screens with a fake installer (sleeps and progress callbacks, no files touched): the
progress page with each status line, the done page with and without the Claude note, and the error page (the app
window is open, and a worker that fails). Checks on the way: INSERT COIN comes first, every line stays at least
~2 s, the bar only grows, and the title bar's close does nothing mid-copy. Saves
design/implemented/<theme>/install-*.png (windows are captured with PrintWindow; the dev venv hides them).
  python tests/install_ui_shots.py [--theme light|night] [--lang da|sv|no|en]"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk

import _screen
from subadubalingo import i18n, installer, skin, theme

ctypes.windll.shcore.SetProcessDpiAwareness(1)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
themes = [arg("--theme", "")] if "--theme" in sys.argv else ["light", "night"]
lang = arg("--lang", "da")
i18n.set_language(lang)
theme.load_fonts()
installer.app_window_open = lambda: False
started = []
installer.subprocess.Popen = lambda *a, **k: started.append(a)   # "Start SubaDubaLingo" starts nothing here
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


# The fake install: (step, seconds, reports); the copy reports a growing fraction
TIMELINE = [("start", 0.2, 1), ("copy", 5.0, 40), ("check", 0.3, 1), ("models", 2.6, 4), ("shortcuts", 0.3, 1),
            ("claude", 2.6, 1), ("cleanup", 0.6, 1), ("final", 0.2, 1)]


def fake_install(connect, progress=None, fail_at=None):
    for step, sec, n in TIMELINE:
        if step == "claude" and not connect:
            continue
        if step == fail_at:
            raise OSError(28, "Der er ikke nok plads på disken")
        for i in range(n):
            progress(step, (i + 1) / n if n > 1 else None)
            time.sleep(sec / n)
    return Path(sys.executable)   # a file that exists: the final check passes


def labels(w):
    out = []
    for c in w.winfo_children():
        try:
            if c.winfo_class() in ("Label", "TLabel") and c.cget("text"):
                out.append(str(c.cget("text")))
        except tk.TclError:
            pass
        out += labels(c)
    return out


def headline(root):
    heads = {i18n.t(f"installer.line.{s}.head"): s for s in installer.STEPS}
    return next((heads[x] for x in labels(root) if x in heads), None)


def new_window(name):
    root = tk.Tk()
    root.title(installer.APP_NAME)
    skin.apply(root, name, root.winfo_fpixels("1i") / 96)
    skin.dialog(root)
    result = {"go": True}
    installer._main_page(root, "2.0.0", result)
    _screen.place(root)
    root.update()
    return root, result


def shot(root, path):
    root.update()
    time.sleep(0.15)
    root.update()
    _screen.grab_window(ctypes.windll.user32.GetParent(root.winfo_id())).save(path)
    print("saved", path.relative_to(ROOT))


def run(root, out, connect, prefix=None, until=40):
    """Pump the window through an install; a shot of each status line 0.6 s after it shows. Returns the lines seen
    with how long each stayed."""
    seen, t0, last, values = [], time.monotonic(), None, []
    while time.monotonic() - t0 < until:
        try:
            root.update()
        except tk.TclError:
            break
        h = headline(root)
        if h != last:
            now = time.monotonic()
            if seen:
                seen[-1][1] = now - seen[-1][1]
            if h is None:
                break
            seen.append([h, now])
            last = h
            shot_due = now + 0.6
        if prefix and last and seen[-1][1] and time.monotonic() >= shot_due:
            shot(root, out / f"{prefix}-{last}.png")
            shot_due = float("inf")
            if last == "copy":   # mid-copy: the close button does nothing
                cmd = root.tk.call("wm", "protocol", root._w, "WM_DELETE_WINDOW")
                root.tk.call(cmd)
                root.update()
                check(root.winfo_exists(), "closing mid-copy is ignored")
        bars = [w for w in _walk(root) if isinstance(w, installer._Bar)]
        if bars:
            values.append(bars[0].value)
        time.sleep(0.03)
    check(values == sorted(values), "the bar only grows")
    return [(s, round(d, 1)) for s, d in seen]


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def press(root, text):
    btn = next(w for w in _walk(root) if w.winfo_class() == "TButton" and w.cget("text") == text)
    btn.invoke()
    try:
        root.update()
    except tk.TclError:
        pass


def gone(root) -> bool:
    try:
        root.winfo_exists()
        return False
    except tk.TclError:
        return True


def wait_for(root, text, sec=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        root.update()
        if any(text in x for x in labels(root)):
            return True
        time.sleep(0.03)
    return False


for name in themes:
    out = ROOT / "design" / "implemented" / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"--- {name}")

    # Install with Claude: every line, then LEVEL UP with the Claude note
    root, result = new_window(name)
    installer.install = fake_install
    installer._run_install(root, "2.0.0", result, True)
    seen = run(root, out, True, prefix="install-progress")
    print("lines:", seen)
    check(seen and seen[0][0] == "start", "INSERT COIN first")
    # measured from the first pump, which comes a moment after the line was put up
    slack = {"start": 0.5}   # the first line was up before the first pump
    check(all(d >= installer.MIN_LINE_S - slack.get(s, 0.3) for s, d in seen), "each line at least ~2 s")
    order = [s for s, _ in seen]
    check(order == sorted(order, key=installer.STEPS.index), "lines in step order")
    check("shortcuts" not in order and "final" not in order, "steps done within another line's 2 s are skipped")
    check(wait_for(root, i18n.t("installer.restart.title")), "done page with the Claude note")
    shot(root, out / "install-done.png")
    # Start SubaDubaLingo: starts the installed exe and closes
    press(root, i18n.t("installer.start", app=installer.APP_NAME))
    check(bool(started), "Start starts the installed exe")
    check(result["go"] is False, "the zip copy does not start after the install")
    check(gone(root), "and the window closes")

    # Without Claude: no note, no phone line
    root, result = new_window(name)
    installer._run_install(root, "2.0.0", result, False)
    seen = run(root, out, False)
    check("claude" not in [s for s, _ in seen], "no Player 2 line without Claude")
    check(wait_for(root, i18n.t("installer.done.title", version=installer.APP_VERSION)), "done page")
    check(not any(i18n.t("installer.restart.title") in x for x in labels(root)), "no Claude note")
    shot(root, out / "install-done-no-claude.png")
    root.destroy()

    # The app window is open: the error screen at once, with the mockup's text; Prøv igen then installs
    root, result = new_window(name)
    installer.app_window_open = lambda: True
    installer._run_install(root, "2.0.0", result, True)
    check(wait_for(root, i18n.t("installer.app_open", app=installer.APP_NAME)), "app open: error screen")
    check(any(i18n.t("installer.fail.unchanged") in x for x in labels(root)), "says nothing changed")
    shot(root, out / "install-failed.png")
    installer.app_window_open = lambda: False
    press(root, i18n.t("installer.retry"))
    check(wait_for(root, i18n.t("installer.line.start.head"), 3), "Prøv igen starts over")
    run(root, out, True)
    check(wait_for(root, i18n.t("installer.done.title", version=installer.APP_VERSION)), "then done")
    root.destroy()

    # The worker fails after the files were in place: the real error, and no "nothing changed"
    root, result = new_window(name)
    installer.install = lambda connect, progress=None: fake_install(connect, progress, fail_at="cleanup")
    installer._run_install(root, "2.0.0", result, True)
    check(wait_for(root, "ikke nok plads", 30), "worker error shown")
    check(not any(i18n.t("installer.fail.unchanged") in x for x in labels(root)), "no 'nothing changed' after activate")
    shot(root, out / "install-failed-worker.png")
    cmd = root.tk.call("wm", "protocol", root._w, "WM_DELETE_WINDOW")
    root.tk.call(cmd)
    check(gone(root), "the title bar's close works on the error screen")

print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
