# Assistant panel (spec, 2026-10-19)

The right-hand card on the work view, where the assistant's turn happens. Replaces `ClaudePanel`'s file mode
(removed with the skill, see the owner's decision of 2026-10-19). Two views remain:

- **Chat view** (`mode = "chat"`): a job started in the app. The user copies instructions per part into any AI chat
  and copies the answer back. This is today's parts mode, promoted to the default and reworded.
- **MCP view** (`mode = "mcp"`): the job came from an MCP assistant, or an MCP assistant took over an app job.
  Today's progress view, unchanged in look.

No new visual language: everything below is an existing widget, style, icon or colour token.

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
"Brug med andre AI-værktøjer" (with "Forbind med Claude") is unchanged. `Settings.skill_installed` is dropped
(ignored when an old settings.json still has it).

## Tests (`tests/test_assistant_panel.py` unless noted)

- Chat view is the default for an app job; no switch button; no file is written next to the video.
- Part states: copy -> copied/waiting (copy button text and styles), valid paste -> done, partial paste -> problem
  keeps the partial; all parts -> finishes by itself without a click.
- Copying the MCP phrase is not taken as an answer.
- Take-over: a `taken.json` newer than `chat_since` switches to the MCP view and sets `app.mcp_job`; an older one
  does not. `complete.json` finishes the round in either view.
- MCP view's switch button opens the chat view.
- `claude.prompt()` names no skill and no product (`tests/test_claude_answers.py`).
- Locales: no `gui.skill.*` / file-mode keys left; no da value outside the Claude-specific allowlist contains
  "Claude"; the four languages have the same keys (existing locale test).
