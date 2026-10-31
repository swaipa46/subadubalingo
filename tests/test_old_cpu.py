"""whisper.cpp on a processor without AVX2 stops with STATUS_ILLEGAL_INSTRUCTION (0xC000001D; the 2026-10-31 smoke test
on an i5-3470S): the user is told the PC is too old, not to read a log file. Any other failure keeps the general
message. No whisper is run: the runner is a fake.
  python tests/test_old_cpu.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sdl-test-oldcpu-"))
common._DATA_DIR = tmp / "data"
common._DATA_DIR.mkdir()

from subadubalingo import engine, i18n  # noqa: E402
from subadubalingo.common import LANG_BY_CODE, Runner, ToolError  # noqa: E402
from subadubalingo.i18n import t  # noqa: E402

i18n.set_language("da")
ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


class FakeRunner(Runner):
    def __init__(self, code):
        super().__init__()
        self.code, self.calls = code, 0

    def run(self, args, on_line=None, **kw):
        self.calls += 1
        return self.code, ""


def error_for(code):
    r = FakeRunner(code)
    try:
        engine.transcribe(r, tmp / "a.wav", LANG_BY_CODE["da"], tmp / "model.bin", 18_000, False,
                          engine.Progress(lambda *a: None, [("t", 1)]), tmp)
    except ToolError as e:
        return str(e)
    return ""


check(error_for(engine.ILLEGAL_INSTRUCTION) == t("engine.error.old_cpu"), "illegal instruction: the PC is too old")
check(error_for(1) == t("engine.error.transcribe"), "any other exit code: the general message")
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
