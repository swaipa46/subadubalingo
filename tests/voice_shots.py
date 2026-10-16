"""Screenshots of Lyt til stemmen: the play button next to Ny tale in the setup view (idle, and while a sample
plays), no button with the original speech, and the button next to the voice options in Indstillinger. Also
clicks the button once for real (Piper speaks the sample into a temporary data folder; the sound is not played).
Windows open on the monitor the user is not working on and are captured with PrintWindow.

  python tests/voice_shots.py [OUT_ROOT] --theme light|night      (OUT_ROOT/<theme>/voice-*.png)
"""
import ctypes
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tkinter as tk

import _screen
from subadubalingo import common

tmp = Path(tempfile.mkdtemp(prefix="sdl-voice-shots-"))
common._DATA_DIR = tmp                     # never the real ~/.subadubalingo (models are still found in the old ones)
from subadubalingo import engine, gui, i18n, theme, voice_preview as vp  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
forced = arg("--theme", "light")
args = [a for a in args if a != forced]
root_out = Path(args[0]) if args else Path(r"D:\SubaDubaLingo\design\implemented")
out = root_out / forced
out.mkdir(parents=True, exist_ok=True)
theme.resolve = lambda choice: forced
i18n.resolve = lambda choice: "da"
gui.Settings.save = lambda self: None
theme.load_fonts()
played = []
vp.play_sound = lambda p: played.append(p)      # never play sound in a test


def pump(win, sec):
    t = time.time()
    while time.time() - t < sec:
        win.update()
        time.sleep(0.03)


def shot(win, name, settle=0.6):
    pump(win, settle)
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    _screen.grab_window(hwnd).save(out / f"voice-{name}.png")
    print("saved", out / f"voice-{name}.png")


def until_part(win, btn, part):
    """Pump until part of the sample has played (a loaded machine makes Tk slow, so not by a fixed time)."""
    b = btn.bubble
    while btn.state_name == "playing" and time.monotonic() - b._start < part * b._total:
        win.update()
        time.sleep(0.02)
    assert btn.state_name == "playing", "the sample ended before the shot"


root = tk.Tk()
app = gui.App(root)
app.settings.voice_mode = "person"
_screen.place(root)
video = tmp / "Call with David.mp4"
shutil.copy(r"C:/Users/karyl/Cowork/Video subtitles test/Call with David Gabelgaard-20260930_134558-Meeting Recording.mp4",
            video)
app.video.set(str(video))
app._refresh(lang_changed=True)
pump(root, 0.3)
assert not app.voice_btn.winfo_manager(), "no button with the original speech"
shot(root, "setup-original")
app.speech.set(gui.LANG_BY_CODE["sv"].name)
app._refresh()
assert app.voice_btn.winfo_manager()
# The tooltip names the voices as people say them (design review round 4, decision 5)
sv = gui.LANG_BY_CODE["sv"]
assert app.voice_btn._tip() == gui.t("voice.listen", singular=sv.singular, voices=gui.t(
    "voice.and", a=f"Nst ({gui.t('voice.male')})", b=f"Lisa ({gui.t('voice.female')})")), app.voice_btn._tip()
print("tooltip:", app.voice_btn._tip())
shot(root, "setup")

# One real click: only the Swedish voices on this machine are installed, so sample lisa and alma (the dub's own
# voices), spoken by Piper into the temporary data folder
lisa, alma = vp.SampleVoice("sv_SE-lisa-medium"), vp.SampleVoice("sv_SE-alma-medium")
real_sample_voices = vp.sample_voices
if engine.find_model("sv_SE-lisa-medium.onnx") and engine.find_model("sv_SE-alma-medium.onnx"):
    vp.sample_voices = lambda lang, mode: [lisa, alma]
    app.voice_btn.invoke()
    assert app.voice_btn.state_name == "making"
    t0 = time.time()
    while app.voice_btn.state_name == "making" and time.time() - t0 < 30:
        pump(root, 0.1)
    assert app.voice_btn.state_name == "playing" and played and played[0].name.startswith("pair-"), played
    print(f"sample made in {time.time() - t0:.1f} s: {played[0].name} ({vp.wav_seconds(played[0]):.1f} s)")
    assert app.voice_btn.bubble.winfo_manager(), "the bubble shows while the sample plays"
    until_part(root, app.voice_btn, 0.4)
    shot(root, "setup-playing", 0.1)
else:
    print("no Swedish voices installed: no playing shots")
if app.voice_btn.state_name == "playing":
    app.voice_btn.invoke()      # second click stops
assert app.voice_btn.state_name == "idle" and not app.voice_btn.bubble.winfo_manager()
# A change of speech language back to the original hides the button
app.speech.set(app._speech_items(app._lang())[0][1])
app._refresh()
assert not app.voice_btn.winfo_manager()
app.speech.set(gui.LANG_BY_CODE["sv"].name)
app._refresh()

vp.sample_voices = real_sample_voices
dlg = gui.SettingsDialog(root, app.settings, on_theme=app.set_theme, on_language=app.set_language,
                         get_models=app._get_voice_models)
area = _screen.other_work_area()
if area:
    dlg.geometry(f"+{area[0] + 60}+{area[1] + 60}")
shot(dlg, "settings")
vp.sample_voices = real_sample_voices          # the real voices in the rows' texts; the cached sample plays
vp.needed_files = lambda voices: []
vp.make_sample = lambda runner, lang, mode: played[0]
sv_btn = dlg.voice_rows[[l.code for l in gui.LANGUAGES].index("sv")]
if played:
    sv_btn.invoke()             # from the cache now: plays at once
    pump(dlg, 0.3)
    assert sv_btn.state_name == "playing" and sv_btn.bubble.winfo_manager()
    until_part(dlg, sv_btn, 0.5)
    assert sv_btn.bubble.name.cget("text") == sv.name   # the language first, in Semibold
    shot(dlg, "settings-playing", 0.1)
    dlg.voice_mode.set("one")   # another voice mode stops the sample
    assert sv_btn.state_name == "idle" and not sv_btn.bubble.winfo_manager()
dlg.destroy()
root.destroy()
shutil.rmtree(tmp, ignore_errors=True)
print("all ok")
