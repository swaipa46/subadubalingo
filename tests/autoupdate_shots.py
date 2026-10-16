"""The self-update in the window: screenshots of the "new version is ready" band (idle, and during a run), the
"updated" band after the restart, and Indstillinger with "Søg efter opdateringer"; then checks that the band survives
a theme switch, that "Genstart nu" does nothing during a run, and that it switches, releases the single-instance
mutex, starts the new exe with --updated-from and closes the window when idle (update.switch/restart are faked).
Runs in a sandbox profile (no real settings or installation) on the monitor the user is not working on, captured
with PrintWindow.
  python tests/autoupdate_shots.py [OUT_ROOT] --theme light|night [--lang da|sv|no|en] [--ui ASSETS_UI_DIR]
OUT_ROOT defaults to D:\\SubaDubaLingo\\design\\implemented; files go to <OUT_ROOT>\\<theme>\\autoupdate-*.png"""
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-autoupdate-shots-"))
for k, sub in (("LOCALAPPDATA", "la"), ("APPDATA", "ad"), ("USERPROFILE", "home")):
    os.environ[k] = str(T / sub)
    (T / sub).mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import autoupdate, gui, i18n, installer, theme, update  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced_theme, forced_lang = arg("--theme", "light"), arg("--lang", "da")
out_root = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
    else Path(r"D:\SubaDubaLingo\design\implemented")
out = out_root / forced_theme
out.mkdir(parents=True, exist_ok=True)
suffix = "" if forced_lang == "da" else f"-{forced_lang}"
theme.resolve = lambda choice: forced_theme
if "--ui" in sys.argv:
    # The design session's newer assets/ui (e.g. the uncommitted frames/update-banner in the main checkout), with
    # this branch's fonts and texts: junctions in a temp assets folder
    import subprocess
    mixed = T / "assets"
    mixed.mkdir()
    here = Path(__file__).resolve().parent.parent / "assets"
    for name, target in (("ui", Path(arg("--ui", ""))), ("fonts", here / "fonts"), ("locales", here / "locales"),
                         ("branding", here / "branding")):
        subprocess.run(["cmd", "/c", "mklink", "/J", str(mixed / name), str(target)], check=True,
                       capture_output=True)
    theme.assets_dir = lambda: mixed
i18n.resolve = lambda choice: forced_lang
gui.Settings.save = lambda self: None
theme.load_fonts()
assert str(gui.Settings.path).startswith(str(T)), gui.Settings.path

# An update staged and waiting; no network at all
installer.running_installed = lambda: True
update.ready = lambda current=None: {"version": "2.1.0", "notes": "Hurtigere klip og en ny stemme.", "skill": 5}
update.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in this test"))
switched, restarted = [], []
update.switch = lambda v: switched.append(v) or Path(r"C:\fake\2.1.0\SubaDubaLingo.exe")
update.restart = lambda exe, old: restarted.append((exe, old))
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def pump(root, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.03)


def shot(win, name):
    pump(win, 0.8)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    path = out / f"autoupdate-{name}{suffix}.png"
    _screen.grab_window(hwnd).save(path)
    print("saved", path)


root = tk.Tk()
app = gui.App(root)
_screen.place(root)
pump(root, 1.5)   # the band comes 800 ms after start
st = app._autoupdate
check(st.frame is not None and st.frame.winfo_ismapped(), "the band is shown when an update is staged")
shot(root, "banner")

app._running = True
pump(root, 1.3)
shot(root, "banner-busy")
autoupdate.restart_now(app)
check(not switched and root.winfo_exists(), "Genstart nu does nothing during a run")
app._running = False

dlg = gui.SettingsDialog(root, app.settings)
area = _screen.other_work_area()
if area:
    dlg.geometry(f"+{area[0] + 60}+{area[1] + 60}")
shot(dlg, "settings")
dlg.destroy()

# The new copy after the restart: "Opdateret fra 2.0.0 til version ..."
st.ready, st.updated_from = None, "1.9.10"
autoupdate.build(app)
shot(root, "updated")
autoupdate._close_updated(app)
check(st.frame is None, "OK closes the updated band")

# Senere hides it for this session
st.ready = update.ready()
autoupdate.build(app)
autoupdate._later(app)
check(st.frame is None, "Senere hides the band")
st.dismissed = False
autoupdate.build(app)

# After a theme switch the band is built again (last: switching back and forth breaks the 9-slice frames in the
# app as on main, so no screenshots after this)
app._apply_theme("night" if forced_theme == "light" else "light")
pump(root, 0.3)
check(st.frame is not None and st.frame.winfo_exists(), "the band is rebuilt after a theme switch")

# Genstart nu when idle: switch, release the mutex, start the new exe, close
gui.main.mutex = ctypes.windll.kernel32.CreateMutexW(None, False, gui.SINGLE_INSTANCE + ".test")
autoupdate.restart_now(app)
try:
    alive = bool(root.winfo_exists())
except tk.TclError:
    alive = False
check(switched == ["2.1.0"] and restarted and restarted[0][1] == gui.APP_VERSION and gui.main.mutex is None
      and not alive, f"Genstart nu: switched, mutex released, new exe started, window closed ({restarted})")
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
