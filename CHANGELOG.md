# Changelog

What changed for the users of SubaDubaLingo, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).
The release notes users see (on GitHub Releases and in the app's update band) are short Danish summaries of these
entries.

## [2.3.0] - 2026-10-31

### Added
- **"Din tur" takes the whole window.** When the run waits for the user in the AI-chat round, the steps, the
  percentage and Pause go away, and the window shows three numbered steps and "Programmet venter på dig · trin n af
  m". The cut review gets a DIN TUR tag and says it waits for "Klip videoen". (A user watched the percentage stand
  still and did not see that it was her turn.)
- **A Windows notification by the clock** when it is the user's turn, "DIN TUR" in the title bar and taskbar, the
  friendly notification sound instead of the error bell, and one reminder after two minutes if the window is still
  not in front.
- **Waiting animations:** a blinking gamepad in the idle AI card, a "typing" bubble while a connected AI assistant
  works, a dropping arrow while models download, and turning dots while a subtitle or voice sample is made.

### Fixed
- Settings no longer shrinks to a sliver when every card is folded. (Built as 2.2.1, which was not released.)
- The cut review's "Klip videoen" button is always visible, also in a small window.
- The idle AI card's sentence is no longer cut off at both sides.

## [2.2.0] - 2026-10-21

### Added
- Any AI chat (ChatGPT, Gemini, ...) can do the AI assistant's turn: "Kopiér instruktioner til AI-chat", and the
  answer is found on the clipboard.
- A connected AI assistant can take over a job started in the app ("Tag over i SubaDubaLingo").

### Removed
- The Claude skill and the file round ("... - til/fra Claude.txt"). Users from 2.1.x are asked once to delete the old
  skill in Claude.

### Changed
- New release signing key: 2.1.x cannot update itself to 2.2.0, so it is installed by hand once.

## [2.1.1] - 2026-10-19

### Added
- Works with other AI tools than Claude (Settings -> "Brug med andre AI-værktøjer"), through a launcher whose path
  never changes.
- A livelier installer with progress bars and a new done screen.

### Fixed
- Claude desktop's setup is only rewritten while Claude is not running (Claude overwrote it with its old copy).

## [2.1.0] - 2026-10-16

### Added
- The app in Danish, Swedish, Norwegian and English.
- The arcade design with a light and a dark theme.
- A subtitle editor, a queue of videos, better sound, filler words as cuts, chapters and a description from the AI
  assistant, the look of burned-in subtitles, voice samples, live picture and sound while dragging a cut, error
  reports.
- Self-update: from 2.1.0 the app updates itself from GitHub Releases.

## 2.0.0 - 2026-10-15

### Changed
- New name, SubaDubaLingo (was Videosprog, and Video undertekster before that), and English as the language of the
  code, the MCP tools and the protocol with the AI assistant. The installer moves the program, data, shortcuts and
  Claude's setup over from the old names.

## Before 2.0

1.0 to 1.9.x (as "Video undertekster" and "Videosprog") built the core: transcription, burned-in subtitles, dubbing
with one voice per speaker, cutting with a review, the MCP server and one folder per installed version. The details
are in the git history and [docs/technical-notes.md](docs/technical-notes.md).

[2.3.0]: https://github.com/swaipa46/subadubalingo/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/swaipa46/subadubalingo/compare/v2.1.1...v2.2.0
[2.1.1]: https://github.com/swaipa46/subadubalingo/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/swaipa46/subadubalingo/releases/tag/v2.1.0
