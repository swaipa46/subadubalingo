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
