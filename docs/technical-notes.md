# Technical notes

Design decisions, measurements and caveats behind SubaDubaLingo, moved out of the README (2026-10-31). Names from
before 2.0 (Videosprog, `vu`, Danish tool names) appear where history is described. The code map and the lessons
learned are in [HANDOVER.md](HANDOVER.md).

## Tools and data

- `bin/` (not in git, fetched by `fetch-tools.ps1`):
  - whisper.cpp, the **Vulkan build** from SubtitleEdit/support-files (whispercpp-194). It also runs on the CPU with
    `-ng`, and the app falls back to the CPU by itself when a GPU run fails. Silero VAD comes with it and is used.
  - Piper 2023.11.14-2 (`--json-input`, one run for all blocks).
  - ffmpeg essentials from gyan.dev (libass, libx264, Quick Sync). Burning in tries `h264_qsv` first.
- Data (settings, the word list `corrections.txt`, `text_state.json`, jobs, log) is in `%USERPROFILE%\.subadubalingo`,
  not in AppData: Claude desktop is an MSIX app, and everything its child processes (the MCP server and an app it
  started) write under AppData ends up in Claude's package folder. Models download to `.subadubalingo\models`;
  `models\` next to the exe and the model folders under the earlier names are used too until they are moved.
- `text_state.json` keeps a fingerprint (size and modification time) and a text version per `.srt`, so a changed video,
  or a text from a version with a known bug, is made again. Checked text is not sent to the assistant again.

## MCP server, install and update

- `SubaDubaLingo.exe --mcp` is an MCP server (JSON-RPC over stdio, `mcp_server.py`, no extra packages). The app and
  the server share jobs through files (`jobs.py`); only the app writes the status. `wait` long-polls: it answers at
  every 10 %, at the assistant's turn, a pause, an error or the end, else after 45 s.
- `cancel` (1.9.9) writes `jobs/<id>/cancel.json` and the app stops without asking; if the app is not running, a stuck
  job is set to `cancelled` directly. Before, a job stuck in `claude_turn` blocked every new job.
- **Update while Claude runs the server (1.9.9).** Windows allows renaming the install folder while the MCP server
  runs from it; PyInstaller reopens the exe on every first import, so the old server read the new exe at the old
  offsets: `zlib.error: Error -3 ... incorrect header check` (a 1.9.3 server with 1.9.8 installed, 2026-10-14). Fixed
  by refusing to install while a process runs from the folder, by the server checking the exe's size and time on
  every call, and by importing `claude` at start. Test: `tests/update_test.py OLD NEW JOB`.
- **One folder per version (1.9.10):** `Programs\SubaDubaLingo\<version>\` plus `current.txt`. An update never touches
  a folder something runs from; an old server goes on and asks for a restart once `current.txt` is not its folder.
  Shortcuts, pinned taskbar icons and Claude's config are pointed at the new version on every install, and folders
  nothing runs from are deleted at start (`installer.cleanup()`), keeping the current and the previous version.
  `SubaDubaLingo.exe --install [--connect]` installs without a UI (exit 0/2/1). Test in a sandbox with temporary
  LOCALAPPDATA/APPDATA/USERPROFILE: `tests/install_test.py OLD_DIST NEW_DIST`.
- **The launcher** `Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe` (`tools/mcp_launcher.cs`) is a path that never
  changes: it starts `current.txt`'s version with `--mcp`. MCP clients other than Claude desktop point at it.
- **The server is its own manual:** the tool descriptions carry the workflow, and the `guide` tool (also the prompt
  `subadubalingo_workflow` and the resource `subadubalingo://guide`) gives the whole manual (`guide.py`).
- **Take-over (2.2.0):** a job started in the app is published as a job in `claude_turn` (`jobs.publish_app_job`,
  `"origin": "app"`); `wait`, `status` and `get_text` without a job id find it, and `get_text` writes `taken.json`,
  after which the app adopts it as an MCP job. Answers already pasted from a chat are dropped so the two never mix;
  if every part was pasted first, the chat wins (`by_user`). Tests: `tests/test_take_over.py`,
  `tests/test_assistant_panel.py`.
- Self-update: [auto-opdatering.md](auto-opdatering.md).

## Cutting (1.9)

`cutting.py` and `review.py`.
- **Suggestions:** long quiet stretches (at least 20 s between blocks and mostly quiet by the sound curve, 250 ms
  resolution) and the assistant's lines `<block>-<block> <kind> <reason>`. The assistant cuts whole blocks, since it
  only sees text.
