"""Every MCP client, not only Claude (version 2.1.1), without the GUI and without a client. Run:
  python tests/test_mcp_clients.py

- the guide and the Claude skill share their text (the skill of version 6 is unchanged, its word list is the guide's);
- a raw JSON-RPC session over stdio against the source server (python app/main.py --mcp), as Gemini CLI would hold
  it: initialize announces tools, prompts and resources; the tool "guide" (whole and per section) in the app's UI
  language with the word list; prompts/list and prompts/get; resources/list and resources/read; errors for unknown
  names; find_videos; requests sent just before stdin closes are still answered;
- Claude-only parts by client: no skill note or skill.json for Gemini, "restart <client>" after an update;
- the setup per client (mcpclients.snippet): valid JSON or TOML, pointing at the launcher, also for a user name
  with an apostrophe;
- the launcher (tools/mcp_launcher.cs, compiled here with .NET Framework's csc): it runs current.txt's version with
  --mcp and passes stdin, stdout and the exit code through byte for byte, writes nothing of its own to stdout,
  fails on stderr when nothing is installed, takes the server down when it is killed; installer.update_launcher
  copies it, also over one that is running.
Sandbox: LOCALAPPDATA, APPDATA and USERPROFILE point into a temp folder (never the real installation or settings).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = Path(tempfile.mkdtemp(prefix="sdl-mcpclients-"))
for k, sub in (("LOCALAPPDATA", "la"), ("APPDATA", "ad"), ("USERPROFILE", "home")):
    os.environ[k] = str(T / sub)
    (T / sub).mkdir()
sys.path.insert(0, str(ROOT / "app"))
from subadubalingo import common  # noqa: E402

common._DATA_DIR = T / "data"
common._DATA_DIR.mkdir()
from subadubalingo import claude, guide, installer, jobs, mcp_server, mcpclients  # noqa: E402
from subadubalingo.common import Cue  # noqa: E402

ok = True


def check(cond, what):
    global ok
    print(("OK   " if cond else "FAIL ") + what)
    ok &= bool(cond)


# --- The guide and the skill share one text -------------------------------------------------------------------------
# The skill of version 6, byte for byte. If SKILL_MD changes meaningfully, bump claude.SKILL_VERSION (the app then
# asks the user to upload the new skill) and put the new hash here.
SKILL_V6 = "dc75669567557c4a5cb6e3ab16736e0d51c7c8a3dac57b1d36906a92640c9e9a"
check(hashlib.sha256(claude.SKILL_MD.encode()).hexdigest() == SKILL_V6 and claude.SKILL_VERSION == 6,
      "SKILL_MD is still the skill of version 6")
check(guide.TASK in claude.SKILL_MD and guide.TOOLS_INTRO in claude.SKILL_MD and guide.TOOLS_REST in claude.SKILL_MD,
      "the skill is built from the guide's rules and steps")
whole = guide.guide_text("Swedish", ["Membercare", "Lex"], [("memberkær", "Membercare")])
check(guide.TASK in whole and guide.TOOLS_INTRO in whole and guide.TOOLS_REST in whole,
      "the guide holds the same rules and steps")
check("skill: 6" not in whole and "Talk to the user in Swedish" in whole and "## Never" in whole
      and "## Job states" in whole, "the guide: no skill version, the user's language, states, never")
check(guide.words_md(["flow", "Membercare"], [("memberkær", "Membercare"), ("fløv", "flow")]) ==
      "# Word list\n\nKnown words (spelled exactly like this in every language):\n\n- flow\n- Membercare\n\n"
      "Typical mishearings by the speech recognition:\n\n- memberkær => Membercare\n- fløv => flow\n",
      "words.md keeps its form")
claude.skill_zip(T / "skill.zip")   # with the default corrections.txt of the temp data folder
words_md = zipfile.ZipFile(T / "skill.zip").read("subadubalingo/words.md").decode("utf-8")
check("- Membercare" in words_md and " => " in words_md
      and mcp_server.tool_guide("words").endswith(words_md.replace("# Word list", "## Word list")),
      "the guide's word list is the skill's words.md (the user's corrections.txt)")

# --- A raw stdio session against the source server --------------------------------------------------------------
home = T / "srv-home"
(home / ".subadubalingo").mkdir(parents=True)
(home / ".subadubalingo" / "settings.json").write_text(json.dumps({"ui_language": "sv"}), encoding="utf-8")
videos = T / "videos"
videos.mkdir()
(videos / "webinar.mp4").write_bytes(b"x")
(videos / "notes.txt").write_text("x")
env = dict(os.environ, USERPROFILE=str(home), PYTHONIOENCODING="utf-8")


class Server:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, str(ROOT / "app" / "main.py"), "--mcp"], cwd=str(ROOT), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.id = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.id += 1
            msg["id"] = self.id
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        return self.id

    def call(self, method, params=None):
        rid = self.send(method, params)
        while True:
            line = self.p.stdout.readline()
            assert line, "the server closed stdout"
            msg = json.loads(line)   # every line on stdout is JSON-RPC
            if msg.get("id") == rid:
                return msg

    def tool(self, name, **args):
        r = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], r.get("isError", False)


s = Server()
init = s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "gemini-cli-mcp-client", "version": "0.37.2"}})["result"]
s.send("notifications/initialized", notify=True)
check(set(init["capabilities"]) == {"tools", "prompts", "resources"}, f"capabilities {init['capabilities']}")
check("'guide'" in init["instructions"] and "Swedish" in init["instructions"],
      "the instructions point to the guide and name the user's language")
tools = {t["name"]: t for t in s.call("tools/list")["result"]["tools"]}
check(set(tools) == set(mcp_server.HANDLERS) and list(tools)[0] == "guide", f"tools {list(tools)}")
check("guide (once) -> start_video -> wait" in tools["start_video"]["description"]
      and "claude_turn" in tools["wait"]["description"] and "filler" in tools["suggest_cuts"]["description"]
      and "ask the user first" in tools["approve_cuts"]["description"], "the descriptions carry the workflow")
check(tools["guide"]["inputSchema"]["properties"]["section"]["enum"] == list(guide.SECTIONS)
      and not tools["guide"]["inputSchema"].get("required"), "guide: an optional section")
text, err = s.tool("guide")
check(not err and text.startswith("# SubaDubaLingo: the manual") and "Talk to the user in Swedish" in text
      and "- Membercare" in text and "`filler`" in text and "## Chapters" not in text and "save_chapters" in text,
      "guide: the whole manual in the user's language, with the word list")
for sec in guide.SECTIONS:
    part, err = s.tool("guide", section=sec)
    check(not err and len(part) < len(text) and "Talk to the user in Swedish" in part, f"guide section {sec}")
part, err = s.tool("guide", section="nope")
check(err and "Unknown section" in part, "guide: an unknown section is an error the model can read")
prompts = s.call("prompts/list")["result"]["prompts"]
check([p["name"] for p in prompts] == ["subadubalingo_workflow"]
      and all(not a["required"] for a in prompts[0]["arguments"]), "prompts/list")
got = s.call("prompts/get", {"name": "subadubalingo_workflow",
                             "arguments": {"video": "C:\\v\\a.mp4", "request": "Swedish subtitles"}})["result"]
msg = got["messages"][0]
check(msg["role"] == "user" and msg["content"]["type"] == "text" and msg["content"]["text"].startswith(text.rstrip())
      and msg["content"]["text"].endswith("What I want: Swedish subtitles\nThe video: C:\\v\\a.mp4"), "prompts/get")
bad = s.call("prompts/get", {"name": "nope"})
check(bad.get("error", {}).get("code") == -32602, "prompts/get: an unknown prompt is -32602")
res = s.call("resources/list")["result"]["resources"]
check([r["uri"] for r in res] == ["subadubalingo://guide"] + [f"subadubalingo://guide/{x}" for x in guide.SECTIONS]
      and all(r["mimeType"] == "text/markdown" for r in res), "resources/list")
for r in res:
    c = s.call("resources/read", {"uri": r["uri"]})["result"]["contents"][0]
    check(c["uri"] == r["uri"] and c["text"].startswith("# SubaDubaLingo"), f"resources/read {r['uri']}")
check(s.call("resources/read", {"uri": "subadubalingo://other"}).get("error", {}).get("code") == -32602,
      "resources/read: an unknown uri is -32602")
check(s.call("resources/templates/list")["result"] == {"resourceTemplates": []}, "resources/templates/list")
check(s.call("nope/nope").get("error", {}).get("code") == -32601, "an unknown method is -32601")
found, err = s.tool("find_videos", folder=str(videos))
check(not err and "webinar.mp4" in found and "notes.txt" not in found, "find_videos in a folder")
# A client that sends its last requests and closes stdin at once still gets every answer
ids = [s.send("ping") for _ in range(3)] + [s.send("tools/call", {"name": "guide", "arguments": {"section": "never"}})]
s.p.stdin.close()
rest = [json.loads(line) for line in s.p.stdout.read().splitlines() if line.strip()]
check(sorted(m["id"] for m in rest) == ids and s.p.wait(10) == 0, "requests before EOF are answered, exit 0")
err_out = s.p.stderr.read().decode("utf-8", "replace")
check("Fatal" not in err_out and "Traceback" not in err_out, "nothing on stderr " + err_out[:200])

# An older client (protocol 2024-11-05) that knows nothing of prompts: tools only
s = Server()
init = s.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "some-client", "version": "1"}})["result"]
check(init["protocolVersion"] == "2024-11-05", "the client's protocol version is answered")
text, err = s.tool("guide", section="steps")
check(not err and "start_video" in text, "an older client: the guide tool works")
s.p.stdin.close()
s.p.wait(10)

# --- Claude-only parts by client ----------------------------------------------------------------------------------
job = "clientjob"
src = [Cue(i * 5000, i * 5000 + 3000, f"Blok {i + 1}.") for i in range(3)]
d = jobs.job_dir(job)
jobs.write_json(d / "source.json", jobs.cues_to_json(src))
jobs.write_json(d / "task.json", {"src": "da", "check": True, "targets": [], "codes": ["da"], "blocks": 3,
                                  "words": [], "cut": False, "duration": 15_000, "rules": []})
mcp_server._client.update(name="gemini-cli-mcp-client", title="Gemini CLI")
text = mcp_server.tool_get_text(job)
check(not text.startswith("NOTE") and not (d / "skill.json").exists(),
      "Gemini without a skill: no skill note, no skill.json (the app shows no Claude skill guide)")
mcp_server._client.update(name="claude-ai", title="claude-ai")
text = mcp_server.tool_get_text(job)
check(text.startswith("NOTE: You do not have the subadubalingo skill") and (d / "skill.json").exists(),
      "Claude without the skill: the note and skill.json as before")
fake = T / "la" / "fake" / "2.1.1" / "SubaDubaLingo.exe"
fake.parent.mkdir(parents=True)
(fake.parent.parent / "current.txt").write_text("2.1.2", encoding="utf-8")
mcp_server._EXE = fake
mcp_server._client.update(name="gemini-cli-mcp-client", title="Gemini CLI")
msg = mcp_server._outdated() or ""
check("to version 2.1.2" in msg and "restart Gemini CLI" in msg and "right-click" not in msg,
      "after an update: Gemini is told to restart Gemini CLI")
mcp_server._client.update(name="claude-ai", title="claude-ai")
check("right-click the Claude icon" in (mcp_server._outdated() or ""), "after an update: Claude desktop as before")
mcp_server._EXE = None

# --- The setup per client ---------------------------------------------------------------------------------------
for user in ("kasper", "O'Brien"):
    launcher = Path(f"C:\\Users\\{user}\\AppData\\Local\\Programs\\SubaDubaLingo\\SubaDubaLingo-mcp.exe")
    exe = launcher.parent / "2.1.1" / "SubaDubaLingo.exe"
    snips = mcpclients.snippets(launcher, exe)
    check(set(snips) == set(mcpclients.KEYS), "a setup for every client")
    for key, text in snips.items():
        if key == "claude_code":
            okc = text == f'claude mcp add --transport stdio --scope user subadubalingo -- "{launcher}"'
        elif key == "codex":
            entry = tomllib.loads(text)["mcp_servers"]["subadubalingo"]
            okc = (entry["command"] == str(launcher) and entry["args"] == []
                   and entry["tool_timeout_sec"] >= 60 and entry["startup_timeout_sec"] >= 10)
        else:
            data = json.loads(text)
            top = "servers" if key == "vscode" else "mcpServers"
            entry = data[top]["subadubalingo"]
            want = (str(exe), ["--mcp"]) if key == "claude_desktop" else (str(launcher), [])
            okc = list(data) == [top] and (entry["command"], entry["args"]) == want
            if key in ("vscode", "cursor"):
                okc &= entry["type"] == "stdio"
            if key == "gemini":
                okc &= "type" not in entry
        check(okc, f"setup {key} ({user}): valid, points at the {'exe' if key == 'claude_desktop' else 'launcher'}")
check(set(mcpclients.KEYS) == {k[len("mcpclients."):-len(".where")]
                               for k in json.loads((ROOT / "assets/locales/da/mcpclients.json").read_text("utf-8"))
                               if k.endswith(".where")}, "a where-to-paste text for every client")
keys = None
for code in ("da", "sv", "no", "en"):
    k = list(json.loads((ROOT / "assets" / "locales" / code / "mcpclients.json").read_text("utf-8")))
    keys = keys or k
    check(k == keys, f"mcpclients.json {code}: the same keys")

# --- The launcher -----------------------------------------------------------------------------------------------
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
STUB = r"""
using System; using System.IO;
static class Stub {
    static int Main(string[] args) {
        string pidFile = Environment.GetEnvironmentVariable("STUB_PIDFILE");
        if (!string.IsNullOrEmpty(pidFile)) File.WriteAllText(pidFile, System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
        var o = Console.OpenStandardOutput();
        byte[] head = System.Text.Encoding.UTF8.GetBytes("ARGS:" + string.Join("|", args) + "\n");
        o.Write(head, 0, head.Length);
        var i = Console.OpenStandardInput();
        var buf = new byte[65536]; int n;
        while ((n = i.Read(buf, 0, buf.Length)) > 0) o.Write(buf, 0, n);
        o.Flush();
        return 7;
    }
}
"""


def compile_cs(src: Path, out: Path):
    r = subprocess.run([str(CSC), "/nologo", "/target:winexe", "/optimize+", f"/out:{out}", str(src)],
                       capture_output=True)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


def alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


if not CSC.is_file():
    print("SKIP the launcher: no csc.exe (.NET Framework 4) on this machine")
else:
    build = T / "build"
    build.mkdir()
    (build / "stub.cs").write_text(STUB, encoding="utf-8")
    compile_cs(ROOT / "tools" / "mcp_launcher.cs", build / installer.LAUNCHER_NAME)
    compile_cs(build / "stub.cs", build / installer.EXE_NAME)
    base = installer.install_dir()
    check(str(base).startswith(str(T)), "the install folder is in the sandbox")
    vdir = base / "2.1.1"
    vdir.mkdir(parents=True)
    shutil.copy(build / installer.EXE_NAME, vdir / installer.EXE_NAME)   # the "app" is the stub
    shutil.copy(build / installer.LAUNCHER_NAME, vdir / installer.LAUNCHER_NAME)
    launcher = installer.launcher_path()
    # Nothing installed yet: an error on stderr, nothing on stdout
    shutil.copy(build / installer.LAUNCHER_NAME, launcher)
    r = subprocess.run([str(launcher)], input=b"x", capture_output=True, timeout=30)
    check(r.returncode == 1 and r.stdout == b"" and b"not installed" in r.stderr,
          f"no current.txt: exit 1, stdout empty, stderr {r.stderr[:80]!r}")
    launcher.unlink()
    (base / "current.txt").write_text("2.1.1", encoding="utf-8")
    check(installer.update_launcher("2.1.1") and launcher.read_bytes() == (vdir / installer.LAUNCHER_NAME).read_bytes(),
          "update_launcher copies it from the version folder to the base folder")
    payload = b"".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping", "params": {"x": "æøå" * 50}}).encode()
                       + b"\n" for i in range(4000))   # ~1 MB, more than any pipe buffer
    t0 = time.time()
    r = subprocess.run([str(launcher), "extra arg", 'q"uote'], input=payload, capture_output=True, timeout=60)
    check(r.stdout == b"ARGS:--mcp|extra arg|q\"uote\n" + payload,
          f"stdout is exactly the app's: --mcp first, arguments passed on, {len(payload)} bytes through "
          f"({time.time() - t0:.2f} s)")
    check(r.returncode == 7 and r.stderr == b"", f"the app's exit code (7) passed on, nothing on stderr {r.stderr!r}")
    # Killing the launcher takes the server with it (job object)
    pidfile = T / "stub.pid"
    p = subprocess.Popen([str(launcher)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         env=dict(os.environ, STUB_PIDFILE=str(pidfile)))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    stub_pid = int(pidfile.read_text())
    check(alive(stub_pid), "the server runs under the launcher")
    # While a client runs it, a new launcher is renamed aside, not refused
    (base / "2.1.2").mkdir()
    shutil.copy(vdir / installer.EXE_NAME, base / "2.1.2" / installer.EXE_NAME)
    (base / "2.1.2" / installer.LAUNCHER_NAME).write_bytes(launcher.read_bytes() + b"\0")
    check(installer.update_launcher("2.1.2") and launcher.read_bytes().endswith(b"\0")
          and len(list(base.glob(installer.LAUNCHER_NAME + ".old-*"))) == 1,
          "a launcher in use is moved aside and the new one put in place")
    p.kill()
    p.wait(10)
    for _ in range(40):
        if not alive(stub_pid):
            break
        time.sleep(0.1)
    check(not alive(stub_pid), "killing the launcher stops the server")
    (base / "current.txt").write_text("2.1.2", encoding="utf-8")
    installer.cleanup()
    check(not list(base.glob(installer.LAUNCHER_NAME + ".old-*")), "cleanup removes the old launcher once free")

shutil.rmtree(T, ignore_errors=True)
print("all ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
