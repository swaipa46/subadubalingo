# SubaDubaLingo UI assets (T2)

These are the assets for the two themes the user can pick between:

- **`a-light/`** is "Lys" (Light arcade).
- **`b-night/`** is "Nat" (Night arcade).

Both folders contain the same file names, so the app picks one folder and loads every file by name. Shared files live in
`common/`, and the fonts are in `../fonts/`. For each screen, see the mockups in `design/mockups/<direction>/`.

## How the files are made

- **Pixel grid and 2x files:** every PNG is drawn on an exact pixel grid. Each `x.png` (1x) has an `x@2x.png` that is a
  nearest-neighbour doubling.
  - Use 1x at 100–124 % display scaling and 2x at 125 % and above.
  - Read the scaling with `root.winfo_fpixels('1i') / 96`.
  - Never scale by non-integer factors.
- **Regenerating:** the sources are `design/mockups/source/` (`mockups.py` for the colours and shapes, `assets.py` for
  the frames, icons and `tokens.json`, `art.py` for the header logo and banner). Change a colour there and rerun them,
  and both the mockups and the assets update.

## tokens.json

The file has these parts:

- **`themes["a-light"|"b-night"]`:** every colour by role, with the same keys in both themes.
  - Roles: window, card, card border and shadow, header band and text, header stripe (4 colours), text, hint, heading,
    accent, success, warning, error, focus, disabled text, input, selection, tooltip.
  - Buttons: primary and secondary, with normal, hover and pressed colours.
  - Pickers: selected and hover colours.
  - Progress: trough, border, fill and current segment.
  - The your-turn card and the callout band.
  - Steps.
  - Timeline: background, waveform, speech blocks and ruler.
  - The seven cut kinds: `cut_<kind>_border` and `cut_<kind>_fill`, for the kinds start, pause, end, inappropriate,
    offtopic, unclear and manual.
- **`contrast[theme]`:** the WCAG contrast ratio of every text colour on the background it is used on, including each
  cut colour as label text on its own fill and on a card. All of them are at least 4.5:1.
- **`nine_slice[theme][name]`:** the slice borders (left, top, right, bottom) in px at 1x and 2x for every file in
  `frames/`. They are identical in both themes.
- **`frame_profiles`:** the pixel-rounded corner steps (an inset per 2 px row) for small frames, buttons and cards.

## Nine-slice frames (`<theme>/frames/`)

Each frame is a single image containing the fill, the border and the hard offset shadow. Slice it with the borders
below, and stretch or tile the middle. In tkinter, the simplest way is
`ttk.Style().element_create(name, "image", img, border=(l, t, r, b), sticky="nsew")` for ttk widgets. For a Canvas,
cut the 9 parts with `PhotoImage.copy`/`subsample` once at start and draw them.

- **Shadow and pressed state:** the shadow sits inside the image on the right and bottom, so give widgets that much
  extra padding there. A pressed button has no shadow and is drawn shifted by the shadow offset, which gives a "press
  down" feel without moving the layout.
- **Focus:** the focus states draw a dashed ring inside the border in the theme's focus colour. Keep a focus state on
  every control.

