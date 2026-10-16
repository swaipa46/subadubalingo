"""The manual for any AI assistant that drives the app through the MCP server (Claude, Gemini CLI, Codex CLI, VS Code
Copilot, Cursor): the steps, the formats and rules for corrections, translation, cuts and chapters, the word list, the
language to talk to the user in and what never to do.

The Claude skill (claude.SKILL_MD) is built from the same pieces, so the two cannot drift apart: the rules for the
text, cuts and chapters and the tool steps below are the skill's own text, word for word; only the frame around them
differs (the skill also covers the task file and pasting, which need Claude's chat). The MCP server serves the guide
as the tool "guide", as the prompt "subadubalingo_workflow" and as the resources "subadubalingo://guide[/<section>]"
(mcp_server.py). This module only holds text: the word list and the user's language come in as arguments, so it
imports nothing of the app.
"""
from __future__ import annotations

# --- Pieces shared with the Claude skill (claude.SKILL_MD); change them here and both follow --------------------------

TASK_HEAD = """## The task
Do what the task says (language codes: da, sv, no, en):
"""

TASK_TEXT = """- **Correct** the text in the spoken language: misheard words, especially names, products and screen names (see
  the word list below and the one in the task). Do not turn spoken language into written language, and do not
  change the content.
- **Translate** to the languages the task names. Natural spoken language, as a Swede/Norwegian would say it.
  Product names and screen names are not translated.
- The same numbering and exactly the same time codes as the original. The same number of blocks; never merge
  blocks and do not split them, not even mid-sentence. At most 2 lines per block, about 42 characters per line.
"""

TASK_CUTS = """- **Suggest cuts** when the task asks for it (`cut=yes`, or "suggest what to cut out"). The video is usually a
  recording of a Teams meeting or webinar. Suggest whole blocks, one line per cut:
  `<first block>-<last block> <kind> <short reason>`, e.g. `1-14 start Folk kommer ind og tester lyd` (the reason
  in the user's language: they read it). Kinds:
  - `start`: before the meeting really begins (people joining, sound checks, "kan I høre mig?", "vi venter lige")
  - `pause`: interruptions and waiting along the way ("to sekunder, min skærm frøs")
  - `end`: after the meeting is over ("tak for i dag" and everything after, "er optagelsen slukket?")
  - `inappropriate`: what should not be kept forever: private matters, customers or colleagues mentioned in a
    vulnerable context, crude language
  - `offtopic`: fooling around and talk that is not about the subject
  - `unclear`: what cannot be understood, not even in context
  - `filler`: blocks with nothing but filler words and hesitation: "øh", "øhm", false starts ("vi skal ... nej,
    vi"), a repeated word or phrase ("det er, det er"), a long hesitation. Only a block that holds nothing else:
    never cut a block that also says something. Short blocks are fine; the app cuts them cleanly. The reason is
    the filler in quotes (`240 filler "øh, øhm"`), or for a repetition the word for repetition in the user's
    language, a colon and the words (`251 filler Gentagelse: "vi vi skal"`).
  Be careful: only cut what clearly does not belong, and never in the middle of an explanation that goes on.
  The app finds long silent pauses itself. The user sees every cut on a timeline and approves them before
  anything is cut. Keep the time codes unchanged; the app moves them after cutting.
"""

TASK_CHAPTERS = """- **Chapters and a summary** when the task asks for it (`chapters=<code>`, or "make chapters and a short summary";
  write them in that language, the language of the new video):
  - Chapters where the subject changes, one per line `<start time> <title>`, e.g. `0:00 Velkommen` and
    `2:15 Sådan opretter du et medlem`. The start time is the time code of the block the chapter begins with
    (`m:ss`, or `h:mm:ss` from one hour), as in the original; the app moves the times if the video is cut. The first
    chapter at `0:00`, at least 3 chapters of at least 10 seconds each, titles of 2-6 words. None if the video is
    too short for that.
  - A summary: 2-4 sentences about what the video is about and what the viewer gets out of it (for e.g. a YouTube
    description), no chapter list in it.
  The app saves them as "<video> - kapitler.txt" (ready to paste as a YouTube description) and shows them to the user
  with copy buttons.
"""

TASK = TASK_HEAD + TASK_TEXT + TASK_CUTS + TASK_CHAPTERS

# The steps with the tools. Step 3's "in chunks ..." differs: the skill adds the skill version to pass along.
TOOLS_INTRO = """With the tools `start_video`, `wait`, `status`, `get_text`, `save_text`, `suggest_cuts`, `save_chapters`,
`approve_cuts`, `cancel` and `find_videos`, do it all in the chat:
1. The user writes e.g. "lav denne video på svensk med norske undertekster: C:\\...\\video.mp4". Call
   `start_video` (video, speech, subtitles, maybe spoken_language). If you do not know the path, use
   `find_videos` or ask. If the user wants the video cut first (e.g. "klip ventetiden og pauserne væk", "fjern
   det, der ikke skal med"), set `cut` to true. Only cutting, no new languages: `speech` original and `subtitles`
   none.
2. Call `wait` again and again. Write a short line to the user at every 10 % (e.g. "40 % - listening through
   the video", in the user's language). Listening can take an hour; keep going, and if you were interrupted,
   `status` picks it up again.
3. When the job is in `claude_turn`: fetch the text with `get_text` in chunks """

