"""installer.install's progress callbacks (T12): the steps come in STEPS order, the copy reports a byte fraction that
grows to 1, "claude" only when connecting, and install() without a callback still works. Runs in a sandbox:
LOCALAPPDATA, APPDATA and USERPROFILE point into a temp folder; the program folder is a small fake one.
  python tests/test_install_progress.py"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="sdl-progress-"))
for k, sub in (("LOCALAPPDATA", "la"), ("APPDATA", "ad"), ("USERPROFILE", "home")):
    os.environ[k] = str(T / sub)
    (T / sub).mkdir()
(T / "ad" / "Claude").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import installer  # noqa: E402  (after the environment)

ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


src = T / "zip" / "SubaDubaLingo"
(src / "_internal").mkdir(parents=True)
(src / "models").mkdir()
(src / installer.EXE_NAME).write_bytes(b"MZ" + b"x" * 200_000)
(src / "_internal" / "base_library.zip").write_bytes(b"z" * 300_000)
for i in range(20):
    (src / "_internal" / f"lib{i}.pyd").write_bytes(b"p" * 10_000)
(src / "models" / "voice.onnx").write_bytes(b"m" * 1000)
installer.app_dir = lambda: src
installer._update_shortcuts = lambda exe: None   # PowerShell and the real Start menu are not the point here

events = []
try:
    exe = installer.install(True, progress=lambda step, frac: events.append((step, frac)))
    steps = [s for s, _ in events]
    order = [s for i, s in enumerate(steps) if i == 0 or steps[i - 1] != s]
    check(order == list(installer.STEPS), f"steps in order: {order}")
    copies = [f for s, f in events if s == "copy"]
    check(copies and copies[0] == 0.0 and copies[-1] == 1.0 and copies == sorted(copies),
          f"copy fraction grows 0..1 ({len(copies)} reports)")
    check(exe.is_file() and (installer.install_dir() / installer.CURRENT).read_text() == installer.APP_VERSION,
          "installed and current")
    check((Path(os.environ["USERPROFILE"]) / ".subadubalingo" / "models" / "voice.onnx").is_file(), "models moved")

    events.clear()
    shutil.rmtree(installer.install_dir() / installer.APP_VERSION)
    installer.install(False, progress=lambda step, frac: events.append((step, frac)))
    check("claude" not in {s for s, _ in events}, "no Claude step without connecting")

    shutil.rmtree(installer.install_dir() / installer.APP_VERSION)
    check(installer.install(False).is_file(), "install() without a callback")

    def boom(step, frac):
        raise RuntimeError("callback")
    shutil.rmtree(installer.install_dir() / installer.APP_VERSION)
    check(installer.install(False, progress=boom).is_file(), "a failing callback does not stop the install")
finally:
    shutil.rmtree(T, ignore_errors=True)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