- **Review:** a timeline with the sound curve, speech blocks and coloured cuts; edges drag with snap to block
  boundaries, new cuts can be dragged, the mouse wheel zooms, and each cut shows a picture before and after. For MCP
  jobs the list is mirrored to `jobs/<id>/cuts.json`.
- **Cutting:** one ffmpeg pass (`select` plus `setpts` minus the sum of earlier cuts, so a variable frame rate is
  kept; the sound in small frames with a 25 ms fade at each join). The graph is in a file (`-/filter_complex`),
  because many cuts break Windows' command line limit. QSV first, else x264. Measured on a 31 min webinar
  (2026-10-12): 221 s with QSV, the length exact, the sound in sync within 10 ms.
- **Timecodes:** the subtitles move to `<name> - klippet - ... undertekster.srt`; a block more than half inside a cut
  is removed. `textstate.record_cut` remembers which original a cut video came from, so it is reused for more
  languages.

## Better sound

`audio.py` filters the original speech in the videos the app writes (the cut one, the one with burned-in subtitles
and the original under a dub). The dub's own voice is not touched.
- A first pass (ebur128 on the sound alone, about 0.3 s per minute of video) measures the loudness and the noise floor
  (the quietest tenth of the 400 ms windows).
- The filter: `afwtdn` at the measured noise level, only for steady noise at least 15 LU under the speech (Teams'
  pauses are digital silence, so Teams recordings skip it), highpass 80 Hz, one gain to -16 LUFS and a limiter
  (-1.5 dB, `latency=1`). No filter moves the sound in time (cross-correlation: 0.2 ms). Not used: `loudnorm`
  (192 kHz; 6 min for the webinar against about 30 s), `afftdn` (25 ms delay), adaptive `afwtdn`.
- Filtered videos get the comment "SubaDubaLingo: improved sound", so a cut video is not cleaned twice.
- Whisper gets the unchanged sound: on real recordings the filter made no measurable difference to the word error rate
  (2026-10-16, large-v3: 223 against 222 errors in 1733 words).

## Voices

- **Whole sentences (1.9.5, `engine.sentences`):** blocks are joined up to . ? ! or a pause over 0.7 s, and each
  sentence is spoken again at the tempo that makes it as long as the original (0.87x-1.5x, Piper's length_scale in
  steps of 0.02). Sentences that cannot make it even at 1.5x push the next ones a little.
- **Male/female (1.9.6, `pitch.py`):** the pitch of the original speech per sentence (autocorrelation): under 160 Hz
  male, over 175 Hz female, else as the sentence before.
- **One voice per person (1.9.7, default, `speakers.py`):** a voice print per sentence (sherpa-onnx, WeSpeaker
  ResNet34, 26 MB; only sentences of 3 s or more), average-linkage clusters (cosine 0.66); two clusters with centroids
  at least 0.75 alike and under 8 Hz apart in pitch are merged. Voices are handed out by speaking time: the language's
  own voices first, then the same shifted ±2.5/4.5 semitones (ffmpeg asetrate+atempo).

## Measurements (Kasper's i7-13850HX, RTX 2000 Ada, Intel UHD; 2026-10-12)

60 s of a webinar:

| Model / device | Time |
|---|---|
| large-v3 q5_0, CPU 8 threads / 20 threads | 55 s / 43 s |
| large-v3-turbo q5_0, CPU 8 threads / 20 threads | 37 s / 26 s |
| turbo, Vulkan on Intel UHD (32 EU) | 42 s |
| turbo, Vulkan on RTX 2000 | 3 s |

- large-v3 was clearly better on three of four sampled minutes, so it is the default; turbo is "Hurtigst". The whole
  31 min webinar with large-v3 on the RTX 2000: 2.7 min; turbo on 8 CPU threads: about 70 min.
- Every model mishears domain words ("memberkær" for Membercare) and hallucinates TV subtitle credits, so known
  hallucinations are filtered and the word list corrects words afterwards. Whisper gets no vocabulary prompt: it made
  things worse.
- Burning in the whole webinar with Quick Sync: 3.5 min; a dub with 397 blocks including burning in: 5.7 min.

## Caveats

- **App Control for Business (WDAC)** is on at EG: the program and tools are unsigned, so whether they may run depends
  on the policy. The start-up self-check reports a blocked tool.
- Testing from a Claude desktop session: what it writes under `%LOCALAPPDATA%` lands in Claude's MSIX package folder,
  so an exe started by hand does not see those models, logs or settings.
- Running from source (also the tests that do not set a temp data folder) uses the real `~/.subadubalingo`.
- Dialogs open centred over the app (`place_over`, within the work area); message boxes are moved over their parent
  by a WH_CBT hook that must not call Tk. Asking for attention flashes the taskbar without taking the focus.
