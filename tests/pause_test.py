"""Pause/resume/close on a CPU run: whisper's CPU time must stand still while paused."""
import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import tkinter as tk
from tkinter import messagebox
from subadubalingo import gui
messagebox.askyesno = lambda *a, **k: True
for n in ("showwarning", "showerror", "showinfo"):
    setattr(messagebox, n, lambda *a, **k: print("MSG", a))

def cpu():
    return subprocess.run(["powershell", "-NoProfile", "-c",
                           "$p=Get-Process whisper-cli -EA 0; if($p){'{0:N1}s {1}' -f $p.CPU,$p.PriorityClass}"],
                          capture_output=True, text=True).stdout.strip()

def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        root.update(); time.sleep(0.05)

root = tk.Tk(); app = gui.App(root); app.settings.use_gpu = False
app.video.set(sys.argv[1]); app.subs.set("Ingen"); app.redo.set(True); app._refresh(); root.update()
app._start(); pump(12)
print("running:", cpu())
app._toggle_pause(); pump(1); a = cpu(); pump(5); b = cpu()
print("paused:", a, "->", b, "|", app.status.cget("text"))
app._toggle_pause(); pump(5); print("resumed:", cpu())
app._toggle_pause(); pump(1); app._on_close()
time.sleep(1); print("after close while paused:", cpu() or "no whisper process")