| File | Slice l / t / r / b (1x px) | Use |
|---|---|---|
| `frames/card.png` | 16 / 16 / 20 / 20 | Every card: video, options, steps, the idle Claude card. |
| `frames/card-your-turn-0.png` | 18 / 18 / 24 / 24 | Claude card on the user's turn; alternate with -1 every ~600 ms so it pulses. |
| `frames/card-your-turn-1.png` | 18 / 18 / 24 / 24 | Second pulse state (border and shadow colours swapped). |
| `frames/dialog.png` | 20 / 20 / 28 / 28 | Outer frame of Indstillinger, the skill guide and the install/update window, inside the OS title bar. |
| `frames/banner.png` | 18 / 18 / 24 / 24 | Frame for any custom banner (art/done-banner.png already includes it). |
| `frames/callout.png` | 14 / 14 / 14 / 14 | The "din tur" callout band under the progress bar (fill and border in callout_bg, no shadow). |
| `frames/timeline.png` | 16 / 16 / 20 / 20 | Frame around the cut-review Canvas. |
| `frames/tile.png` | 16 / 16 / 20 / 20 | Choice tile (language, Udseende), normal. |
| `frames/tile-hover.png` | 16 / 16 / 20 / 20 | Choice tile under the mouse. |
| `frames/tile-selected.png` | 18 / 18 / 24 / 24 | The chosen tile. |
| `frames/tile-focus.png` | 16 / 16 / 20 / 20 | Choice tile with keyboard focus (dashed ring). |
| `frames/button-primary.png` | 11 / 11 / 14 / 14 | Main action: Start, Klip videoen, Afspil, Gem, Fortsæt. |
| `frames/button-primary-hover.png` | 11 / 11 / 14 / 14 | button-primary, state hover. |
| `frames/button-primary-pressed.png` | 11 / 11 / 14 / 14 | button-primary, state pressed. |
| `frames/button-primary-disabled.png` | 11 / 11 / 11 / 11 | button-primary, state disabled. |
| `frames/button-primary-focus.png` | 11 / 11 / 14 / 14 | button-primary, state focus. |
| `frames/button-secondary.png` | 11 / 11 / 14 / 14 | Every other button, and the outer frame of the theme and language pickers in the header. |
| `frames/button-secondary-hover.png` | 11 / 11 / 14 / 14 | button-secondary, state hover. |
| `frames/button-secondary-pressed.png` | 11 / 11 / 14 / 14 | button-secondary, state pressed. |
| `frames/button-secondary-disabled.png` | 11 / 11 / 11 / 11 | button-secondary, state disabled. |
| `frames/button-secondary-focus.png` | 11 / 11 / 14 / 14 | button-secondary, state focus. |
| `frames/picker-item-hover.png` | 4 / 4 / 4 / 4 | One icon or code inside a picker, under the mouse. |
| `frames/picker-item-selected.png` | 4 / 4 / 4 / 4 | The current language or theme inside a picker. |
| `frames/picker-item-focus.png` | 4 / 4 / 4 / 4 | Picker item with keyboard focus (transparent with a dashed ring; lay it over the item). |
| `frames/input.png` | 6 / 6 / 6 / 6 | Combobox / entry field. |
| `frames/input-focus.png` | 6 / 6 / 6 / 6 | Combobox / entry with focus. |
| `frames/progress-trough.png` | 6 / 6 / 6 / 6 | Segmented progress bar background (20 segments, 3 px gaps). |
| `frames/progress-segment.png` | 4 / 4 / 4 / 4 | A filled segment. |
| `frames/progress-segment-current.png` | 4 / 4 / 4 / 4 | The segment being worked on (the last filled one). |

## Icons (`<theme>/icons/`)

**Steps** are 24 px at 1x (a 12-pixel grid) and go in the step list in place of the radio circles:

- `step-pending`
- `step-done`
- `step-error`
- `step-your-turn-0` / `-1`: blink between them about every 500 ms.
- `step-active-0` … `step-active-5`: an animation of the play triangle stepping right and back. Show each frame for
  about 120 ms with `after()`.

**Checkboxes and radio buttons** are 16 px (an 8-pixel grid): `check-on`, `check-off`, `check-disabled`, `radio-on` and
`radio-off`. Use them as the ttk Checkbutton/Radiobutton indicator images.

**Small icons** are 16 px. Each is drawn in the secondary-button text colour, and each has a `-selected` variant drawn
in the picker's selected-text colour. `play`, `cut`, `choose-video`, `retry` and `copy` also have an `-on-primary` variant for yellow
buttons.

| Icon | Used for |
|---|---|
| `choose-video` | Vælg video / Skift video … |
| `play` | Afspil, Se overgangen |
| `show-in-folder` | Vis i mappe |
| `settings` | Indstillinger |
| `pause` | Pause |
| `cancel` | Annullér, Afbryd |
| `copy` | Kopiér fil |
| `cut` | Klip videoen |
| `plus`, `minus` | Zoom on the timeline, Lav en ny |
| `arrow-right` | The "din tur" callout band |
| `language` | Leading icon of the language picker |
| `file-video`, `file-subtitles` | Rows in "Alle filer fra denne kørsel" on the done view (.mp4 / .srt) |
| `retry` | Prøv igen (has `-on-primary`) |
| `edit`, `split`, `merge`, `trash`, `grip`, `mail`, `queue`, `chapters`, `stop`, `voice-play`, `up`, `wave` | New in T8, see "New features (T8)" |
| `speaker-0`, `speaker-1` | Scrub preview caption: idle / sound snippet playing (T7), drawn in `hint` |
| `theme-light`, `theme-night`, `theme-system` | The three choices in the theme picker (Lys, Nat, Følg Windows) |

## Header (final, T4)

From the left, the header contains:

1. **Logo:** the icon (48 px), with the wordmark next to it in Press Start 2P 24 px.
2. **Tagline:** "CUT · DUB · SUB" under the wordmark, in VT323 20 px, 3 px letter spacing, colour `text`. The dots are
   5 px squares in #ff2e88 and in cyan (#22e4ff on Nat, #22a6c9 on Lys).
3. **No other tagline:** the old "Undertekster og dubbing af videoer" line is gone.

On the right, with 12 px between them, all three controls are **40 px tall** and use `button-secondary` as their outer
frame:

