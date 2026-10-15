"""The Claude step: the user copies a prompt into a Claude chat and pastes the answer back.

One round per part of the video: Claude corrects the subtitles in the spoken language (product names and
other known words), translates them to the languages the new video needs, and lists words it corrected.
Those words are saved to rettelser.txt, so the next transcription gets them right and the next prompt
carries them along. The `videosprog` skill holds the same rules and the word list for claude.ai;
the prompt works on its own too, in case the skill is not installed or not picked up.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from pathlib import Path

from .common import Cue, Language, LANGUAGES, format_srt, log, parse_srt_text
from . import engine

SKILL_NAME = "videosprog"
# Bump when SKILL_MD (or what the program expects from Claude) changes. The program knows which skill version it
# ships with: after an update it guides the user to upload the new skill, and Claude reports the version it has
# (answer file header "skill=N", or hent_tekst(skill=N)), so a forgotten update is caught too.
# 1 = before klip (v1.8), 2 = klip (v1.9.0-1.9.2), 3 = version stamp.
SKILL_VERSION = 3
LANG_BY_CODE = {l.code: l for l in LANGUAGES}

# --- Parts ------------------------------------------------------------------------------

def part_size(n_languages: int) -> int:
    """Blocks per round, so Claude's answer (one SRT per language) is not cut off."""
    return 120 if n_languages <= 2 else 90


def split_parts(cues: list[Cue], n_languages: int) -> list[list[Cue]]:
    size = part_size(n_languages)
    n = max(1, math.ceil(len(cues) / size))
    size = math.ceil(len(cues) / n)
    return [cues[i:i + size] for i in range(0, len(cues), size)]

# --- Word list (shared with rettelser.txt) ------------------------------------------------

def word_rules() -> list[tuple[str, str]]:
    """(wrong, right) pairs from rettelser.txt."""
    try:
        lines = engine.corrections_file().read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            wrong, right = (x.strip() for x in line.split("=", 1))
            if wrong and right:
                out.append((wrong, right))
    return out


def known_words() -> list[str]:
    return sorted({r for _, r in word_rules()}, key=str.lower)


