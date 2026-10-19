"""The screenshots for design review round 4 (design/review/round-4.md) in one theme: runs the feature shot scripts
into a temp folder and copies the shots that show each item to design/implemented/<theme>/round4-<name>.png, then
shoots Indstillinger on a short monitor (the scroll box with its pixel scroll bar) itself.

  python tests/round4_shots.py --theme light|night [--out OUT_ROOT] [--skip SCRIPT]

--skip: leave out one feature script and its shots (CI: queue_flow.py, whose video B needs a Whisper model).

Each script runs in its own process (they replace parts of the app); a failing one stops the run. Windows open on
the monitor the user is not working on and are captured with PrintWindow.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VIDEO = Path(os.environ.get("SDL_TEST_VIDEO", "C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4"))
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
THEME = arg("--theme", "light")
OUT = Path(arg("--out", ROOT / "design" / "implemented")) / THEME
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="sdl-round4-"))

# script, its arguments, and {its shot (relative to its output folder): round4 name}
RUNS = [
    ("queue_flow.py", [str(TMP / "queue"), "--theme", THEME], {
        f"queue-setup-{THEME}.png": "queue-setup",      # grip icon, Forbedr lyden for all the videos
        f"queue-work-{THEME}.png": "queue-work",        # Lyttet igennem with step-done
        f"queue-done-{THEME}.png": "queue-done"}),      # ALL CLEAR! as wide as the card, Ny kø with the queue icon
    ("editor_shot.py", [str(VIDEO), str(TMP / "editor"), "--theme", THEME], {
        f"editor-{THEME}.png": "editor",                # Luk, pixel scroll bar, framed video, timeline handles
        f"editor-fast-{THEME}.png": "editor-fast",      # the footer's warning square
        f"editor-drag-{THEME}.png": "editor-drag",      # the dragged handle
        f"editor-narrow-{THEME}.png": "editor-narrow"}),  # a long name shortened in the middle
    ("language_shots.py", [str(TMP / "language"), "--theme", THEME, "--lang", "da"], {
        f"settings-{THEME}-da.png": "settings",         # the accordion: Tekst og stemmer open
        f"done-{THEME}-da.png": "done",                 # STAGE CLEAR! as wide as the card
        f"done-error-{THEME}-da.png": "done-error"}),   # CONTINUE? as wide as the card
    ("voice_shots.py", [str(TMP / "voice"), "--theme", THEME], {
        f"{THEME}/voice-settings.png": "voice-settings",          # names with a capital, (én stemme)
        f"{THEME}/voice-settings-playing.png": "voice-settings-playing"}),   # Svensk first in the bubble
    ("burnin_shots.py", [str(TMP / "burnin"), "--theme", THEME], {
        "burnin-settings.png": "burnin-settings",       # the sample in the frames/input border
        "burnin-settings-loading.png": "burnin-loading"}),   # the empty framed box while it is made
]


def main():
    py = sys.executable
    skip = arg("--skip", "")
    for script, args, shots in RUNS:
        if script == skip:
            print(f"--- {script}: skipped", flush=True)
            continue
        print(f"--- {script}", flush=True)
        subprocess.run([py, str(HERE / script)] + args, check=True, cwd=ROOT)
        folder = Path(args[1] if script == "editor_shot.py" else args[0])
        for src, name in shots.items():
            dest = OUT / f"round4-{name}.png"
            shutil.copyfile(folder / src, dest)
            print("copied", dest)
    print("--- Indstillinger on a short monitor", flush=True)
    subprocess.run([py, str(HERE / "round4_shots.py"), "--settings-scroll", "--theme", THEME, "--out", str(OUT.parent)],
                   check=True, cwd=ROOT)
    shutil.rmtree(TMP, ignore_errors=True)


def settings_scroll():
    """Indstillinger with Undertekster i videoen open on a 700 px tall work area: the cards scroll, with the pixel
    scroll bar in the window's colour, and the footer stays in view."""
    import ctypes
    import time
    import tkinter as tk
    sys.path.insert(0, str(ROOT / "app"))
    sys.path.insert(0, str(HERE))
    from subadubalingo import common
    common._DATA_DIR = TMP / "data"
    common._DATA_DIR.mkdir()
    import _screen
    from subadubalingo import gui, i18n, theme
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    theme.resolve = lambda choice: THEME
    i18n.resolve = lambda choice: "da"
    gui.Settings.save = lambda self: None
    theme.load_fonts()
    root = tk.Tk()
    app = gui.App(root)
    _screen.place(root)
    area = _screen.other_work_area() or _screen.monitors()[0][1]
    gui.work_area = lambda hwnd: (area[0], area[1], area[2], area[1] + 700)
    gui.SettingsDialog.last_open = "burnin"
    dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language,
                             on_report=app.open_report)
    dlg.geometry(f"+{area[0] + 40}+{area[1] + 20}")
    t0 = time.time()
    while time.time() - t0 < 2.5:
        dlg.update()
        time.sleep(0.03)
    canvas, sb, _ = dlg._body
    assert sb.winfo_manager() and "Pix" in str(dlg.tk.call("ttk::style", "layout", "Vertical.TScrollbar")), \
        "no pixel scroll bar"
    hwnd = ctypes.windll.user32.GetParent(dlg.winfo_id())
    dest = OUT / "round4-settings-scroll.png"
    _screen.grab_window(hwnd).save(dest)
    print("saved", dest)
    dlg.destroy()
    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    settings_scroll() if "--settings-scroll" in sys.argv else main()
