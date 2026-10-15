# Locales

The app's own language (Programmets sprog): `da`, `sv`, `no`, `en`. One folder per language with the same files and the
same keys; one file per area (`gui.json` holds the `gui.*` keys). See `app/subadubalingo/i18n.py`.

- `{name}` placeholders are filled in by the code; keep them, move them where the sentence needs them.
- `lang.json`: the video languages in this UI language. Forms: `name` ("Dansk", in lists), `plural` ("danske", as in
  "de danske undertekster"), `singular` ("dansk", as in "dansk dub" / "på danska"), `choice` (subtitle list entry).
  Code never upper- or lowercases these; pick the form a sentence needs.
- `file.json`: output file names. Files are recognised in all four languages, so a name may change but must stay unique.
- Danish is the source. When a text changes, change it in all four files.