TOOLS_REST = """, correct and translate by the rules above, and hand each language back with `save_text` (same
   numbers and time codes). The answer says what is missing or wrong; fix it. Pass corrected misheard words in
   `words` (`wrong => right`). If the video is to be cut, read the whole text and hand in your suggestions with
   `suggest_cuts` (block numbers as above; `done` true at the end, also when you have none). When the task asks
   for chapters, hand in the chapters and the summary with `save_chapters` once you have read the whole text (an
   empty `chapters` when the video is too short).
   When the cut suggestions and every language are in, the app shows the cuts to the user on a timeline
   (`review_cuts`). Tell the user briefly what you suggested cutting (time and why). The user can change it in
   the app or tell you: "behold det ved 14:20" -> `approve_cuts` with `keep` = the cut's number; "klip også
   22:10-22:40" -> `approve_cuts` with `add`. `approve_cuts` without `wait_for_user` cuts at once, so ask the user
   whether the cuts are right before you approve.
4. When everything is in, the app does the rest. Call `wait` until "done", and tell the user the name of the new
   video and that it can be played from the app.
If the user wants to stop, or a job hangs and blocks the app, use `cancel` (ask first).
"""


def tools_steps(chunk: str) -> str:
    """The steps with the tools; chunk is what follows "get_text in chunks" (the skill: with the skill version)."""
    return TOOLS_INTRO + chunk + TOOLS_REST


def words_md(words: list[str], rules: list[tuple[str, str]], heading: str = "# Word list") -> str:
    """The word list (the skill's words.md): the known words and the typical mishearings from corrections.txt."""
    lines = [heading, "", "Known words (spelled exactly like this in every language):", ""]
    lines += [f"- {w}" for w in words] or ["- Membercare"]
    lines += ["", "Typical mishearings by the speech recognition:", ""]
    lines += [f"- {w} => {r}" for w, r in rules]
    return "\n".join(lines) + "\n"

# --- The guide for any MCP client ----------------------------------------------------------------------------------

GUIDE_TITLE = "# SubaDubaLingo: the manual for AI assistants\n"

ABOUT = """SubaDubaLingo is a Windows app that cuts, subtitles and dubs videos locally on the user's PC, in Danish,
Swedish, Norwegian and English. The app does the slow work itself (listening through the video with speech
recognition, dubbing, burning in subtitles); you do the language work in between: correct what the speech
recognition misheard, translate, suggest cuts, and write chapters and a summary. You drive the app with the tools of
this MCP server. The app checks everything you hand in, and its answers say what is wrong or still missing. The
`claude_turn` state and the "Claude" in some texts mean you, whichever assistant you are.
"""


def talk(language: str) -> str:
    return f"""## Talk to the user
Talk to the user in {language} (the language of their SubaDubaLingo app; if they write to you in another language,
use that). Keep it short and friendly: the user is not technical. Messages "from the app" in the tools' answers are
written in that language and can be passed on as they are. Everything you hand to the app follows the formats below.
"""


STEPS_HEAD = "## Steps\n"

STATES = """## Job states
`wait`, `status` and `start_video` answer with the job's state:
- `queued`, `running`: the app is working; call `wait` again (tell the user at every 10 %).
- `paused`: the user paused it in the app; call `wait` again when they resume.
- `claude_turn`: your turn: `get_text` from block 1, then `save_text` (and `suggest_cuts`, `save_chapters` when the
  task asks for them).
- `review_cuts`: the user is reviewing the cuts in the app; tell them what will be cut and ask whether it is right.
- `done`: tell the user the new video is ready and can be played from the app.
- `error`: tell the user the app's message; the log can be opened from the app's settings.
- `busy`: the app is working on another video; try again later, or ask whether to stop it with `cancel`.
- `cancelled`: the job has stopped.
"""

NEVER = """## Never
- Never change the numbering or the time codes, and never merge or split blocks.
- Never translate or respell product and screen names in the word list.
- Never write the subtitles into the chat: hand them to the app with `save_text` (and `suggest_cuts`,
  `save_chapters`).
- Never call `approve_cuts` without `wait_for_user` before the user has said the cuts are right.
- Never call `cancel` without asking the user first.
- Never cut a block that also says something, and never in the middle of an explanation that goes on.
- Never make, cut, convert or delete video or subtitle files yourself (no ffmpeg, no scripts): the app does that.
- Never start the same video again while its job is running; pick it up with `status` or `wait`.
- Never ask the user to install a Claude skill or to paste a task file: with these tools you need neither.
"""

SECTIONS = ("steps", "text", "cuts", "chapters", "words", "never")


def guide_text(language: str, words: list[str], rules: list[tuple[str, str]], section: str | None = None) -> str:
    """The guide, whole or one section (SECTIONS). language: the user's language in English (the app's UI language);
    words, rules: the word list (claude.known_words, claude.word_rules)."""
    parts = {
        "steps": STEPS_HEAD + tools_steps("(about 120 blocks)") + "\n" + STATES,
        "text": TASK_HEAD + TASK_TEXT,
        "cuts": "## Cuts\n" + TASK_CUTS,
        "chapters": "## Chapters and a summary\n" + TASK_CHAPTERS,
        "words": words_md(words, rules, "## Word list"),
        "never": NEVER,
    }
    if section:
        key = section.strip().lower()
        if key not in parts:
            raise ValueError(f"Unknown section {section!r}. Use one of: {', '.join(SECTIONS)} (or leave it out).")
        return GUIDE_TITLE + "\n" + talk(language) + "\n" + parts[key]
    return "\n".join([GUIDE_TITLE, ABOUT, talk(language), parts["steps"], TASK, parts["words"], NEVER]).rstrip() + "\n"
