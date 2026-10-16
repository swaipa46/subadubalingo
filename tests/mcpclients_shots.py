"""Screenshots of Indstillinger's card "Brug med andre AI-værktøjer" in one theme: the card open with each client
picked, into design/implemented/<theme>/mcpclients-<client>.png (and mcpclients-<client>-en.png for --lang en).
Windows open on the monitor the user is not working on (the dev venv makes them invisible) and are captured with
PrintWindow. The data folder is a temp folder; the launcher path is a fixed example user's.
  python tests/mcpclients_shots.py --theme light|night [--lang da|sv|no|en] [--out OUT_ROOT]"""
import ctypes
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(HERE))
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
THEME, LANG = arg("--theme", "light"), arg("--lang", "da")
OUT = Path(arg("--out", ROOT / "design" / "implemented")) / THEME
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="sdl-mcpclients-"))

from subadubalingo import common  # noqa: E402
common._DATA_DIR = TMP / "data"
common._DATA_DIR.mkdir()
import tkinter as tk  # noqa: E402

import _screen  # noqa: E402
from subadubalingo import gui, i18n, installer, mcpclients, theme  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
theme.resolve = lambda choice: THEME
i18n.resolve = lambda choice: LANG
gui.Settings.save = lambda self: None
# The path every installed copy shows, for a user called "kasper" (the screenshot must not depend on this machine)
installer.launcher_path = lambda: Path(r"C:\Users\kasper\AppData\Local\Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe")
theme.load_fonts()


def pump(win, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        win.update()
        time.sleep(0.03)


def main():
    root = tk.Tk()
    app = gui.App(root)
    _screen.place(root)
    area = _screen.other_work_area() or _screen.monitors()[0][1]
    gui.SettingsDialog.last_open = "aitools"
    tail = "" if LANG == "da" else f"-{LANG}"
    for key in ("claude_code", "gemini", "codex", "vscode", "cursor", "claude_desktop"):
        mcpclients.ClientsCard.last = key
        dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language,
                                 on_report=app.open_report)
        dlg.geometry(f"+{area[0] + 40}+{area[1] + 20}")
        pump(dlg, 1.5)
        dlg._body[0].yview_moveto(1)   # the card is the last one: in view when Indstillinger scrolls
        pump(dlg, 0.4)
        card = dlg.aitools
        assert card.opened and card.choice.get() == key, (card.opened, card.choice.get())
        if key != "claude_desktop":
            shown = card.text.get("1.0", "end").strip()
            assert shown == mcpclients.snippet(key, installer.launcher_path()).strip(), shown
        hwnd = ctypes.windll.user32.GetParent(dlg.winfo_id())
        dest = OUT / f"mcpclients-{key.replace('_', '-')}{tail}.png"
        _screen.grab_window(hwnd).save(dest)
        print("saved", dest)
        dlg.destroy()
    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