- **Theme picker:** the `theme-light`, `theme-night` and `theme-system` icons.
- **Language picker:** the `language` icon, then `DA SV NO EN` in Press Start 2P 12 px.
- **Indstillinger:** an icon-only, square 40×40 button. Use `button-secondary` and its hover, pressed and focus
  states; a 9-slice stretches to a square, so no new frame is needed. The 16 px `settings` icon is centred (`@2x` at
  125 % and above). The tooltip says "Indstillinger", translated.

**Each picker item** is 28 px tall with 7–9 px side padding:

- **normal:** no background;
- **hover:** `picker-item-hover`;
- **current:** `picker-item-selected`, with the text or icon in `picker_selected_text` and the icon's `-selected`
  variant;
- **keyboard focus:** `picker-item-focus` on top.

**Tooltips** use `tooltip_bg` / `tooltip_text` and give the option's name in its own language plus the action. For
example: "Svenska · byt programmets språk", or "Følg Windows (nu: Lys) · skift udseende".

**Defaults and Indstillinger:** `theme-system` (Følg Windows) is selected by default. Indstillinger has the same
choices as tiles under "Programmets sprog" and "Udseende", using the `tile*` frames.

See the mockups `1-setup.png`, `theme-header.png`, `language-header.png`, `install-2.png` and `update.png` in
`design/mockups/*/`.

## Cut review: live preview while dragging (T7)

Mockups: `4-review-scrub.png` (dragging the end edge of cut 2) and `4-review-scrub-states.png` (end edge, new cut,
image not loaded yet) in `design/mockups/*/`. Source: `design/mockups/source/scrub.py`.

**Where:** a floating preview inside the timeline, next to the edge being dragged. It is not shown in the details
column.

- **Why:**
  - Your eyes are already on the edge and the waveform, so the picture is where you look.
  - It can be bigger than the 168×94 details box without costing timeline or list height.
  - Video editors use this for scrub previews, so it reads as familiar.
- **Details column while dragging:** it keeps showing the last "Lige før / Lige efter" frames, unchanged. On release
  they update as today. Show the video in only one place at a time.

**Size and parts (1x):**

| Part | Value |
|---|---|
| Image | 256×144, scaled from the 4-per-second thumbnails (nearest one to the edge time) |
| Frame | `frames/scrub` (slice 12/12/16/16 at 1x, 24/24/32/32 at 2x); image inset 6 px |
| Caption row | 30 px tall, under the image, inside the frame |
| Total | about 288×196 including the shadow |
| Pointer | `art/scrub-pointer-right` when the preview is left of the edge, `-left` when it is right of it; 8×16, vertically centred on the image |
| Gap | 16 px from the edge line to the pointer tip |
| Vertical position | top 8 px under the ruler. It stays above the speech bar at the bottom, so you can always see where speech stops. If the track is shorter than 220 px, scale the image down to fit, keeping 16:9 and at least 192×108. |
| Dragged handle | `art/timeline-handle-dragging` (14×60 accent with a 2 px border), centred on the edge, replacing the normal 10×44 handle while dragging |

**Which side:**

- **Existing cut edge:** the preview goes on the cut's inside, away from the edge. That is left of an end edge and
  right of a start edge. This side is being cut away anyway, and the kept side of the waveform stays visible.
- **New cut being dragged out:** the preview goes on the side the drag started from.
- **Flip:** if the preview would leave the visible timeline, put it on the other side.
- **Follows the edge:** it moves with the edge, and the edge follows the mouse (with your snapping).

**Caption, left to right:**

1. **Tag:** a chip in Press Start 2P 10 px, with a background in the cut's border colour and text in `#ffffff` (Lys)
   or `#1a0840` (Nat). The chip text:
   - `START` for the start edge, `SLUT` for the end edge;
   - `NYT KLIP` while dragging out a new cut, using the `manual` cut colour.
2. **Time:** the exact edge time in VT323 26 px, `text` colour, as `m:ss,cc` with the UI language's decimal sign
   (`0:07,75` in da/sv/no, `0:07.75` in en).
3. **Right-aligned:** the speaker icon, then the cut's new length in Segoe UI 10 pt, `hint`. The length text is
   "klip 1,75 s" in da, "klipp 1,75 s" in sv and no, and "cut 1.75 s" in en.
   - The speaker icon is `icons/speaker-1` while a sound snippet is playing and `speaker-0` between snippets.

**States:**

| State | Shown |
|---|---|
| Not dragging (incl. hover over a handle) | No preview. Only the normal handle, with the cursor `sb_h_double_arrow` on the handle |
| Dragging | Preview appears at once, with no fade. It updates the image whenever the nearest thumbnail changes, and the time on every mouse move |
| Thumbnail not ready | Same frame, image area filled `#0b0320` with "..." in Press Start 2P 10 px `#9c8cc9`. The caption still shows the time |
| Released / Esc | Preview disappears at once. The details column frames update as today |

