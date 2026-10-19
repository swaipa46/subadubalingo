"""The test run the CI workflows use (.github/workflows/ci.yml), also runnable on a dev PC:

  python tests/ci_run.py unit  MEDIA_DIR            every tests/test_*.py
  python tests/ci_run.py shots MEDIA_DIR OUT_ROOT   the GUI screenshot scripts that need no Whisper model, both themes

MEDIA_DIR is made by tests/make_test_clip.py (the 18 s clip with its subtitles, and a 45 s clip); SDL_TEST_VIDEO
points the scripts that have Kasper's recording built in at the clip. Each script runs in its own process; it fails
the run when it exits non-zero, and a unit test also when it prints no "ok" line. Every script runs even after a
failure, and the failures are listed at the end. On a dev PC the scripts find the models in ~/.subadubalingo, so a
run there is not proof that CI has what it needs: point USERPROFILE at an empty folder to check that.

Not run here, because they need what a CI machine does not have:
  screenshot.py, one_go.py, claude_one_go.py, smoke.py, pause_test.py   real transcription (a Whisper model)
  mcp_test.py, cancel_test.py                                             the MCP server's real job (Whisper)
  queue_flow.py --scenario main                                           video B is listened to (Whisper), so
                                                                          round4_shots.py runs with --skip queue_flow.py
  install_test.py, update_test.py                                         two built program folders (an old Videosprog)
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from make_test_clip import NAME

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OK = re.compile(r"(^|\s)(all ok|ok)\b|\bok$", re.I | re.M)


def run(label: str, args: list[str], need_ok: bool, env: dict, timeout: int = 900) -> str | None:
    """Runs one script; returns None when it passed, else why not."""
    print(f"::group::{label}", flush=True)
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = p.stdout + p.stderr
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"{e.stdout or ''}{e.stderr or ''}\nTIMEOUT after {timeout} s", -1
    print(out.rstrip(), flush=True)
    print("::endgroup::", flush=True)
    why = None
    if code != 0:
        why = f"exit code {code}"
    elif need_ok and not OK.search(out):
        why = "no 'ok' line in the output"
    print(f"{'FAIL' if why else 'PASS'} {label} ({time.time() - t0:.0f} s){': ' + why if why else ''}", flush=True)
    if why:
        # Annotations are readable through the public API without a login (job logs are not), so the developer on
        # McHoense can see why a run failed without gh being logged in.
        tail = "\n".join(out.rstrip().splitlines()[-40:])[-3500:]
        tail = tail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={label} failed ({why})::{tail}", flush=True)
    return why


def unit(media: Path, env: dict) -> list[str]:
    clip, long_clip = media / f"{NAME}.mp4", media / f"{NAME} 45s.mp4"
    extra = {"test_audio.py": [str(clip)], "test_cutting.py": [str(long_clip)]}
    failed = []
    only = os.environ.get("SDL_CI_ONLY") or "test_*.py"   # workflow_dispatch input "only": rerun one test fast
    for t in sorted(HERE.glob(only)):
        why = run(t.name, [str(t), *extra.get(t.name, [])], True, env)
        if why:
            failed.append(f"{t.name}: {why}")
    return failed


def shots(media: Path, out: Path, env: dict) -> list[str]:
    clip = str(media / f"{NAME}.mp4")
    failed = []
    for theme in ("light", "night"):
        o = out / theme
        runs = [
            ("language_shots.py", [str(o / "language"), "--theme", theme, "--lang", "da"]),
            ("install_ui_shots.py", ["--theme", theme]),          # always into ROOT/design/implemented/<theme>
            ("mcpclients_shots.py", ["--theme", theme, "--out", str(out)]),
            ("autoupdate_shots.py", [str(out), "--theme", theme]),
            ("report_shots.py", [str(o / "report"), "--theme", theme]),
            ("audio_shots.py", [str(o / "audio"), "--theme", theme, "--video", clip]),
            ("burnin_shots.py", [str(o / "burnin"), "--theme", theme]),
            ("claude_shots.py", [clip, str(out), "--theme", theme]),
            ("voice_shots.py", [str(out), "--theme", theme]),
            ("editor_shot.py", [clip, str(o / "editor"), "--theme", theme]),
            ("review_shot.py", [clip, str(o / "review"), "--theme", theme]),
            *[("queue_flow.py", [str(o / f"queue-{sc}"), "--theme", theme, "--scenario", sc])
              for sc in ("cancel", "resume", "auto", "summary")],
            ("round4_shots.py", ["--theme", theme, "--out", str(out), "--skip", "queue_flow.py"]),
        ]
        for script, args in runs:
            scenario = args[args.index("--scenario") + 1] if "--scenario" in args else ""
            label = f"{script} {theme} {scenario}".strip()
            why = run(label, [str(HERE / script), *args], False, env)
            if why:
                failed.append(f"{label}: {why}")
    return failed


def main():
    mode, media = sys.argv[1], Path(sys.argv[2]).resolve()
    env = dict(os.environ, SDL_TEST_VIDEO=str(media / f"{NAME}.mp4"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1")   # a native crash (Tk) still shows where it was
    if mode == "unit":
        failed = unit(media, env)
    elif mode == "shots":
        failed = shots(media, Path(sys.argv[3]).resolve(), env)
    else:
        sys.exit(f"unknown mode {mode}")
    if failed:
        print(f"\n{len(failed)} failed:\n  " + "\n  ".join(failed))
        sys.exit(1)
    print("\nall ok")


if __name__ == "__main__":
    main()
