# SubaDubaLingo branding

The SubaDubaLingo logo is an 80s/90s arcade pixel-art design. The icon is a neon speech bubble shaped like a screen,
with a play button and subtitle lines. The wordmark is SUBADUBA**LINGO** and the tagline is **CUT · DUB · SUB**.

Since 2.0.0 the app uses `SubaDubaLingo.ico` (exe, window, taskbar, shortcuts) and `header-logo.png`/`@2x` (the
header, next to the Danish tagline). `build.ps1` puts the icon in the exe and bundles those three files; the rest
is for the README, an About dialog or slides.

## Files

| File | Use |
|---|---|
| `SubaDubaLingo.ico` | Windows icon: 16, 24, 32, 48, 64, 128 and 256 px, all hand-pixelled. Use it for the exe, the window and the shortcuts. |
| `icon-16.png` … `icon-1024.png` | The icon as PNGs. 16, 32 and 64 use the simplified 16×16 drawing; the others use the 24×24 master. |
| `icon.svg` / `icon-small.svg` | Vector source: the 24×24 master and the 16×16 small-size variant. Use `shape-rendering="crispEdges"`. |
| `header-logo.png` (+ `@2x`) | Icon and wordmark on a transparent background, 56 px tall at 1×. For the app's light header. |
| `header-logo-tagline.png` (+ `@2x`) | The same with CUT · DUB · SUB underneath, 56 px tall at 1×. |
| `logo-light-transparent.png` (+ `@2x`) | Large horizontal logo for light backgrounds, transparent. |
| `logo-light.png` | Large horizontal logo on #f9f9f9. |
| `logo-dark.png` / `logo-dark-transparent.png` | Large horizontal logo for dark backgrounds (yellow/cyan with pink extrusion). |
| `title-screen.png` (+ `@2x`) | 1200×680 arcade "title screen" for a splash screen, an About dialog, a README banner or slides. |
| `source/` | The scripts that generated everything (`make_icons.py`, `render_logos.py`). |

## Palette

| Name | Hex | Role |
|---|---|---|
| Night | `#1a0840` | Icon background, dark backgrounds, wordmark on light |
| Screen | `#2a0f6b` | Inside of the bubble |
| Neon cyan | `#22e4ff` | Bubble outline, "LINGO" on dark |
| Hot pink | `#ff2e88` | Extrusion/shadow, accents |
| Pink (light bg) | `#d1186f` | "LINGO" on light backgrounds (passes contrast) |
| Arcade yellow | `#ffd400` | Play button, "SUBADUBA" on dark |
| Cream | `#fff4d6` | Subtitle lines, tagline on dark |
| Deep purple | `#4a148c` | Second extrusion step on dark |

## Fonts

- Wordmark: **Press Start 2P**. Tagline: **VT323**. Both are from Google Fonts and licensed under SIL OFL 1.1.
- The PNGs already contain the text. The app does not need the fonts installed.

## Wiring it into the app (for Claude Code)

Done in 2.0.0, as described here, with two differences: the build is `build.ps1` (PyInstaller on the command
line, `--icon` and `--add-data`), and the AppUserModelID is `SubaDubaLingo.App` (`common.set_app_id`). The app is
Python/tkinter (`app/subadubalingo/gui.py`).

1. **Exe icon:** in `SubaDubaLingo.spec`, add `icon='assets/branding/SubaDubaLingo.ico'` to `EXE(...)`. Also add
   `('assets/branding', 'assets/branding')` to `datas` so the runtime can find the files. The installer's shortcut uses
   `IconLocation = <exe>`, so it picks up the icon automatically once the exe has one.
2. **Window and taskbar icon:** on the root `Tk()`, call `root.iconbitmap(default=<path to SubaDubaLingo.ico>)`.
   Before creating the window, call
   `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('EG.SubaDubaLingo')`. Without it, Windows can group
   the app under python.exe and show the wrong taskbar icon when it runs from source. Resolve paths via
   `sys._MEIPASS` when frozen, and from the repo root otherwise.
3. **Header:** replace the text title "SubaDubaLingo" plus "Undertekster og dubbing af videoer" with a `ttk.Label`
   whose `image=tk.PhotoImage(file=header-logo.png)`. Use the `@2x` file when
   `root.winfo_fpixels('1i') / 72 >= 1.5`, i.e. on a scaled display. Keep a reference to the PhotoImage so it is
   not garbage-collected.
4. Do not scale the pixel art by non-integer factors. Pick the size that fits instead.
