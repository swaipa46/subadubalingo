# SubaDubaLingo: instructions for Claude

Read `docs/HANDOVER.md` first: it is the full context (what the app is, how it is built, what was learned the hard way,
how releases and design work). This file holds the rules that apply to every session.

- **Owner:** Kasper (GitHub swaipa46). Since 2026-10-19 development is done by Claude on McHoense (Linux). The app is
  Windows-only: build and test it through GitHub Actions (`.github/workflows/`), never assume something works on
  Windows because it runs on Linux.
- **Language:** code, comments, commit messages and docs for developers in English. Everything the user sees is in the
  four UI languages (`assets/locales/{da,sv,no,en}`, Danish is the source). Notes and replies to Kasper in Danish.
- **Releases:** never publish a release yourself. Bump `APP_VERSION`, push, let `release-build` make the zip, then ask
  Kasper to run `tools\release.ps1` on his PC (it installs, walks him through a checklist, signs and publishes). The
  signing key exists only on his PC.
- **Design:** there is no separate design session (since 2026-10-19). Do the design here yourself, following the
  design system (`assets/ui/README.md`, `skin.py`, the theme tokens) and the look of what exists; check it in the CI
  screenshots in both themes. Kasper can open Claude Design if he wants mockups. Add no tasks to the design repo.
- **Users are not technical:** short, friendly, no jargon in anything they see. Videos never leave the PC; only text
  goes to the AI assistant. Say "AI-assistent"/"AI-chat" where any assistant is meant; "Claude" only where it really
  is Claude (connecting Claude desktop, the MCP client list).
- **Never** send mail or messages on Kasper's behalf, never handle passwords or tokens (he puts secrets in place
  himself), never commit secrets (the repo is public).
- **Tests:** add or update a `tests/test_*.py` for every behaviour change; CI must be green before asking for a release.
