# Assistant panel (spec, 2026-10-19)

The right-hand card on the work view, where the assistant's turn happens. Replaces `ClaudePanel`'s file mode
(removed with the skill, see the owner's decision of 2026-10-19). Two views remain:

- **Chat view** (`mode = "chat"`): a job started in the app. The user copies instructions per part into any AI chat
  and copies the answer back. This is today's parts mode, promoted to the default and reworded.
- **MCP view** (`mode = "mcp"`): the job came from an MCP assistant, or an MCP assistant took over an app job.
  Today's progress view, unchanged in look.

No new visual language: everything below is an existing widget, style, icon or colour token. Design is done in
this repo by the developing Claude, following the design system (`assets/ui/README.md`, `skin.py`, theme tokens);
there is no separate design session and no task goes to the design repo. Kasper can open Claude Design for mockups
if he wants them.

**Status (2026-10-19):** round 1 is built: the skill and the file round are gone, the chat view is the default, the
texts are neutral, the old-skill notice and the MCP side of the cleanup are in. Round 2 is open: the MCP take-over
(publishing the app job, `taken.json`, `app.adopt_job`, the phrase in the server and `guide.py`) and its tests. The
nudge line is in the panel already.

## Card and idle state

- Card title: `" Claude "` (hard-coded) becomes `t("gui.claude.card_title")`.
- Idle content unchanged (PLAYER 2 KLAR + `gui.claude.idle`), new wording.

## Header (both views)

Unchanged layout: `Big.TLabel` heading in `your_turn_title` + the small pixel "PLAYER 1", then the intro line
(`gui.claude.intro`, wrapped with `app._wrap`). Heading is `gui.claude.your_turn` ("DIN TUR!") in the chat view and
`gui.claude.working` in the MCP view.

## Chat view (job started in the app)

Top to bottom inside `self.body`:

1. **Next box** (existing `next_box`: `tk.Frame` in `P.card`, `SunValleyBodyStrongFont`, `P.heading`): the one thing
   to do now (texts below).
2. **Split hint** (`Hint.TLabel`): `gui.parts.split`, only when there is more than one part.
3. **Part list** (existing scrolling canvas). One row per part:
   - left: a step icon (`tk.Label`, `icons/step-*`, background `P.card`), exactly as the removed file-mode sub-steps;
     the active one animates `step-active-0..5` at 120 ms (keep `_spin_fsteps`, renamed `_spin_parts`);
   - title `gui.parts.title` in `SunValleyBodyStrongFont` (hidden when there is only one part);
   - buttons under the title: `skin.IconButton(icon="copy")` with `gui.parts.copy`, then a plain `ttk.Button`
     `gui.parts.paste`. No fixed `width=` (the new label is long);
   - a state line under the buttons (`ttk.Label`, wrapped).
4. **Data line** (`Hint.TLabel`): `gui.parts.data`.
5. **MCP nudge**, separated by 12 px: `Hint.TLabel` `gui.parts.mcp_nudge`, then on one row the phrase
   `gui.parts.mcp_phrase` (with `app=APP_NAME`) in `SunValleyBodyStrongFont`, quoted, and a small
   `skin.IconButton(icon="copy")` `gui.parts.copy_phrase`. Click copies the bare phrase (no quotes) and shows
   `gui.parts.copied_short` for 3 s. Copying the phrase must not be taken as an answer (add it to `_seen_clip`).

Footer: only `gui.claude.stop` (Afbryd). The switch button and "Gem og fortsæt" are gone in this view.

### Part states

| State | Icon | Title colour | Copy button | Paste button | State line | Next box |
|---|---|---|---|---|---|---|
| not copied, next | `step-active-*` | `P.step_active` | `Accent.TButton` (icon `copy-on-primary`), focus | `TButton` | empty | `gui.parts.next_copy` |
| not copied, later | `step-pending` | default | `TButton` | `TButton` | empty | – |
| copied, waiting | `step-active-*` | `P.step_active` | `TButton`, text `gui.parts.copy_again` | `Accent.TButton` | `gui.parts.copied`, `P.hint` | `gui.parts.waiting` |
| answer received | `step-done` | `P.success` | `TButton` | `TButton` | "✓ " + `gui.parts.ok` / `ok_words`, `P.success` | (next part's) |
| problem | `step-error` | `P.error` | `TButton` | `Accent.TButton` | "✗ " + `gui.parts.have` + checker message, `P.error` | `gui.parts.problem` |

- Paste-back is unchanged: clipboard watch on the waiting part (`_watch_clipboard`, needs `-->`, ignores the prompt
  and `_seen_clip`) plus the manual "Indsæt svar". A problem keeps the partial answer (`self.partial`) as today, so
  the user only needs the missing rest.
- **All parts received:** next box shows `gui.claude.answer_ok_blocks` in `P.success`, and after 1200 ms the panel
  finishes by itself (`_save_parts`). No "Gem og fortsæt" click any more (same as the old file mode).

### The prompt

`claude.prompt()` stays the source of the copied text, but must be self-contained for any chat model: drop the
"Use the ... skill" line, keep the rules, known words and output format inline. Test: the prompt names no skill and
no product ("Claude", "SubaDubaLingo-skill").

### MCP take-over (round 2 contract)

When the chat view opens, the panel publishes the app job so an MCP assistant can find it:
`jobs.job_dir(<new id>)` gets `job.json` (`{"origin": "app", "video": ...}`), `status.json` with state
`claude_turn`, and the same `source.json` / `task.json` that `_build_mcp_mode` writes today (move that into a shared
`_publish_task()`). Writes are skipped quietly if the folder cannot be written.

The server (round 2) treats "Tag over i SubaDubaLingo" / "Take over ..." as: find the active `claude_turn` job,
write `jobs/<id>/taken.json`, and continue with `get_text` as for its own jobs. `guide.py` documents the phrase.

The panel's `_tick` checks for `taken.json` newer than `self.chat_since` (the time the chat view was built). When
found: discard chat answers, call `app.adopt_job(id)` (sets `app.mcp_job`, so later states such as `review_cuts` and
`done` reach the assistant), hide the callout band, stop the pulse, put the card back to its normal style, set the
status to `gui.work.claude_in_chat`, and rebuild the body as the MCP view (without rewriting source/task). On close
or finish of an app job that was never taken, its job folder gets state `cancelled` / `done` like an MCP job.

## MCP view (job from MCP, or taken over)

Unchanged: `gui.claude.mcp_intro`, one `ttk.Progressbar` row per language with `gui.claude.corrected` /
`gui.claude.n_of`, the cut line (`gui.claude.cuts_*`) and the chapters line (`chapters.mcp_*`).

- The footer switch button becomes `gui.claude.to_chat` and opens the chat view (fresh parts). It stays: it is the
  way out when the assistant stalls or hits a usage limit. Switching sets `chat_since`, shows the callout band and
  the your-turn card as today's `_mcp_to_file` does.
- While in the chat view, `complete.json` from the assistant still finishes the round (first to finish wins), and a
  new `taken.json` (newer than `chat_since`) switches back to the MCP view.
- `skill.json` / `app.note_skill` go away.

## Texts

Key prefixes stay `gui.claude.*` / `gui.parts.*` (internal names; renaming them is churn). "Claude" stays only in
Claude-specific texts: `gui.settings.connect*`, `mcpclients.*`, installer texts about Claude desktop.

### New or changed keys

| Key | da | sv | no | en |
|---|---|---|---|---|
| `gui.claude.card_title` (new) | AI-assistent | AI-assistent | AI-assistent | AI assistant |
| `gui.claude.idle` | AI-assistenten hjælper, når det bliver din tur. | AI-assistenten hjälper till när det blir din tur. | AI-assistenten hjelper når det blir din tur. | The AI assistant helps when it is your turn. |
| `gui.claude.working` | AI'en arbejder | AI:n arbetar | AI-en jobber | AI at work |
| `gui.claude.intro` | AI-assistenten skal {tasks}. | AI-assistenten ska {tasks}. | AI-assistenten skal {tasks}. | The AI assistant will {tasks}. |
| `gui.claude.mcp_intro` | AI-assistenten henter teksten, retter og oversætter den og sender den tilbage hertil. Du skal ikke gøre noget her.\n\nFølg med i chatten. Er den stoppet undervejs, så skriv "fortsæt" til den. | AI-assistenten hämtar texten, rättar och översätter den och skickar tillbaka den hit. Du behöver inte göra något här.\n\nFölj med i chatten. Har den stannat på vägen, skriv "fortsätt" till den. | AI-assistenten henter teksten, retter og oversetter den og sender den tilbake hit. Du trenger ikke gjøre noe her.\n\nFølg med i chatten. Har den stoppet underveis, skriv "fortsett" til den. | The AI assistant fetches the text, corrects and translates it and sends it back here. You do not need to do anything here.\n\nFollow along in the chat. If it stops on the way, write "continue" to it. |
| `gui.claude.to_chat` (was `to_file`) | Gør det selv i en AI-chat i stedet | Gör det själv i en AI-chatt i stället | Gjør det selv i en AI-chat i stedet | Do it yourself in an AI chat instead |
| `gui.claude.stop_ask` | AI-trinnet er ikke færdigt, så videoen bliver ikke lavet.\n\nAfbryd alligevel? | AI-steget är inte klart, så videon blir inte gjord.\n\nAvbryta ändå? | AI-trinnet er ikke ferdig, så videoen blir ikke laget.\n\nAvbryte likevel? | The AI step is not finished, so the video will not be made.\n\nStop anyway? |
| `gui.parts.copy` | Kopiér instruktioner til AI-chat | Kopiera instruktioner till AI-chatt | Kopier instruksjoner til AI-chat | Copy instructions for AI chat |
| `gui.parts.copy_again` (new) | Kopiér igen | Kopiera igen | Kopier igjen | Copy again |
| `gui.parts.next_copy` (drops `{part}`) | Næste: klik "{copy}", sæt det ind i en AI-chat (Ctrl+V), og send. | Nästa: klicka på "{copy}", klistra in det i en AI-chatt (Ctrl+V) och skicka. | Neste: klikk "{copy}", lim det inn i en AI-chat (Ctrl+V), og send. | Next: click "{copy}", paste it into an AI chat (Ctrl+V) and send. |
| `gui.parts.copied` | Kopieret. Sæt det ind i AI-chatten med Ctrl+V, og send. | Kopierat. Klistra in i AI-chatten med Ctrl+V och skicka. | Kopiert. Lim inn i AI-chatten med Ctrl+V og send. | Copied. Paste it into the AI chat with Ctrl+V and send. |
| `gui.parts.waiting` | Venter på svaret på {part}. Når AI-chatten er færdig, så kopiér hele svaret (kopiknappen i chatten). Programmet opdager det selv. | Väntar på svaret på {part}. När AI-chatten är klar, kopiera hela svaret (kopieringsknappen i chatten). Programmet märker det själv. | Venter på svaret på {part}. Når AI-chatten er ferdig, kopier hele svaret (kopiknappen i chatten). Programmet oppdager det selv. | Waiting for the answer to {part}. When the AI chat is done, copy the whole answer (the copy button in the chat). The program notices by itself. |
| `gui.parts.problem` (new) | Svaret på {part} var ikke helt færdigt. Bed AI-chatten skrive resten, og kopiér det igen. | Svaret på {part} var inte helt klart. Be AI-chatten skriva resten och kopiera det igen. | Svaret på {part} var ikke helt ferdig. Be AI-chatten skrive resten, og kopier det igjen. | The answer to {part} was not quite finished. Ask the AI chat to write the rest, and copy it again. |
| `gui.parts.split` | Teksten er delt i {n} dele, så svaret ikke bliver for langt. Send dem i samme chat, én ad gangen. | Texten är delad i {n} delar så att svaret inte blir för långt. Skicka dem i samma chatt, en i taget. | Teksten er delt i {n} deler, så svaret ikke blir for langt. Send dem i samme chat, én om gangen. | The text is split into {n} parts so the answer is not too long. Send them in the same chat, one at a time. |
| `gui.parts.data` (new) | Brug den AI-chat, din arbejdsplads tillader. | Använd den AI-chatt som din arbetsplats tillåter. | Bruk den AI-chatten arbeidsplassen din tillater. | Use the AI chat your workplace allows. |
| `gui.parts.mcp_nudge` (new) | Har du forbundet en AI-assistent? Skriv til den: | Har du kopplat en AI-assistent? Skriv till den: | Har du koblet til en AI-assistent? Skriv til den: | Connected an AI assistant? Write to it: |
| `gui.parts.mcp_phrase` (new) | Tag over i {app} | Ta över i {app} | Ta over i {app} | Take over in {app} |
| `gui.parts.copy_phrase` (new) | Kopiér | Kopiera | Kopier | Copy |
| `gui.parts.copied_short` (new) | Kopieret ✓ | Kopierat ✓ | Kopiert ✓ | Copied ✓ |
| `claude.no_answer` | Der er ikke noget svar i udklipsholderen. Kopiér hele svaret fra AI-chatten, og prøv igen. | Det finns inget svar i urklipp. Kopiera hela svaret från AI-chatten och försök igen. | Det er ikke noe svar på utklippstavlen. Kopier hele svaret fra AI-chatten, og prøv igjen. | There is no answer on the clipboard. Copy the whole answer from the AI chat and try again. |

Same sweep outside the panel (user-visible "Claude" meaning any assistant):

| Key | da | sv | no | en |
|---|---|---|---|---|
| `gui.plan.claude` | AI-assistenten {tasks} (i kortet til højre, når det er din tur). | AI-assistenten {tasks} (i kortet till höger när det är din tur). | AI-assistenten {tasks} (i kortet til høyre når det er din tur). | The AI assistant {tasks} (in the card on the right, when it is your turn). |
| `gui.step.claude_turn` | AI-assistenten {tasks} - din tur | AI-assistenten {tasks} - din tur | AI-assistenten {tasks} - din tur | The AI assistant {tasks} - your turn |
| `gui.step.claude_chat` | AI-assistenten {tasks} - i chatten | AI-assistenten {tasks} - i chatten | AI-assistenten {tasks} - i chatten | The AI assistant {tasks} - in the chat |
| `gui.work.your_turn_callout` | Følg trinene i kortet til højre | Följ stegen i kortet till höger | Følg trinnene i kortet til høyre | Follow the steps in the card on the right |
| `gui.work.claude_stops` | AI-assistenten stopper kørslen... | AI-assistenten stoppar körningen... | AI-assistenten stopper kjøringen... | The AI assistant is stopping the run... |
| `gui.work.claude_in_chat` | AI-assistenten retter og oversætter i chatten | AI-assistenten rättar och översätter i chatten | AI-assistenten retter og oversetter i chatten | The AI assistant is correcting and translating in the chat |
| `gui.done.learned` | AI-assistenten rettede disse ord, og programmet husker dem fremover: {words} | (same pattern) | (same pattern) | (same pattern) |
| `gui.done.claude_skipped` | AI-trinnet blev afbrudt, ... (rest unchanged) | AI-steget ... | AI-trinnet ... | The AI step ... |
| `chapters.from_claude` | Fra AI | Från AI | Fra AI | From AI |
| `mcp.cancelled_app_not_running` | Stoppet fra AI-assistenten (appen kørte ikke). | (same pattern) | (same pattern) | (same pattern) |

"(same pattern)": replace "Claude" with the language's "AI-assistenten" / "AI-steget" form from the rows above;
the existing sentence otherwise stays. Message box titles `"Claude"` in gui.py use `gui.claude.card_title`.

### Reused unchanged

`gui.claude.player2`, `your_turn`, `task_check`, `task_translate`, `task_cut`, `stop`, `corrected`, `n_of`,
`cuts_none`, `cuts_got`, `cuts_done`, `answer_ok_blocks`; `gui.parts.title`, `paste`, `part`, `the_text`, `have`,
`ok`, `ok_words`; `chapters.task`, `chapters.mcp_*`; `claude.language_problem`, `claude.still_missing`.

### Deleted (all four languages)

- `gui.claude.`: `to_parts`, `to_file` (renamed), `save_continue`, `work_file_failed`, `step_copy`, `step_paste`,
  `step_paste_hint`, `step_download`, `step_download_hint`, `copy_file`, `pick_answer`, `pick_answer_title`,
  `copy_followup`, `followup_copied`, `copied_file`, `copy_failed`, `found_checking`, `found`, `found_clipboard`,
  `read_failed_detail`, `read_failed`, `other_round_detail`, `other_round`, `answer_ok`, `answer_incomplete`,
  `details`, `waiting`, `waiting_minutes`.
- `gui.parts.all_ready`.
- `file.task`, `file.answer`.
- `claude.answer_no_cuts`, `claude.answer_no_end` (file answers only; delete if nothing else uses them after
  `check_file_answer` goes).
- `gui.skill.*` (whole SkillGuide), `gui.settings.save_skill`, `save_skill_title`, `skill_title`, `skill_saved`,
  `mcp.button.save_skill`.

## Settings dialog

Only the "Tekst og stemmer" card changes: the links row loses "Gem Claude-skill..." and becomes
`Åbn modelmappe` · `Rediger rettelser` · `Åbn log` (same `ttk.Button`s, same 6 px gaps). `_save_skill` goes.
"Brug med andre AI-værktøjer" (with "Forbind med Claude") is unchanged. `Settings.skill_installed` is dropped as a
setting, but an old settings.json's value is first read as an upgrade signal (next section), then popped like the
v1.0 `model` key so it is not written back.

## Upgrade: "delete the old skill" notice (shown once)

Every version up to 2.1.x shipped a Claude skill (`subadubalingo`, earlier `videosprog`) that users uploaded to
Claude. Left in Claude, it tells Claude about task files and a protocol the app no longer has. Users who come from
such a version are asked once, right after the update, to delete it. Fresh installs never see it.

### Who sees it

A pure function, testable on Linux, in a new small module `app/subadubalingo/upgrade_notice.py` (which also holds
the dialog, so gui.py does not grow):

```python
SKILL_REMOVED_IN = "2.2.0"   # the first version without the skill (the APP_VERSION this ships in)

def needs_old_skill_notice(saved: dict | None) -> bool:
    """saved: settings.json as read at start, None when there was no file (a fresh install)."""
    if not saved or saved.get("old_skill_notice_done"):
        return False
    if saved.get("old_skill_notice_pending"):
        return True
    if (saved.get("skill_installed") or 0) > 0:
        return True
    last = saved.get("last_version")
    if last:
        return _vtuple(last) < _vtuple(SKILL_REMOVED_IN)
    return "skill_installed" in saved
```

- No settings file: fresh install, never. Every app of 2.1.x and older wrote all its settings, `skill_installed`
  among them (0 meant "unknown", not "absent": the skill could have been saved from Indstillinger), and no
  `last_version`, so such a file counts as an upgrade even when `skill_installed` is 0.
- **Implemented differently from the first draft:** a file without `last_version` is *not* enough on its own. The
  installer's first-start language page writes `settings.json` with only `ui_language` before the app's first start,
  so a fresh install has a file too. Without `skill_installed` and `last_version` it is a fresh install.
- `Settings.__init__` keeps the parsed file as `self._saved` (None when missing or unreadable); `Settings.save()`
  skips keys starting with `_`. New settings: `last_version`, `old_skill_notice_done`, `old_skill_notice_pending`.
- At every start, `App.__init__` calls `upgrade_notice.decide_at_start` first: it records `last_version =
  APP_VERSION`, marks a fresh install (or anything not needing the notice) as done at once, and sets
  `old_skill_notice_pending` when the notice is due, so a session an assistant started (which writes `last_version`
  too) cannot lose it: the next normal start shows it.

### When and where

- In the slot the removed SkillGuide had: 900 ms after the main window is built, on the main window, not modal
  (`transient`, `place_over`), one dialog at a time (`offer_crash_report` and `_call_attention` look at
  `app._notice` instead of `app._skill_guide`).
- Not in a session the app was started in by an MCP assistant (the launch flag, see "Removed" below): the user is
  mid-chat there, and the server's instructions already tell the assistant to mention it. The notice then waits for
  the next normal start (`old_skill_notice_pending`); the done flag stays unset until it has been shown.
- It may show together with the "LEVEL UP Version 2.2.0" update band (autoupdate); they do not overlap (band in
  the window, notice as its own dialog).
- `old_skill_notice_done = True` is saved the moment the dialog is shown (not on close), so a crash or a kill cannot
  make it show twice. Any way of closing it (button, ×, Escape, Enter) just closes it.

### Look (existing dialog pattern, as the removed SkillGuide and the report dialog)

`tk.Toplevel` themed by `place_over` -> `skin.dialog`, `ttk.Frame(padding=18)`, wrap width `520 * app.scale`:

1. Heading `oldskill.heading`, `Section.TLabel`, `P.heading`; window title the same text.
2. Intro `oldskill.intro` (`app=APP_NAME`), plain `ttk.Label`, `pady=(6, 12)`.
3. Two numbered steps, plain `ttk.Label`s, `pady=2`, `"1.  "` / `"2.  "` prefix as before:
   `oldskill.step_open`, then `oldskill.step_delete` with
   `names = f" {t('gui.and')} ".join(f'"{n}"' for n in OLD_SKILL_NAMES)`, where `OLD_SKILL_NAMES =
   ("subadubalingo", "videosprog")` lives in `upgrade_notice.py` (`claude.SKILL_NAME` / `OLD_SKILL_NAMES` go).
4. `oldskill.no_claude` in `Hint.TLabel`, `pady=(10, 0)`.
5. One button, `Accent.TButton`, `default="active"`, focus: `oldskill.ok`. Escape and Enter close.

No icon, no art: it is a short housekeeping note, not an event.

### Texts (new area file `assets/locales/<code>/oldskill.json`)

These are Claude-specific on purpose (it is Claude's skill list), so `oldskill.*` is on the "may say Claude"
allowlist. "Customize -> Skills" stays in English: it is the label in Claude's own UI.

| Key | da | sv | no | en |
|---|---|---|---|---|
| `oldskill.heading` | Ryd op i Claude | Städa upp i Claude | Rydd opp i Claude | Tidy up in Claude |
| `oldskill.intro` | {app} bruger ikke længere en Claude-skill. Har du lagt skill'en ind i Claude, så slet den, så Claude ikke bliver forvirret. Det tager et minut, og du skal kun gøre det én gang. | {app} använder inte längre någon Claude-skill. Har du lagt in skillen i Claude, ta bort den så att Claude inte blir förvirrad. Det tar en minut, och du behöver bara göra det en gång. | {app} bruker ikke lenger en Claude-skill. Har du lagt skillen inn i Claude, så slett den, så Claude ikke blir forvirret. Det tar et minutt, og du trenger bare gjøre det én gang. | {app} no longer uses a Claude skill. If you added the skill to Claude, delete it so Claude does not get confused. It takes a minute, and you only do it once. |
| `oldskill.step_open` | Åbn Claude, og gå til Customize -> Skills. | Öppna Claude och gå till Customize -> Skills. | Åpne Claude, og gå til Customize -> Skills. | Open Claude and go to Customize -> Skills. |
| `oldskill.step_delete` | Slet {names}, hvis de er der. | Ta bort {names}, om de finns där. | Slett {names}, hvis de er der. | Delete {names}, if they are there. |
| `oldskill.no_claude` | Bruger du ikke Claude, eller har du aldrig lagt skill'en ind, så skal du ikke gøre noget. | Använder du inte Claude, eller har du aldrig lagt in skillen, behöver du inte göra något. | Bruker du ikke Claude, eller har du aldri lagt inn skillen, trenger du ikke gjøre noe. | If you do not use Claude, or never added the skill, there is nothing to do. |
| `oldskill.ok` | Forstået | Uppfattat | Forstått | Got it |

### MCP side of the cleanup (round 2, server)

- `instructions()` and `guide.py` (the overview section the `guide` tool, prompt and resources serve) say, in
  English for the assistant: a skill called "subadubalingo" or "videosprog" is obsolete; ignore it, never follow its
  task-file or answer-file steps, and tell the user once, briefly, that they can delete it in Claude under
  Customize -> Skills.
- `get_text` keeps accepting the old `skill` argument (a stale skill still sends it) and ignores it silently:
  no `skill.json`, no note in the reply. It is no longer in the tool's input schema.
- guide.py's line "Never ask the user to install a Claude skill" stays.

### Tests (`tests/test_upgrade_notice.py`, runs on Linux)

- Fresh install (`saved=None`): never; and after the first start's save, the next start's settings also give False.
- Upgrade: a 2.1.1 settings file (`skill_installed` 6, no `last_version`) -> True; `skill_installed` 0 and no
  `last_version` -> True; `last_version` "2.1.1" -> True.
- Once: after the notice is shown (`old_skill_notice_done` saved), the next start gives False; a settings file with
  `last_version` >= `SKILL_REMOVED_IN` and no `skill_installed` -> False.
- `Settings.save()` does not write `_saved` or `skill_installed`.
- Started by an MCP assistant: not shown, flag still unset (decision helper takes the launch flag, or the App-side
  check is a separate small function tested the same way).
- The four `oldskill.json` files have the same keys; `step_delete` has `{names}`, `intro` has `{app}`.

## Tests (`tests/test_assistant_panel.py` unless noted)

- Chat view is the default for an app job; no switch button; no file is written next to the video.
- Part states: copy -> copied/waiting (copy button text and styles), valid paste -> done, partial paste -> problem
  keeps the partial; all parts -> finishes by itself without a click.
- Copying the MCP phrase is not taken as an answer.
- Take-over: a `taken.json` newer than `chat_since` switches to the MCP view and sets `app.mcp_job`; an older one
  does not. `complete.json` finishes the round in either view.
- MCP view's switch button opens the chat view.
- `claude.prompt()` names no skill and no product (`tests/test_claude_answers.py`).
- Locales (new `tests/test_locales.py`; today only some areas have a per-file key check): every area file has the
  same keys in da/sv/no/en; no `gui.skill.*` / file-mode keys left; no da value contains "Claude" outside the
  allowlist (`gui.settings.connect*`, `mcpclients.*`, `installer.*` texts about Claude desktop, `oldskill.*`).
- The upgrade notice: see its own section.

## Removed with the skill and the file round (for the implementer)

- `SkillGuide`, `App.show_skill_guide`, `App.note_skill`, `_skill_warning` (and its use in `_show_result`),
  the `skill_installed < SKILL_VERSION` start check; ClaudePanel's file mode (`_build_file_mode`, `_set_fstep`,
  `_draw_fstep`, `_copy_file`, `_pick_answer`, `_scan_downloads`, `_try_answer_file`, `_show_problem`,
  `_copy_followup`, the file part of `_tick`, `_to_parts` as a switch).
- `--from-claude`: the decision lists it with the file round, but in code it is the flag `mcp_server._start_app`
  passes when an assistant starts the app (it skips the install offer, the resume offer, the crash-report offer and
  the start-up notices). Keep that behaviour under a neutral name, `--from-assistant`, and still accept
  `--from-claude` (a running 2.1.x MCP server can start the new app during an update).