def add_words(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Append new (wrong, right) pairs to rettelser.txt; returns the ones that were new."""
    have = {w.lower() for w, _ in word_rules()}
    new = [(w, r) for w, r in pairs if w.lower() not in have and w.lower() != r.lower()]
    if new:
        f = engine.corrections_file()
        text = f.read_text(encoding="utf-8-sig")
        if not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{w} = {r}\n" for w, r in new)
        f.write_text(text, encoding="utf-8")
        log.info("learned words: %s", new)
    return new

# --- Prompt ---------------------------------------------------------------------------------
#
# Claude answers in ONE code block, with a marker line before each language:
#     === da ===   (corrected text in the spoken language)
#     === sv ===   (translations)
#     === ordliste ===   (forkert => rigtigt)
# One block means one copy, whichever copy button the user uses (the block's or the whole answer's).

CUT_TASK = "foreslå, hvad der skal klippes væk"
CUT_RULES = [
    "Klip: foreslå blokke, der skal klippes ud af videoen (en optagelse af et Teams-møde/webinar):",
    "  start = før mødet reelt går i gang (folk kommer ind, lydtest, \"kan I høre mig\"),",
    "  pause = afbrydelser og ventetid undervejs, slut = efter mødet er slut (snak, \"er den slukket?\"),",
    "  upassende = noget, der ikke skal gemmes (private ting, navne på kunder/kolleger i en sårbar sammenhæng, "
    "grove udtryk), offtopic = fjol og snak uden for emnet, uklart = noget, der ikke kan forstås, heller ikke i "
    "sammenhængen.",
    "  Én linje pr. klip: <første blok>-<sidste blok> <kategori> <kort begrundelse>, fx \"1-14 start Folk kommer "
    "ind og tester lyd\". Klip hele blokke, og vær forsigtig: klip kun det, der tydeligt ikke hører til. Lange "
    "stille pauser finder programmet selv. Brugeren gennemser alle klip, før der klippes.",
]


def prompt(src: Language, proofread: bool, targets: list[Language], part: list[Cue], first_number: int,
           idx: int, n_parts: int, cut: bool = False) -> str:
    tasks = []
    order = []
    if proofread:
        tasks.append(f"ret de {src.plural} undertekster (fejlhørte ord, især navne og fagord)")
        order.append(src.code)
    if targets:
        tasks.append("oversæt dem til " + " og ".join(t.name.lower() for t in targets))
        order += [t.code for t in targets]
    if cut:
        tasks.append(CUT_TASK)
    part_note = f"Del {idx + 1} af {n_parts} (blok {first_number}-{first_number + len(part) - 1}). " if n_parts > 1 else ""
    words = known_words()
    rules = word_rules()
    lines = [
        f"Brug skill'en {SKILL_NAME}, hvis du har den.",
        "",
        f"Undertekster fra programmet Videosprog. {part_note}Opgave: " + ", og ".join(tasks) + ".",
        "",
        "Regler:",
        "- Behold nummerering og tidskoder præcis. Samme antal blokke; slå ikke blokke sammen og del dem ikke.",
        "- Højst 2 linjer pr. blok og ca. 42 tegn pr. linje. Naturligt talesprog, som man ville sige det højt.",
        "- Navne på produkter og skærmbilleder staves som i ordlisten og oversættes ikke.",
        "- Svar direkte i chatten (ingen filer, ingen kode), med ALT i ÉN kodeblok. Før hvert sprog en linje med "
        "=== og sprogkoden, i denne rækkefølge: " + ", ".join(f"=== {c} ===" for c in order) + ".",
    ]
    if cut:
        lines += ["- Derefter i samme kodeblok en linje === klip === med dine klipforslag til denne del (ingen linjer, "
                  "hvis der ikke er noget). Numrene er bloknumrene ovenfor."] + ["  " + r for r in CUT_RULES]
    lines += [
        "- Sidst i samme kodeblok: en linje === ordliste === og derefter de fejlhørte ord, du har rettet, én pr. linje: "
        "forkert => rigtigt. Ingen linjer efter, hvis der ikke var nogen.",
        "- Ingen tekst før eller efter kodeblokken.",
        "",
        "Eksempel på formatet:",
        "```",
        f"=== {order[0]} ===",
        "1",
        "00:00:01,000 --> 00:00:03,000",
        "Tekst",
        "",
    ] + (["=== klip ===", "1-3 start Folk kommer ind og tester lyd", ""] if cut else []) + [
        "=== ordliste ===",
        "memberkær => Membercare",
        "```",
    ]
    if words:
        lines += ["", "Ordliste (kendte ord): " + ", ".join(words)]
    if rules:
        lines += ["Typiske fejlhøringer: " + "; ".join(f"{w} => {r}" for w, r in rules[:60])]
    lines += ["", f"Underteksterne ({src.name.lower()}):", "```", format_srt(part, first_number).rstrip(), "```"]
    return "\n".join(lines) + "\n"

# --- Answer -----------------------------------------------------------------------------------

_NAMES = {"da": ["da", "dansk", "danske"], "sv": ["sv", "svensk", "svenske", "svenska"],
          "no": ["no", "nb", "norsk", "norske"], "en": ["en", "engelsk", "engelske", "english"]}
_LABEL = {alias: code for code, names in _NAMES.items() for alias in names}
_LABEL.update({"ordliste": "ordliste", "rettelser": "ordliste", "ord": "ordliste", "slut": "slut", "klip": "klip"})
# A marker line: "=== sv ===", "### Svensk", "**srt-sv**", "Svensk:" ... (alone on its line)
MARKER_RE = re.compile(r"^[ \t>#*=_-]*(?:srt[-_ ])?([A-Za-zæøåÆØÅ]+)[ \t]*(?:\([^)]*\))?[ \t:*=#_-]*$")
# The format the prompt asks for ("=== sv ==="): a marker wherever it stands.
STRICT_MARKER_RE = re.compile(r"^={2,}\s*(?:srt[-_ ]?)?([A-Za-zæøåÆØÅ]+)\s*={2,}$")
FENCE_RE = re.compile(r"^```[ \t]*([^\n`]*)$")


def _label(line: str) -> str | None:
    m = MARKER_RE.match(line.strip())
    if not m or not line.strip():
        return None
    return _LABEL.get(m.group(1).lower())


def _sections(text: str) -> dict[str, list[str]]:
    """The answer's lines per section: language codes, "ordliste", "klip", "slut"."""
    sections: dict[str, list[str]] = {}
    current = None
    # A marker must start a paragraph, so a subtitle line that just says "Norsk" is not taken for one.
    para_start = True
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        f = FENCE_RE.match(raw.strip())
        if f:
            lab = _LABEL.get(re.sub(r"^srt[-_ ]?", "", f.group(1).strip().lower()))
            if lab:
                current = lab
            para_start = True
            continue
        strict = STRICT_MARKER_RE.match(raw.strip())
        lab = _LABEL.get(strict.group(1).lower()) if strict else (_label(raw) if para_start else None)
        para_start = not raw.strip()
        if lab:
            current = lab
            sections.setdefault(lab, [])
            para_start = True
            continue
        if current:
            sections.setdefault(current, []).append(raw)
    return sections


def parse_answer(text: str) -> tuple[dict[str, list[Cue]], list[tuple[str, str]]]:
    """Per language code the SRT blocks, and the corrected words. Reads the one-block format, separate
    labelled code blocks (```srt-sv), and headings before blocks; fence lines are treated as separators."""
    sections = _sections(text)
    by_lang = {}
    for code, lines in sections.items():
        if code in ("ordliste", "slut", "klip"):
            continue
        cues = parse_srt_text("\n".join(lines))
        if cues:
            by_lang[code] = cues
    return by_lang, parse_words(sections.get("ordliste", []))


def answer_cuts(text: str) -> list[str] | None:
    """The lines of the answer's === klip === section (cut suggestions), or None when it has none."""
    sections = _sections(text)
    if "klip" not in sections:
        return None
    return [l.strip() for l in sections["klip"] if l.strip()]


def parse_words(lines: list[str]) -> list[tuple[str, str]]:
    words: list[tuple[str, str]] = []
    for line in lines:
        m = re.match(r"\s*[-*•]?\s*(.+?)\s*(?:=>|->|→|=)\s*(.+?)\s*$", line)
        if m and m.group(1) and m.group(2):
            words.append((m.group(1).strip('"\'`'), m.group(2).strip('"\'`')))
    return words


def check_answer(part: list[Cue], codes: list[str], text: str, have: dict | None = None
                 ) -> tuple[dict[str, list[Cue]], list, str | None]:
    """Adds what the pasted text holds to `have` (earlier pastes for this part). Returns
    (languages so far, new words, None when all `codes` are there and valid, else a message)."""
    have = dict(have or {})
    by_lang, words = parse_answer(text)
    if not by_lang:
        # A bare SRT (copied from one of several code blocks, without its label): take it as the first
        # language still missing, or Swedish when it is clearly Swedish (ä/ö). The status shows which.
        bare = parse_srt_text(text)
        missing = [c for c in codes if c not in have]
        if bare and missing:
            body = " ".join(c.text for c in bare).lower()
            swedish = body.count("ä") + body.count("ö") >= 3
            guess = "sv" if swedish and "sv" in missing else next((c for c in missing if c != "sv"), missing[0])
            by_lang = {guess: bare}
    problems = []
    for code, cues in by_lang.items():
        if code not in codes:
            continue
        msg = engine.compare(part, cues)
        if msg:
            problems.append(f"{LANG_BY_CODE[code].name}: {msg}")
        else:
            have[code] = cues
    if problems:
        return have, words, problems[0]
    missing = [LANG_BY_CODE[c].name.lower() for c in codes if c not in have]
    if not by_lang:
        return have, words, ("Der er ikke noget svar fra Claude i udklipsholderen. Klik på kopiknappen øverst på "
                             "Claudes kodeblok (eller under hele svaret) og prøv igen.")
    if missing:
        return have, words, f"Mangler stadig: {', '.join(missing)}."
    return have, words, None

# --- The work file: the whole text in one file instead of many copy-paste rounds -------------------
#
# The program writes "<video> - til Claude.txt" with a header line carrying a job id, the task and the
# subtitles. The user pastes the file into a Claude chat; the skill (Claude with code execution) writes
# "<video> - fra Claude.txt" in a fixed template, which the user downloads. The program watches the Downloads
# folder for a file whose first line has the same id, checks it and carries on.

TASK_HEADER = "VIDEOSPROG-OPGAVE"
ANSWER_HEADER = "VIDEOSPROG-SVAR"
OLD_ANSWER_HEADER = "VIDEO-UNDERTEKSTER-SVAR"   # files from before the rename


def work_file_text(src: Language, proofread: bool, targets: list[Language], cues: list[Cue], job_id: str,
                   answer_name: str, cut: bool = False) -> str:
    order = ([src.code] if proofread else []) + [t.code for t in targets]
    tasks = []
    if proofread:
        tasks.append(f"ret de {src.plural} undertekster (fejlhørte ord, især navne og fagord)")
    if targets:
        tasks.append("oversæt dem til " + " og ".join(t.name.lower() for t in targets))
    if cut:
        tasks.append(CUT_TASK)
    words = known_words()
    rules = word_rules()
    lines = [
        f"{TASK_HEADER} id={job_id} sprog={','.join(order)} blokke={len(cues)}" + (" klip=ja" if cut else ""),
        "",
        f"Til Claude: brug skill'en {SKILL_NAME}. Opgave: " + ", og ".join(tasks) + ".",
        f"Lav svarfilen \"{answer_name}\" som en fil, brugeren kan hente, efter skabelonen i skill'en:",
        f"første linje \"{ANSWER_HEADER} id={job_id} skill=<versionsnummeret fra skill'en>\", derefter " + ", ".join(f"=== {c} ===" for c in order)
        + " med alle " + str(len(cues)) + " blokke hver, "
        + ("så === klip === med dine klipforslag, " if cut else "")
        + "så === ordliste === og til sidst === slut ===.",
        "Behold nummerering og tidskoder præcis. Skriv ikke underteksterne i chatten.",
    ]
    if cut:
        lines += [""] + CUT_RULES
    if words:
        lines += ["", "Ordliste (kendte ord): " + ", ".join(words)]
    if rules:
        lines += ["Typiske fejlhøringer: " + "; ".join(f"{w} => {r}" for w, r in rules[:80])]
    lines += ["", f"=== {src.code} ===", format_srt(cues).rstrip(), ""]
    return "\n".join(lines) + "\n"


def answer_skill(text: str) -> int | None:
    """The skill version Claude wrote in the answer file's first line ("skill=3"), or None (no skill, or an old one)."""
    first = text.lstrip("\ufeff").split("\n", 1)[0]
    m = re.search(r"\bskill=(\d+)", first)
    return int(m.group(1)) if m else None


def answer_id(text: str) -> str | None:
    """The job id from an answer file's first line, or None if it is not an answer file."""
    first = text.lstrip("﻿").split("\n", 1)[0].strip()
    m = re.match(rf"(?:{ANSWER_HEADER}|{OLD_ANSWER_HEADER})\s+id=(\S+)", first)
    return m.group(1) if m else None


def check_file_answer(cues: list[Cue], codes: list[str], text: str, cut: bool = False
                      ) -> tuple[dict[str, list[Cue]], list, str | None]:
    """Check a whole answer file: every language complete, the cut section when asked for, and the end marker."""
    have, words, msg = check_answer(cues, codes, text)
    if msg is None and cut and answer_cuts(text) is None:
        msg = "Svarfilen mangler afsnittet === klip === med klipforslag (det må gerne være tomt)."
    if msg is None and not re.search(r"^={2,}\s*slut\s*={2,}\s*$", text, re.M | re.I):
        msg = "Svarfilen slutter ikke med === slut ===, så den er nok ikke blevet skrevet færdig."
    return have, words, msg


def followup_text(answer_name: str, msg: str, cut: bool = False) -> str:
    """A message the user can paste to Claude when the answer file was incomplete."""
    return (f"Svarfilen var ikke i orden: {msg}\n"
            f"Lav venligst \"{answer_name}\" igen, komplet efter skabelonen i skill'en {SKILL_NAME} "
            f"(alle sprog, alle blokke, {'=== klip ===, ' if cut else ''}=== slut === til sidst).")

# --- The skill ------------------------------------------------------------------------------------

SKILL_MD = """---
name: videosprog
description: Retter og oversætter undertekster fra programmet "Videosprog" (tidligere "Video undertekster") mellem dansk, svensk, norsk og engelsk, foreslår klip i optagelser af Teams-møder og webinarer (ventetid, pauser, upassende eller uklare udsagn) og lærer EG-fagord som Membercare. Brug den, når brugeren vil have undertekster eller dub af en video eller vil have en optagelse klippet (fx "lav denne video på svensk med norske undertekster", "klip ventetiden væk"), når værktøjerne fra Videosprog (start_video, vent, hent_tekst, gem_tekst) er tilgængelige, når brugeren sender en fil "... - til Claude.txt" (første linje VIDEOSPROG-OPGAVE), eller indsætter undertekster fra Videosprog.
---

# Video-oversættelse

**Skill-version: 3.** Programmet spørger efter dette nummer for at se, om brugeren har den nyeste skill: skriv
det i svarfilens første linje (`skill=3`) og send det med i `hent_tekst` (`skill: 3`).

Brugeren laver undertekster og dub af videoer i programmet "Videosprog" (tidligere "Video undertekster"). Programmet
skriver teksten med talegenkendelse, og du retter og oversætter den. Programmet læser dit svar
maskinelt, så formatet skal følges præcist.

## Opgaven
Gør det, opgaven siger (sprogkoder: da, sv, no, en):
- **Ret** teksten på originalsproget: fejlhørte ord, især navne, produkter og skærmbilleder (se
  ordlisten nedenfor og ordlisten i opgaven). Ret ikke talesprog til skriftsprog, og ændr ikke indholdet.
- **Oversæt** til de sprog, opgaven nævner. Naturligt talesprog, som en svensker/nordmand ville sige det.
  Produktnavne og navne på skærmbilleder oversættes ikke.
- Samme nummerering og præcis samme tidskoder som i originalen. Samme antal blokke; slå aldrig blokke
  sammen og del dem ikke, heller ikke midt i en sætning. Højst 2 linjer pr. blok, ca. 42 tegn pr. linje.
- **Foreslå klip**, når opgaven beder om det (`klip=ja`, eller "foreslå, hvad der skal klippes væk"). Videoen er
  typisk en optagelse af et Teams-møde eller webinar. Foreslå hele blokke, én linje pr. klip:
  `<første blok>-<sidste blok> <kategori> <kort begrundelse>`, fx `1-14 start Folk kommer ind og tester lyd`.
  Kategorier:
  - `start`: før mødet reelt går i gang (folk kommer ind, lydtest, "kan I høre mig?", "vi venter lige på et par stykker")
  - `pause`: afbrydelser og ventetid undervejs ("to sekunder, min skærm frøs")
  - `slut`: efter mødet er slut ("tak for i dag" og alt derefter, "er optagelsen slukket?")
  - `upassende`: noget, der ikke skal gemmes for altid: private forhold, kunder eller kolleger omtalt i en
    sårbar sammenhæng, grove udtryk
  - `offtopic`: fjol og snak, der ikke handler om emnet
  - `uklart`: noget, der ikke kan forstås, heller ikke i sammenhængen
  Vær forsigtig: klip kun det, der tydeligt ikke hører til, og aldrig midt i en forklaring, der fortsætter.
  Lange stille pauser finder programmet selv. Brugeren ser alle klip på en tidslinje og godkender dem, før der
  klippes. Tidskoderne beholdes uændret; programmet flytter dem selv efter klipningen.

## Hvis du har værktøjerne fra Videosprog (det bedste)
Har du værktøjerne `start_video`, `vent`, `status`, `hent_tekst` og `gem_tekst`, så klar det hele i chatten:
1. Brugeren skriver fx "lav denne video på svensk med norske undertekster: C:\\...\\video.mp4". Kald
   `start_video` (video, tale, undertekster, evt. talesprog). Kender du ikke stien, så brug `find_videoer`
   eller spørg. Vil brugeren have videoen klippet først (fx "klip ventetiden og pauserne væk", "fjern det,
   der ikke skal med"), så sæt `klip` til true. Kun klip, uden nye sprog: `tale` original og `undertekster` ingen.
2. Kald `vent` igen og igen. Skriv en kort linje til brugeren ved hver 10 % (fx "40 % - lytter videoen
   igennem"). Programmet kan være en time om at lytte; bliv ved, og er du blevet afbrudt, så samler `status`
   op igen.
3. Når jobbet er i `claude_turn`: hent teksten med `hent_tekst` i bidder (ca. 120 blokke, og altid med
   `skill: 3`), ret og oversæt
   efter reglerne ovenfor, og aflever hvert sprog med `gem_tekst` (samme numre og tidskoder). Svaret siger,
   hvad der mangler eller er forkert; ret det. Rettede fejlhørte ord sendes med i `ordliste`
   (`forkert => rigtigt`). Skal videoen klippes, så læs hele teksten og aflever dine klipforslag med
   `foreslaa_klip` (bloknumre som ovenfor; `faerdig` true til sidst, også hvis du ikke har nogen forslag).
   Når klipforslagene og alle sprog er afleveret, viser programmet klippene for brugeren på en tidslinje
   (`review_cuts`). Fortæl kort, hvad du har foreslået at klippe (tid og hvorfor). Brugeren kan rette i
   programmet eller sige det til dig: "behold det ved 14:20" -> `godkend_klip` med `behold` = klippets nummer;
   "klip også 22:10-22:40" -> `godkend_klip` med `tilfoej`. `godkend_klip` uden `vent_paa_brugeren` klipper med det
   samme, så spørg brugeren, om klippene er i orden, før du godkender.
4. Når alt er afleveret, laver programmet resten. Kald `vent` til "færdig", og fortæl brugeren navnet på den
   nye video, og at den kan afspilles i programmet.

## A. Brugeren sender en fil "... - til Claude.txt"
Første linje er `VIDEOSPROG-OPGAVE id=<id> sprog=<koder> blokke=<antal>`.

1. Læs filen med kode. Underteksterne står efter linjen `=== <originalsprog> ===`.
2. Lav svarfilen med præcis det navn, opgaven angiver (`... - fra Claude.txt`), som en fil brugeren kan
   hente. Arbejd i bidder: ret og oversæt højst ca. 100 blokke ad gangen, og skriv (append) hver bid til
   filen med kode, til alle blokke er med. Skriv ikke underteksterne i chatten.
3. Skabelon for svarfilen:

```
VIDEOSPROG-SVAR id=<samme id som i opgaven> skill=3
=== da ===
1
00:00:01,000 --> 00:00:03,500
Velkommen til webinaret om Membercare.

(... alle blokke ...)
=== sv ===
1
00:00:01,000 --> 00:00:03,500
Välkommen till webbinariet om Membercare.

(... alle blokke ...)
=== klip ===
1-14 start Folk kommer ind og tester lyd
212-218 offtopic Snak om vejret
=== ordliste ===
memberkær => Membercare
=== slut ===
```

   - Én sektion pr. sprog i den rækkefølge, `sprog=` angiver. Originalsproget med (rettet), hvis det står
     i `sprog=`.
   - `=== klip ===` kun når første linje har `klip=ja`: dine klipforslag, se "Foreslå klip" ovenfor (tom, hvis
     der ikke er noget at klippe).
   - `=== ordliste ===`: de fejlhørte ord, du har rettet, én pr. linje `forkert => rigtigt`. Programmet
     husker dem og retter dem selv næste gang.
   - `=== slut ===` som sidste linje viser programmet, at filen er færdig.
4. Tjek med kode, at hver sektion har præcis `blokke=` blokke med de samme tidskoder som originalen. Ret
   det, hvis ikke.
5. Svar i chatten med én kort sætning: at filen er klar, og at brugeren skal klikke på den og hente den
   (Download). Programmet finder den selv i Overførsler-mappen.

## B. Brugeren indsætter undertekster direkte i chatten (reserve)
Beskeden har en opgave og en kodeblok med underteksterne, evt. "Del x af y".
- Svar direkte i chatten, uden filer og kode, med ALT i ÉN kodeblok: en linje `=== <kode> ===` før hvert
  sprog i den angivne rækkefølge, derefter `=== ordliste ===` med rettede ord. Ingen tekst før eller efter.

## Ordliste
Se [ordliste.md](ordliste.md). Ordene staves præcis sådan på alle sprog.
"""


def skill_zip(dest: Path) -> Path:
    """Write the skill (with the current word list) as a zip for Claude > Settings > Skills > Upload."""
    words = known_words()
    rules = word_rules()
    ordliste = ["# Ordliste", "", "Kendte ord (staves sådan på alle sprog):", ""]
    ordliste += [f"- {w}" for w in words] or ["- Membercare"]
    ordliste += ["", "Typiske fejlhøringer fra talegenkendelsen:", ""]
    ordliste += [f"- {w} => {r}" for w, r in rules]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{SKILL_NAME}/SKILL.md", SKILL_MD)
        z.writestr(f"{SKILL_NAME}/ordliste.md", "\n".join(ordliste) + "\n")
    dest.write_bytes(buf.getvalue())
    return dest
