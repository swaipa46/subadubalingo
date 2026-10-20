# Handover: SubaDubaLingo (2026-10-19)

From Claude Code on Kasper's Windows PC (which built versions 1.x to 2.1.1) to Claude on McHoense, which takes over all
further development. Read this once fully; `CLAUDE.md` holds the standing rules.

## What it is

A Windows desktop app that makes subtitles and dubs of videos and cuts recordings, locally on the PC, with an AI
assistant correcting, translating and suggesting cuts (any AI chat the user may use, or an AI tool that drives the app
through MCP, Claude first among them). Users: a business unit at EG (Kasper's employer) that
records Teams meetings and webinars; the first user is Anne. Users are not technical and use Danish, Swedish, Norwegian
or English. Public repo swaipa46/subadubalingo; website https://subadubalingo.swaipa.dk/ (on McHoense).

Earlier names: "Video undertekster" (< 1.7), "Videosprog" (1.7-1.9.x). The installer migrates installs, shortcuts,
data and Claude's config from those names.

## How a video goes through the app

1. Setup view: choose video(s) (one or a queue), spoken language, speech (keep original or dub to a language),
   subtitles, options (cut first, better sound, ...).
2. Work: extract audio (ffmpeg) -> transcribe (whisper.cpp, `bin/whisper`) -> **the assistant's turn**: correct the
   source subtitles, translate, suggest cuts (incl. filler words), chapters and a description -> **the user's turn**:
   the cut review (timeline, live scrub picture and sound, legend) -> cut (ffmpeg) -> voice the dub (Piper,
   `bin/piper`, voice per speaker via sherpa-onnx speaker embeddings) -> mix and burn subtitles (ffmpeg).
3. Done view: result card with stats, the run's files (subtitle editor for .srt), what next.

The assistant's turn has two modes (spec: `docs/assistant-panel.md`):
- **MCP**: the app is a local stdio MCP server (`--mcp`, `mcp_server.py`). Claude (or any MCP client)
  calls start_video, wait, get_text, save_text, suggest_cuts, approve_cuts, cancel, find_videos, guide. The server
  is its own manual (`guide.py`: the `guide` tool, prompt `subadubalingo_workflow`, resources
  `subadubalingo://guide[/section]`). The app shows progress in the right-hand card (MCP view), with a way out to the
  chat view.
- **Any AI chat** (the default for a job started in the app): the right-hand card ("AI-assistent", `ClaudePanel`'s
  chat view) copies self-contained instructions per part (`claude.prompt`: rules, word list and answer format, no
  product or skill named) and watches the clipboard for the answer (also "Indsæt svar"). The answer is one code block
  with `=== <code> ===` per language, `=== cuts/chapters/summary/words ===`; `claude.check_answer` also reads markdown
  headings, bold labels and a code block per language (ChatGPT, Gemini). The panel finishes by itself when every
  part is in. It also nudges MCP users ("Tag over i SubaDubaLingo"): the app job is published as a `claude_turn` job
  (`jobs.publish_app_job`), `status`/`wait`/`get_text` without a job id find it, and `get_text` takes it over
  (`taken.json`; the card switches to the MCP view and `App.adopt_job` makes it an MCP job). Pasted chat answers are
  dropped on a take-over; if the user pasted every part first, the chat wins (`by_user`).
- Up to 2.1.x there was also a Claude skill with a task/answer file round; it was removed in 2.2.0 (owner's decision
  2026-10-19). `upgrade_notice.py` asks upgraders once to delete the old skill in Claude; the MCP guide tells an
  assistant to ignore it.

## Code map (`app/subadubalingo/`)

| Module | What |
|---|---|
| `gui.py` | Main window (App), setup/work/done views, ClaudePanel, SettingsDialog, done view, main() |
| `review.py`, `scrub.py` | Cut review, live scrub (thumbnails 4/s, audio snippets via winsound) |
| `editor.py` | Subtitle editor |
| `queue_view.py` | Queue of videos |
| `engine.py`, `cutting.py`, `audio.py`, `burnin.py`, `voice_preview.py`, `speakers.py`, `pitch.py` | Media work |
| `claude.py`, `guide.py`, `mcp_server.py`, `jobs.py`, `textstate.py` | The assistant side (`jobs.py` also holds the take-over of app jobs) |
| `upgrade_notice.py` | The one-time "delete the old Claude skill" notice after an update from 2.1.x |
| `installer.py`, `update.py`, `autoupdate.py`, `ed25519.py` | Install, self-update, signature check |
| `report.py` | Error reports to the relay (see below) |
| `common.py`, `i18n.py`, `theme.py`, `skin.py`, `pickers.py` | Shared: Language, file names, data dir; UI language; design system |

Data: `~/.subadubalingo` (settings.json, corrections.txt word list, models/, reports/, log.txt). Install:
`%LOCALAPPDATA%\Programs\SubaDubaLingo\<version>\` + `current.txt` + the stable MCP launcher
`SubaDubaLingo-mcp.exe` (C#, `tools/mcp_launcher.cs`, compiled by build.ps1 with Windows' csc).

## Design system

- Two themes: Lys (a-light) and Nat (b-night), arcade pixel style. Everything in `assets/ui` (tokens.json colours by
  role, 9-slice frames, pixel icons at 1x/@2x, art) and `assets/fonts` (Press Start 2P, VT323, OFL). Spec:
  `assets/ui/README.md`.
- `skin.apply()` builds the ttk styles; `skin.Card` (card with title inside), `skin.IconButton`, `Main.Accent.TButton`
  (48 px main action), pickers in `pickers.py`. A theme or language switch rebuilds the window.
- UI languages da/sv/no/en: `assets/locales/<code>/<area>.json`, flat keys, `i18n.t("area.key", **kw)`. New feature =
  new area file. Never upper/lowercase language names in code (`lang.json` forms). Output file names follow the UI
  language (`file.json`) and are found in any language (`find_srt`, `find_cut`).

## Learned the hard way (do not relearn)

- **sv_ttk** recolours every widget via tk_setPalette on `<<ThemeChanged>>` after the window is built; `skin.apply`
  runs `configure_colors` at once, unbinds it and clears the option db. sv_ttk also has its own `Card.TFrame`.
- **ttk image elements** use the image's own size as minimum (inputs were 48 px, buttons 56 px): `skin._element` sets
  width/height to the slice borders.
- **Theme switch there and back** lost all frames: one image cache per theme (`theme.Images`), because ttk keeps the
  elements per theme.
- **Claude desktop overwrites its config** (`%APPDATA%\Claude\claude_desktop_config.json`) with its in-memory copy when
  it saves preferences. An install that changed it while Claude ran was undone and Cowork started the old Videosprog.
  `installer.keep_claude_pointed` only writes while Claude desktop is not running (waits in a thread otherwise) and runs
  at every app start. Claude desktop is an MSIX app (`WindowsApps\Claude_...\app\claude.exe`); Claude Code's
  `claude.exe` is not it.
- **PyInstaller onedir** reopens the exe on first import of a module: never replace a version folder something runs
  from (zlib error -3). Hence versioned folders and `running_from()`.
- **Node refuses to spawn .cmd** files (EINVAL): MCP clients need a real exe, hence the C# launcher.
- **Tk does not paint windows outside every monitor**: test screenshots use PrintWindow (`tests/_screen.grab_window`) on
  a real monitor. On Kasper's PC the dev venv's sitecustomize makes test windows transparent and click-through; never
  put test windows on his working screen.
- **SmartScreen** warns on first run of the unsigned exe from the web (users: "Mere info" -> "Kør alligevel").
  Self-updates do not trigger it. Code signing for an individual in Denmark: Azure Artifact Signing is US/CA only for
  individuals; options are EG IT distribution or a company. Not decided.
- **Gemini CLI** could not be tested: Google refused the login ("no longer supported for Gemini Code Assist for
  individuals"). Claude Code is tested through the launcher.
- Whisper: do not give it a vocabulary/initial prompt (it hallucinates names); corrections happen after, via the
  word list and the assistant.
- Windows memory pressure on Kasper's PC can make tests fail with fork/OpenBLAS errors; rerun singly before
  concluding a test is broken.

## Releases and self-update

- `update.py`: installed copies check `releases/latest/download/latest.json` once a day, verify its ed25519
  signature with `PUBLIC_KEY`, download the zip (sha256 and size from the manifest), stage it in a new version folder,
  and `autoupdate.py` shows "Genstart for at opdatere".
- The release carries `SubaDubaLingo-<version>.zip`, `SubaDubaLingo.zip` (fixed name for the website) and the signed
  `latest.json`.
- **Process (see docs/release.md), since 2026-10-20 on McHoense:** you bump `APP_VERSION`, CI green, `release-build`
  makes the zip, `tools/release.py prepare` fetches it, you check it in the Windows test VM (`~/winvm`), and after
  Kasper says yes in the chat `tools/release.py publish --yes` signs (key in `~/.subadubalingo-release/`, mode 600)
  and publishes. The old way (`tools\release.ps1` on his PC) still works. Anyone on 2.0.0 or older installs 2.1.x once by hand; from 2.1.0 it updates itself.
- Released: 2.1.0 (2026-10-16), 2.1.1 (2026-10-19).

## Services on McHoense (yours now)

- **Error report relay:** `~/subadubalingo-relay` (Python stdlib, 127.0.0.1:8787, sysvinit service
  `/etc/init.d/subadubalingo-relay`, config `~/.config/subadubalingo-relay/env`, state/logs
  `~/.local/share/subadubalingo-relay`). Contract: `docs/report-relay.md`. Caddy routes
  `swaipa.dk/api/subadubalingo/v1/*` and `subadubalingo.swaipa.dk/api/...` to it. **Still in dry run**: Kasper must create
  the private repo swaipa46/subadubalingo-reports and a fine-grained token (Issues RW + Metadata R, that repo only, 1
  year), put it in the env file and set DRY_RUN=0. Clear `dry-run.jsonl` (test reports) after.
- **Website:** source `website/` in this repo, served from `/var/www/subadubalingo` (owned by repo) by Caddy
  (`dashboard-manager/scripts/public_proxy/Caddyfile`, applied with `setup_caddy.sh` as root). Deploy = copy
  `website/` (without README.md) there. The download button follows the latest GitHub release by itself.
- Root on the box: `repo` has no sudo terminal; Kasper runs root steps himself (prepare a script, he types the
  password). The router does not hairpin: test public URLs with `curl --resolve <host>:443:192.168.1.82`.

## Design

Since 2026-10-19 there is no separate design session: the developing Claude does the design here, following the design
system (`assets/ui/README.md`, `skin.py`, the theme tokens) and the look of what exists. Check it with the CI
screenshots (artifact "screenshots", both themes). Kasper can open Claude Design if he wants mockups. Do not add tasks
to swaipa46/subadubalingo-design. (History: a Claude in Cowork on Kasper's PC made mockups and reviews through
`tasks.md` there; rounds 1-4 and the feature mockups T1-T12 are done, round 3 concluded "aligned".)

## Open items

- Relay go-live (token, DRY_RUN=0) and a test report from the app -> an issue.
- SmartScreen / code signing decision (Kasper).
- Gemini CLI untested; Codex CLI, VS Code Copilot, Cursor untested ("should work").
- 2.2.0: `APP_VERSION` is 2.2.0 (matches `upgrade_notice.SKILL_REMOVED_IN`); CI on Windows, then the usual release.
  Check on Windows: the chat view, the take-over (write "Tag over i SubaDubaLingo" in Claude desktop while the app
  waits in the chat view) and the old-skill notice.
- Optional design polish from review round 3 (CUT · DUB · SUB on the first-start page, done banner width, tile checks,
  empty space under a short cut list).
- Tests write to the real `~/.subadubalingo/log.txt` (should use a temp data dir).
- `website/i18n.js` `hero.alt` still mentions STAGE CLEAR! (now ALL CLEAR!).

## History

The private vault note "Video-undertekster til Anne" on Kasper's PC has the day-by-day history. The old private
history of the project lives in KasperAtEG/videosprog (branch eg-history) and is not needed for development.