Translation keys for the tag and length text: `review.scrub.start`, `review.scrub.end`, `review.scrub.new`,
`review.scrub.length`.

## New features (T8)

The mockups are in `design/mockups/{a-light,b-night}-arcade/`, and the source is `design/mockups/source/features.py`.
Everything reuses the existing frames, buttons, cards, pickers and colours unless a section names something new. New
16 px icons (1x + @2x, plus a `-selected` variant, in `<theme>/icons/`):

- `edit`
- `split`, `merge`, `trash`
- `grip`
- `mail` (+ `-on-primary`)
- `arrow-right-on-primary` (the light theme's `arrow-right`, for "Send" in the error report)
- `queue` (+ `-on-primary`)
- `chapters`
- `stop`
- `voice-play`
- `up` (+ `up-on-callout`)
- `wave`

### 1. Subtitle editor (`editor-main`, `editor-entry`, `editor-save`)

**How you get to it:**

- A secondary button "Ret undertekster" (`edit`) in the done view's result card, after Vis i mappe.
- In "Alle filer fra denne kørsel", the hovered or focused `.srt` row shows a small "Ret" button (30 px, `edit`).
  Double-click or Enter on an `.srt` row opens the editor instead of an external program, and the hint text under
  the list says so.

**Layout (replaces the main area, header unchanged):**

- **Title row:**
  - On the left: "RET UNDERTEKSTER", Press Start 2P 16 px, `heading`.
  - On the right:
    - a language picker for the languages that were made: `DA SV NO`, the same picker as the header, Press Start
      2P 11 px;
    - Annullér;
    - Gem (primary).
- **Under the title:** the file name in Semibold, then a hint line.
- **Left, flexible width:** the line list in a `card`.
  - **Header row, `hint` 13 px:** `#`, `Tid`, `Tekst`.
  - **Rows:** at least 38 px, growing with wrapped text. Each row has:
    - the number in Press Start 2P 10 px;
    - the time "0:04,90 - 0:06,20" in VT323 20 px, 150 px wide;
    - the text.
  - **Selected row:** `picker_selected_bg` / `picker_selected_text`. Its text becomes an `input-focus` field, edited
    in place.
  - **Lines too fast to read** (over 20 characters per second): a 10 px `warning` square and "23 tegn/s" in
    `warning` 12 px at the row's end.
  - **Footer, `hint` 13 px:** "8 af 212 linjer · 1 linje er svær at nå at læse (over 20 tegn/s)".
- **Right column, 628 px wide:**
  - **Video:** 620×349 with a 2 px `border` and the small pixel corners. The selected line is shown as a subtitle in
    the video.
  - **Under the video:** a play button, the time in VT323 22 px, and to the right "Linje 3 · 0:04,90 - 0:06,20
    (1,3 s)" in `hint`.
  - **Mini timeline:** the `timeline` frame, a 24 px ruler and a 150 px track.
    - The waveform is drawn as in the cut review.
    - Each line is a 38 px box at the bottom of the track: `card` fill, 2 px `heading` border on Lys and `border`
      on Nat, number plus text clipped.
    - The selected line has a `your_turn_card` fill, a 3 px `accent` border, and 8×26 drag handles on both edges.
      Drag them like cut edges; T7's scrub preview is not used here, because the big video already follows.
    - The playhead is 2 px in `progress_current` with an 12×8 cap.
  - **Tools:** "Del linjen her" (`split`, splits at the text cursor), "Slå sammen med næste" (`merge`), and an
    icon-only delete (`trash`). All are 40 px.
  - **Keys, `hint` 13 px:**
    - Enter: next line;
    - Ctrl+Enter: split at the cursor;
    - Space outside the text: play;
    - Ctrl+S: save.

**After Gem: a dialog, 700 px wide.**

- "Gemt" and the file name.
- A card "Skal programmet huske ordene næste gang?". Each changed word pair is a check box row: the old word
  struck through in `hint`, an arrow, then the new word in Semibold. All are checked by default except splits and
  merges of whole words.
- A check box "Lav videoen igen med de nye undertekster", with a hint. It is shown only if a video with burned-in
  subtitles or a dub was made from this file.
- Buttons "Spring over" and "Føj N ord til ordlisten" (primary).

### 2. Queue (`queue-setup`, `queue-work`, `queue-done`)

**Setup.** Card 1 becomes "1. Videoer i køen (N)", a list with one row per video. Each row is 54 px tall, with a
2 px dashed `step_pending_icon` divider and `picker_hover_bg` on hover. From left to right:

- `grip` (drag to reorder) and the number in Press Start 2P 10 px;
- the name in Semibold and the duration in `hint` 12 px;
- three 34 px combos: the spoken language (110 px), then "→", then "Tale: …" (140 px) and "Tekst: …" (150 px);
- an icon-only remove button (`cancel`).

Under the list: "Tilføj videoer …" (`plus`, multi-select), and you can also drop files onto the card. With one video
the old single-video card is shown. The list appears from the second video.

**Card 2, "For alle videoerne":**

- "Klip videoerne først …";
- "Forbedr lyden …" (see 8);
- "Godkend Claudes klipforslag uden at gennemse dem". Its hint says that this lets the queue run overnight.
  Otherwise each video waits for you at "Du gennemser klippene", and the queue moves on to the next video meanwhile;
- "Når køen er færdig:", a combo with Gør ingenting / Sluk pc'en / Gå i dvale.

**The Start button** reads "START KØEN". The hint next to it gives the count and an estimate, and says that you can
add videos while the queue runs.

**Work view.**

- **Left, at the top:** a "Køen" card with "2/4" in Press Start 2P 12 px on the right. Each row is 40 px:
  - a status icon (`step-done`, `step-active-*`, `step-your-turn-*` or `step-pending`);
  - the name;
  - the status on the right in Semibold 13 px: "Færdig" in `ok`, "Kører · 57%" in `heading`, "Din tur: gennemse
    klip" in `your_turn_title`, "Venter" in `hint`.
  - The running row has `picker_hover_bg`.
- **Below that card:** "Arbejder på" + name, the progress bar, then "Pause køen" and "Annullér denne video".
- **Right:** when a video waits for you, the Claude card turns into the your-turn card for that video, with the
  buttons "Gennemse klippene nu" (primary) and "Senere".

**Done view.**

- **Banner:** `art/queue-banner.png`, "ALL CLEAR!".
- **Result card "Køen er færdig":** one 52 px row per video. Each row has:
  - a status icon;
  - the output file name in Semibold, with a `hint` line under it ("-14 s klippet væk · 1:11:50", or the error);
  - icon-only Afspil and Vis i mappe, or "Prøv igen" for a failed video.
- **Right column:**
  - "I alt": "3/4 videoer klaret", "-53 S klippet væk", "2:31 timer brugt".
  - "Hvad nu?": "Prøv <video> igen" (primary, shown only on errors), "Ny kø" (`queue`), and "Åbn mappen med alle
    videoerne".

### 3. Update banner (`update-banner`; `update.png` is unchanged)

The banner is a 52 px band right under the header stripe, 28 px from both sides, drawn with the
`frames/update-banner` 9-slice (`callout_bg`). The main content moves down 64 px while it shows.

**Contents, from the left:**

- `up-on-callout` (20 px);
- "NY VERSION" in Press Start 2P 12 px;
- "2.1.0 er klar" in Semibold;
- "· Genstart for at opdatere. Dine indstillinger bevares.";
- on the right: "Genstart nu" (primary on Lys, secondary on Nat, because Nat's band is yellow), "Senere", and a
  close ×.

**Behaviour:**

- While a video is running, "Genstart nu" is disabled with the tooltip "Når videoen er færdig".
- Senere hides the banner until the next start.

### 4. Error report (`report-entry`, `report-dialog`)

Reports go to the relay on swaipa.dk (`docs/report-relay.md`), not by e-mail.

**Buttons:**

- In the error done view, a secondary "Send fejlrapport …" (`mail`) under the error text, with a hint 13 px next to
  it ("Rapporten går til udvikleren, …"). The existing "Åbn logfil" button stays.
- In Indstillinger, the same button in the footer after the version number.

**Dialog:**

- The title "Send fejlrapport" (after a crash: "Programmet stoppede med en fejl. Vil du sende en fejlrapport om det?").
- A card "Det kommer med" with disabled check boxes, one per group of the report's fields: the error and the failed
  step (only when there is an error), the versions with language and theme, the run's choices and steps, and the last
  lines of the log with a secondary "Vis loggen" / "Skjul loggen" button at the right. The button opens a read-only
  box (Consolas 9 pt, 12 lines, `input` colours) with the exact redacted log tail that is sent. Under it the `hint`
  "Videoen, lyden og underteksterne kommer aldrig med. …".
- "Hvad skete der? (kan udelades)", a multi-line `input` field.
- A `hint` saying the report goes to the developer and nothing is sent before Send.
- Buttons Annullér and "Send" (primary, `arrow-right-on-primary`). While sending: both disabled, "Sender …" in `hint`.
- Then a result line in place of the buttons' question: `success` "Tak. Fejlen er sendt (#12).", `text` "Den bliver
  sendt, næste gang der er net." (or the relay is busy), or `error` with the reason. Send becomes Luk.

### 5. Burned-in subtitles (`burnin-settings`)

This is a new Indstillinger card, "Undertekster i videoen". Indstillinger is now 1000 px wide, and its cards can
collapse (▸).

- **Left, the controls:**
  - "Størrelse": a picker `S M L` in Press Start 2P 11 px.
  - "Placering": a picker "Nederst / Øverst" in Segoe UI.
  - A check box "Mørk boks bag teksten", with a hint.
  - A check box "Logo i et hjørne". Under it: a 34 px button with the file name (`folder`), and a corner picker. The
    corner picker is a 2×2 grid of 18×12 cells in an `input` frame; the chosen corner is filled with `heading` on Lys
    or `accent` on Nat, and the others are 2 px outlines.
- **Right:** a live sample frame, 420×236, with the 2 px border. It updates at once, with a `hint` caption under it.
- **Sizes:** relative to the video height, S = 4 %, M = 5 % and L = 6.5 %. The box is black at 72 % opacity. The
  logo is 7 % of the width, 3 % in from the edges.

### 6. Voice preview (`voice-preview-setup`, `voice-preview-settings`)

**The play button:** a square 34×34 `button-secondary` with `voice-play`, sitting right of the "Tale" combo. While
it plays, it shows `stop`.

**The sample bubble while playing:**

- It sits under the combo, framed with `button-secondary` (no hover).
- Inside, left to right: 12 level bars (4 px wide, played part in `heading`, the rest in `step_pending_icon`), the
  sample sentence in italics between » «, and the time "0:02 / 0:04" in VT323 18 px `hint` on the right.
- It closes when the sample ends.

**Tooltip:** "Hør stemmen · svensk, Sofie".

**In Indstillinger, "Stemmer i dubben":**

- The radio buttons for the voice mode stay as they are, with no play buttons, because a mode is not a voice.
- Under a dashed divider, one row per dub language: the language, a voice combo (220 px) and the play button.

### 7. Chapters and description (`chapters-done`)

- **Placement:** on the done view, a card "Kapitler og beskrivelse" replaces the compact step list in the right
  column. The step list appears only after an error.
- **Card header:** "FRA CLAUDE" in Press Start 2P 10 px `hint` on the right.
- **Description:** two or three sentences in `text`.
- **Chapters:** 28 px rows, with the time in VT323 20 px `heading` (70 px wide) and then the title.
- **Buttons:** "Kopiér kapitler" and "Kopiér beskrivelse" (`copy`, 40 px). After copying, a button reads
  "Kopieret ✓" for 2 s.
- **Under the buttons:** a `hint` naming the file.
- **Files list:** it gains "<video> - kapitler.txt" with the `chapters` icon and the type "kapitler".

### 8. Small ones

**Forbedr lyden:**

- A check box in setup card 2, after the language rows: "Forbedr lyden: ens lydstyrke og mindre støj", with the
  hint "Gør tale lettere at høre og fjerner sus og brum. Påvirker kun den nye video."
- Its default comes from Indstillinger → "Lyd" → "Forbedr lyden som standard".
- In the queue, it is in "For alle videoerne".

**Fyldord (`filler-review`)** is a new cut kind, `filler`:

| Token | Lys | Nat |
|---|---|---|
| `cut_filler_border` | #4d7c0f | #a3e635 |
| `cut_filler_fill` | #ecf5dc | #26350f |

- **Contrast of the border:** on the timeline 4.99 (Lys) and 12.81 (Nat); on its own fill 4.44 (Lys) and 8.71 (Nat).
- **Legend label:** "Fyldord" (sv "Utfyllnadsord", no "Fyllord", en "Filler words"), last in the legend.
- **List labels:**
  - `Fyldord: "øh"`;
  - repeated words read `Gentagelse: "vi vi skal"`.
- **Bulk action:** the title row gets a secondary "Behold alle fyldord (N)", which keeps them all in the video.
- **Detail column:** shows the quote with the filler in context, and "Hør det" instead of "Se overgangen".
- **On the timeline:** fillers are often narrow. When a cut is narrower than its label, show only the number.

## Install window: animation, progress, done and error (T12)

The mockups are in `design/mockups/<direction>/`:

- `install-progress.png`: the window while it installs.
- `install-progress-states.png`: every status line with its animation.
- `install-done.png` and `install-done-no-claude.png`: the done screen, with and without the Claude note.
- `install-failed.png`: the error state.
- `install-anim-sheet.png`: every animation frame.

The art is drawn by `design/mockups/source/install_anim.py`, and the mockups come from
`design/mockups/source/install_mockups.py`.

### Animations (`<theme>/art/`, transparent PNG)

Each frame is 160×120 (1x) and 320×240 (@2x). One art pixel is 2 screen px at 1x. Show the frames at 1x or @2x;
never scale them by a non-integer factor.

| Loop | Files | Frames | Frame time | Used for |
|---|---|---|---|---|
| cut | `install-anim-0..7` | 8 | 110 ms | The mascot snips a film strip with scissors. The cut frame flies off and tumbles; "SNIP!" shows on frames 0–1. |
| rewind | `install-anim-rewind-0..3` | 4 | 140 ms | A cassette whose reels turn backwards, with blinking ◀◀ arrows. |
| phone | `install-anim-phone-0..3` | 4 | 160 ms | The mascot holds the handset while the pink phone rings ("RING!"), then "P2?". |
| cheer | `install-anim-cheer-0..2` | 3 | 180 ms | The done screen: the mascot crouches, jumps with "1UP" and lands, with confetti. Loop it, or play it 3 times and stop on frame 2. |
| fail | `install-anim-fail-0..1` | 2 | 400 ms | The error screen: X eyes and stars circling the head. |

- **Themes:** A and B differ only in the outline colour: `#1a0840` in Light and `#0b0320` in Night.
- **Loops:** every loop is seamless. Restart from frame 0 whenever the status line changes animation.
- **Reduced motion:** when Windows has animations turned off (SPI_GETCLIENTAREAANIMATION), show frame 0 of the loop
  still. For cheer, show frame 1.

### While it installs (`install-progress`)

The window is the same one as `install-2` and `update`, from top to bottom:

1. The logo header, without a page number.
2. "Installerer SubaDubaLingo 2.1.0", or "Opdaterer til 2.1.0" for an update.
3. A `card` holding the animation on the left at 1x.
4. On the right of the card, the status headline in Press Start 2P 14 px. Use `head` in A and `accent` in B. The
   explanation goes under it in the body font.
5. Under the card, the segmented progress bar (`progress-trough`, `progress-segment`, `progress-segment-current`) and
   "Trin n af 8" with a percentage.
6. A hint: "Det tager typisk 10–30 sekunder. Vinduet kan ikke lukkes, mens programmet bliver kopieret."

While it is copying:

- Show no buttons, and grey out or disable the title bar ✕ and Alt+F4.
- Keep pumping the message loop, by doing the copy on a worker thread, so the window does not freeze.

Each status line stays at least 2 s. If its step is finished before then, go on to the next step without showing
the line, but always show "INSERT COIN" first. The progress bar follows the real work, not the 2 s minimum.

### Status lines (pixel headline / plain explanation)

These map the work steps to text and animations. The Danish lines are the agreed source. The other languages are
puns in the same spirit, not translations. Press Start 2P covers …, Å, Ä, Ö, Æ and Ø.

| Step | Loop | DA | SV | NO | EN |
|---|---|---|---|---|---|
| start | cut | INSERT COIN … / Klar, parat, rul! | INSERT COIN … / Klara, färdiga, rulla! | INSERT COIN … / Klar, ferdig, rull! | INSERT COIN … / Ready, steady, roll! |
| copy program | rewind | Spoler båndet tilbage … / Kopierer filmrullerne ind på harddisken | Spolar tillbaka bandet … / Filmrullarna kopieras in på hårddisken | Spoler tilbake båndet … / Filmrullene kopieres inn på harddisken | Be kind, rewind … / Copying the film reels onto your hard drive |
| unpack/check | cut | Splejser filmstumperne … / Saksen går, tapen sidder | Klipper och tejpar … / Saxen klipper, tejpen håller | Skjøter filmbitene … / Saksen klipper, tapen holder | Splicing the footage … / Snip, snip, tape, done |
| move models | rewind | Flytter stemmebåndene … / Dansk, svensk, norsk og engelsk på plads i reolen | Bär in röstkassetterna … / Danska, svenska, norska och engelska på plats i hyllan | Bærer inn stemmebåndene … / Dansk, svensk, norsk og engelsk på plass i hylla | Shelving the voice tapes … / Danish, Swedish, Norwegian and English, all in their cases |
| shortcuts | cut | Sætter VHS-klistermærker på kassetterne … / Du finder dem i Start og på skrivebordet | Sätter etiketter på kassetterna … / Du hittar dem i Start och på skrivbordet | Setter VHS-klistremerker på kassettene … / Du finner dem i Start og på skrivebordet | Sticking on the VHS labels … / You'll find them in Start and on the desktop |
| connect Claude | phone | Ringer til Player 2 … / Claude tager telefonen | Ringer spelare 2 … / Claude svarar i luren | Ringer spiller 2 … / Claude tar telefonen | Calling Player 2 … / Claude picks up the phone |
| remove old version | cut | Sletter de gamle optagelser … / Videosprog ryger i returkassen | Spelar över det gamla avsnittet … / Videosprog åker tillbaka till videobutiken | Tar opp over det gamle programmet … / Videosprog havner i returkassa | Taping over the old show … / Videosprog goes in the returns box |
| final check | rewind | Tracking-justering … / Sneen forsvinder fra billedet | Justerar trackingen … / Snöet försvinner från bilden | Justerer trackingen … / Snøen forsvinner fra bildet | Adjusting the tracking … / The snow is clearing from the picture |
| done | cheer | LEVEL UP! 2.1.0 / Kassetten er spolet tilbage og klar til afspilning | LEVEL UP! 2.1.0 / Kassetten är tillbakaspolad och redo att spelas | LEVEL UP! 2.1.0 / Kassetten er spolt tilbake og klar til avspilling | LEVEL UP! 2.1.0 / Rewound and ready to play |
| error | fail | GAME OVER? Nej, CONTINUE! / Prøv igen (indsæt ny mønt) | GAME OVER? Nej, CONTINUE! / Försök igen (stoppa i ett mynt) | GAME OVER? Nei, CONTINUE! / Prøv igjen (sett inn en ny mynt) | GAME OVER? No, CONTINUE! / Try again (insert coin) |

### Done (`install-done`, `install-done-no-claude`)

This screen replaces the grey Windows message box and stays in the same window. From top to bottom:

1. The header.
2. A `card` with the cheer animation and "LEVEL UP! 2.1.0" in Press Start 2P 26 px:
   - A: `#d1186f` with a 3 px `#22e4ff` shadow.
   - B: `#ffd400` with a 3 px `#ff2e88` shadow.
3. In the card, "Version 2.1.0 er installeret" (Semibold 17) and the explanation line as a hint.
4. **Only when Claude was connected:** a callout band (`callout_bg` / `callout_text`) with the arrow icon. It reads
   **"Genstart Claude"**, then "Luk Claude helt (Quit i menuen) og åbn den igen, så Claude bruger den nye version."
5. The buttons at the bottom right: "Luk" (secondary) and a yellow "▶ Start SubaDubaLingo" (primary, the default
   button).

The title bar ✕ works again on this screen.

### Error (`install-failed`)

1. The header.
2. A `card` with the fail animation and "GAME OVER? / Nej, CONTINUE!" in Press Start 2P 18 px, using the same
   colours as LEVEL UP.
3. In the card, the hint "Installationen blev ikke færdig. Der er ikke ændret noget i din gamle version." Only say
   this when it is true, which means a rollback.
4. Under the card, the real error in `err` with the `err` icon.
5. The buttons: "Luk" and the primary "Prøv igen (indsæt ny mønt)".

## Larger art (`<theme>/art/`, `common/`)

- `header-logo.png` (+ `@2x`): icon and wordmark in the theme's colours on a transparent background, 52 px tall at 1x.
  It goes on the header band (`header_band` colour).
- `header-stripe.png` (+ `@2x`): the 4-colour stripe under the header, 1920×6 at 1x. Crop it to the window width, or
  draw four Canvas rectangles from `header_stripe` in tokens.json.
- `continue-banner.png` (+ `@2x`): the same banner with CONTINUE? for the done view after an error (`5-done-error.png`).
- `done-banner.png` (+ `@2x`): the 960×230 STAGE CLEAR! banner for the done view. It is the same night artwork in both
  themes; only the frame colour differs.
- `common/splash.png` (+ `@2x`): the 1200×680 title screen, for an About dialog or a short splash at start.

## Fonts (`../fonts/`)

- `PressStart2P-Regular.ttf` and `VT323-Regular.ttf`, with their SIL OFL 1.1 licences (`*-OFL.txt`).
- **Loading:** load them privately at start with `AddFontResourceExW(path, FR_PRIVATE, 0)` via ctypes, before creating
  the Tk root. They then work as `("Press Start 2P", 12)` and `("VT323", 20)`.
- **What to use them for:** only the logo, short headings, numbers and the language codes. All instructions, labels
  and messages stay in Segoe UI.
- **Danish letters:** both full TTFs contain Æ Ø Å. The web subset used in the mockups lacked them, which is why the
  mockups avoid them in pixel text.

## Notes

- **Header and window:** the header band is a plain colour (`header_band`) with `header-logo` on it. The window
  background is `window`.
- **Title bar in Nat:** for B, set a dark title bar with `DwmSetWindowAttribute(hwnd, 20, 1)`
  (DWMWA_USE_IMMERSIVE_DARK_MODE). Windows' file dialogs and message boxes stay light; that is expected.
- **Timeline drawing order:** fill each cut with `cut_<kind>_fill`, then draw the waveform (`timeline_waveform`) and
  the speech blocks (`timeline_speech`) on top, and finally the cut outline (3 px, or 5 px when selected), the drag
  handles and the label in `cut_<kind>_border`.
