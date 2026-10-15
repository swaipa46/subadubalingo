# Glossary: the domain language of SubaDubaLingo

The code, the MCP tools, the protocol with Claude (headers, sections, cut kinds), the skill and the prompts use
these English words. The user interface, the output files next to the video and the user guide are Danish: the
right-hand column shows what the user sees.

| Term | Meaning | In code | The user sees |
|---|---|---|---|
| Video | The original recording the user picks. Never changed. | `video` | video |
| Job | One run for one video: what to make, its state, its files. Shared between the app and the MCP server. | `jobs.py`, `jobs/<id>/` | – |
| State | Where a job is: queued, running, claude_turn, review_cuts, paused, done, error, cancelled, busy. | `jobs.STATES` | the steps on the left |
| Spoken language | The language spoken in the video. | `lang`, `spoken_language` | "Sprog i videoen" |
| Transcription | Speech recognition (Whisper) of the spoken language into cues. | `engine.transcribe` | "Lytter og skriver tekst" |
| Cue | One subtitle block: start, end (ms), text. Numbered from 1. | `Cue` | blok |
| Transcript | All cues in the spoken language, corrected or not. | `source.json`, `*.srt` | "danske undertekster" |
| Correction | A rule `wrong = right` for a word the recognition mishears; applied after transcription and sent to Claude. | `corrections.txt` | "rettelser" |
| Word list | The right-hand sides of the corrections: product and screen names, spelled the same in every language. | `known_words()` | "ordliste" |
| Proofreading | Claude correcting misheard words in the transcript. | `check`, `proofread` | "Claude retter" |
| Translation | Claude translating the cues, keeping numbers and time codes. | `targets` | "oversætter" |
| Claude's turn | The step where Claude proofreads, translates and suggests cuts, through the MCP tools or the task/answer files. | `claude_turn` | "Din tur" |
| Task file / answer file | `<video> - til Claude.txt` (`SUBADUBALINGO-TASK`) and `<video> - fra Claude.txt` (`SUBADUBALINGO-ANSWER`, sections `=== <code> ===`, `=== cuts ===`, `=== chapters ===`, `=== summary ===`, `=== words ===`, `=== end ===`). | `claude.work_file_text`, `check_file_answer` | the files in Downloads |
| Skill | The instructions Claude gets as an uploaded skill `subadubalingo`, with a version number the app checks. | `claude.SKILL_MD`, `SKILL_VERSION` | "Claude-skill'en" |
| Cut suggestion | A line `<first>-<last block> <kind> <reason>` from Claude, or a silent pause the app found. | `suggest_cuts`, `parse_cut_lines` | – |
| Cut | A time range to remove, with a kind: start, pause, end, inappropriate, offtopic, unclear, manual, filler. | `Cut`, `KINDS` | "klip" (Danish labels) |
| Filler | A cut kind (skill 6): blocks with nothing but filler words, false starts, repetitions or hesitation. | `"filler"` | "Fyldord" |
| Chapters / summary | Claude's chapters (`0:00 Title`) and a short description of the video (skill 6), saved as `<video> - kapitler.txt` and shown on the done view. | `chapters.py`, `save_chapters` | "Kapitler og beskrivelse" |
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
