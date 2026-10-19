# Glossary: the domain language of SubaDubaLingo

The code, the MCP tools, the answer format of the assistant (section markers, cut kinds) and the prompts use these
English words. "The assistant" is whatever AI does the language work: one driving the app through MCP (Claude,
Gemini CLI, Codex, Copilot, Cursor, ...) or any AI chat the user copies the instructions into. The user interface, the output files next to the video and the user guide are Danish: the
right-hand column shows what the user sees.

| Term | Meaning | In code | The user sees |
|---|---|---|---|
| Video | The original recording the user picks. Never changed. | `video` | video |
| Job | One run for one video: what to make, its state, its files. Shared between the app and the MCP server. | `jobs.py`, `jobs/<id>/` | – |
| State | Where a job is: queued, running, claude_turn, review_cuts, paused, done, error, cancelled, busy, by_user (the user did the assistant's turn in an AI chat). | `jobs.STATES` | the steps on the left |
| Spoken language | The language spoken in the video. | `lang`, `spoken_language` | "Sprog i videoen" |
| Transcription | Speech recognition (Whisper) of the spoken language into cues. | `engine.transcribe` | "Lytter og skriver tekst" |
| Cue | One subtitle block: start, end (ms), text. Numbered from 1. | `Cue` | blok |
| Transcript | All cues in the spoken language, corrected or not. | `source.json`, `*.srt` | "danske undertekster" |
| Correction | A rule `wrong = right` for a word the recognition mishears; applied after transcription and sent to the assistant. | `corrections.txt` | "rettelser" |
| Word list | The right-hand sides of the corrections: product and screen names, spelled the same in every language. | `known_words()` | "ordliste" |
| Proofreading | The assistant correcting misheard words in the transcript. | `check`, `proofread` | "AI-assistenten retter" |
| Translation | The assistant translating the cues, keeping numbers and time codes. | `targets` | "oversætter" |
| The assistant's turn | The step where the assistant proofreads, translates and suggests cuts: through the MCP tools, or in an AI chat (the chat view: copy the instructions per part, copy the answer back). | `claude_turn`, `ClaudePanel` | "Din tur", the "AI-assistent" card |
| Take over | An MCP assistant takes the assistant's turn of a job the user started in the app ("Tag over i SubaDubaLingo"): `get_text` marks it taken and the card shows the assistant's progress. | `jobs.publish_app_job`, `taken.json`, `App.adopt_job` | "Tag over i SubaDubaLingo" |
| Instructions / answer | The self-contained prompt per part (`claude.prompt`) and the chat's answer: one code block with `=== <code> ===` per language, `=== cuts ===`, `=== chapters ===`, `=== summary ===`, `=== words ===` (the parser also reads markdown headings, bold labels and a code block per language). | `claude.prompt`, `claude.check_answer` | "Kopiér instruktioner til AI-chat", "Indsæt svar" |
| Old skill | Up to 2.1.x a Claude skill `subadubalingo` (earlier `videosprog`) with a task-file protocol. Removed in 2.2.0; a one-time notice asks upgraders to delete it. | `upgrade_notice.py`, `guide.OLD_SKILL` | "Ryd op i Claude" |
| Cut suggestion | A line `<first>-<last block> <kind> <reason>` from the assistant, or a silent pause the app found. | `suggest_cuts`, `parse_cut_lines` | – |
| Cut | A time range to remove, with a kind: start, pause, end, inappropriate, offtopic, unclear, manual, filler. | `Cut`, `KINDS` | "klip" (Danish labels) |
| Filler | A cut kind: blocks with nothing but filler words, false starts, repetitions or hesitation. | `"filler"` | "Fyldord" |
| Chapters / summary | The assistant's chapters (`0:00 Title`) and a short description of the video, saved as `<video> - kapitler.txt` and shown on the done view. | `chapters.py`, `save_chapters` | "Kapitler og beskrivelse" |
| Cut review | The timeline where the user adjusts, keeps or adds cuts before anything is cut. | `review.py`, `review_cuts`, `approve_cuts` | "Gennemse klippene" |
| Cut video | The video with the cuts removed; subtitles and dub are made for it. | `cutting.cut_video` | "... - klippet.mp4" |
| Dub | New speech in another language, a synthetic voice per sentence, timed to the original. | `engine.dub` | "svensk dub" |
| Sentence | Cues joined into one spoken sentence for the dub. | `Sentence`, `engine.sentences` | – |
| Voice | A Piper voice (with a pitch shift) used for one speaker. | `GENDER_VOICES`, `PERSON_VOICES` | "stemme" |
| Speaker | A person in the video, found by voiceprint (or by pitch: male/female). | `speakers.py`, `pitch.py` | – |
| Burn-in | Subtitles drawn into the picture. | `burned_name` | "med danske undertekster" |
| Model | A downloaded file the app needs (Whisper, Piper voices, voiceprint). | `models/`, `model_dirs()` | "filer" |
| Release | A version published on GitHub with a signed manifest `latest.json`. | `update.py`, `tools/release_key.py` | "ny version" |
| Installation | One folder per version under `Programs\SubaDubaLingo`, `current.txt` names the current one. | `installer.py` | – |
