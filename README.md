# SubaDubaLingo (tidligere Videosprog og Video undertekster)

> **2.0.0 (15.10.2026): nyt navn og ét domænesprog.** Appen hedder SubaDubaLingo (*sub*titles, *dub*, *lingo*).
> - **Engelsk domænesprog:** kode, MCP-værktøjer, protokollen til Claude (overskrifter, sektioner, klip-typer), skill
>   og prompts. Ordliste: [docs/glossary.md](docs/glossary.md).
> - **Dansk:** brugerfladen, outputfilerne ved siden af videoen og brugervejledningen.
> - **Nye navne:**
>   - Python-pakken `vu` hedder `subadubalingo`.
>   - Datamappen er `~/.subadubalingo`, med filerne `corrections.txt`, `text_state.json` og `models/`.
>   - Programmet installeres i `Programs\SubaDubaLingo\<version>`.
>   - Claude-konfigurationen og skill'en hedder `subadubalingo`; skill'en er version 4.
>   - Svarfilen begynder med `SUBADUBALINGO-ANSWER` og bruger sektionerne `=== words/cuts/end ===`.
>   - MCP-værktøjerne hedder `start_video`, `wait`, `status`, `get_text`, `save_text`, `suggest_cuts`,
>     `approve_cuts`, `cancel` og `find_videos`.
>   - Klip-typerne er `start`, `pause`, `end`, `inappropriate`, `offtopic`, `unclear` og `manual`.
> - **Overgang (testet i sandkasse med `tests/install_test.py dist-1.9.10/Videosprog dist-2.0.0/SubaDubaLingo`):**
>   - Datamappen omdøbes, og er den i brug, kopieres de små filer, og modellerne flyttes ved en senere start.
>   - Genveje og fastgjorte ikoner peges om, og Claudes `videosprog`-indgang erstattes.
>   - En kørende 1.9.10-server beder om genstart af Claude.
>   - `Programs\Videosprog` fjernes, når intet kører fra den.
>   - Gamle danske svarfiler og klip-ord læses stadig.
>   - Skill-guiden beder om at slette den gamle skill "videosprog".
>
> Afsnittene herunder er skrevet før 2.0 og bruger de gamle navne, hvor de beskriver historik.


Windows-program til ikke-tekniske brugere: undertekster (.srt og indbrændt) og dub med syntetisk stemme,
alt lokalt. Brugeren vælger kun video, talesprog, ny tale og nye undertekster; programmet genbruger eller laver selv
det, der mangler, og brugeren flytter aldrig .srt-filer.

Claude er altid i loopet ("halvt-halvt", brugeren har Claude Chat/Cowork, ikke Code): `subadubalingo/claude.py` laver en prompt
pr. del (70-100 blokke), brugeren kopierer den til Claude og svaret tilbage. Ét svar indeholder den rettede tekst på
talesproget (```srt-da), oversættelserne (```srt-sv osv.) og en ```ordliste med rettede ord. Programmet
validerer blokke og tidskoder, gemmer filerne og føjer ordene til `rettelser.txt`, så de rettes allerede i næste
transskription og sendes med i næste prompt. Skill'en `videosprog` (SKILL.md + ordliste.md) ligger som
zip i release-mappen (`tools/make_skill.py`); Indstillinger -> "Gem Claude-skill..." laver en ny med brugerens
lærte ord. Claude-tjekket tekst huskes i `claude-tjekket.json`, så den ikke sendes igen.

## Opbygning
- `app/subadubalingo/gui.py` - tkinter-GUI på dansk med Windows 11-tema (sv-ttk); to kolonner, Claude-panel til højre; arbejdet kører i en tråd, pause/fortsæt/annullér; kun ét vindue ad gangen.
- `app/subadubalingo/textstate.py` - fingeraftryk (størrelse + ændringstid) og tekstversion pr. .srt, så en ændret video eller en tekst fra en version med kendt fejl laves forfra.
- `app/subadubalingo/engine.py` - transskription, indbrænding, dub, modeller, Claude-prompt og validering.
- `app/subadubalingo/common.py` - stier, sprog, filnavne, log, procesafvikling (pause = NtSuspendProcess), SRT.
- `bin/` (ikke i git, hentes med `fetch-tools.ps1`):
  - whisper.cpp **Vulkan-build** fra SubtitleEdit/support-files (whispercpp-194). Kører også på CPU med `-ng`, og
    programmet falder selv tilbage til CPU, hvis GPU-kørslen fejler. Silero VAD følger med og bruges.
  - Piper 2023.11.14-2 (`--json-input`, én kørsel for alle blokke; for lange blokke indtales igen med
    `--length_scale` i trin af 0,1 op til 1,5x).
  - ffmpeg 9.0.2 essentials fra gyan.dev (libass, libx264, Quick Sync). Indbrænding prøver `h264_qsv` først.
- Data (indstillinger, rettelser, tekststatus, jobs, log) ligger i `%USERPROFILE%\.subadubalingo` (skjult), ikke i
  AppData: Claude desktop er MSIX, og alt hvad dens børneprocesser (MCP-serveren og appen startet af den) skriver
  under AppData, havner i Claudes pakkemappe. Modeller hentes til `...\.subadubalingo\models`; `models\` ved
  exe'en og modelmapperne under de tidligere navne bruges også, indtil de er flyttet.

## Claude-chat via MCP (v1.7)
`SubaDubaLingo.exe --mcp` er en MCP-server (JSON-RPC over stdio, `app/subadubalingo/mcp_server.py`, ingen ekstra pakker).
Indstillinger -> "Forbind med Claude" skriver den ind i `%APPDATA%\Claude\claude_desktop_config.json` (backup ved
siden af). Værktøjer: `start_video` (starter appen, hvis den ikke kører), `wait` (lang-poll: svarer ved hver 10 %,
Claudes tur, pause, fejl, færdig, ellers efter 45 s), `status`, `get_text`, `save_text` (validerer tidskoder med
det samme), `find_videos`, `suggest_cuts`, `approve_cuts` og `cancel` (hed `vent`, `hent_tekst`, `gem_tekst`,
`find_videoer`, `foreslaa_klip`, `godkend_klip` og `annuller` før 2.0). Appen og serveren deler jobs via filer (`app/subadubalingo/jobs.py`); kun appen skriver status.
Claude-kortet viser "Claude arbejder" med en bjælke pr. sprog. Virker i Claude Chat (lokale MCP-servere er tilladt i
EG-org); Cowork er ikke testet. Test: `tests/mcp_test.py` spiller Claude.
- **`annuller` (v1.9.9):** stopper et job (standard: det, appen arbejder på). MCP-serveren skriver
  `jobs/<id>/cancel.json`, og appen stopper uden at spørge. Kører appen ikke, sættes et hængende job direkte til
  `cancelled`. Ellers kunne et job, der hang i `claude_turn`, blokere alle nye jobs ("busy"), indtil brugeren lukkede appen.
- **Opdatering mens Claude kører (v1.9.9):** Windows tillader at omdøbe installationsmappen, mens MCP-serveren kører
  fra den. Det gjorde installationen tidligere, og den gamle server blev ved med at køre. PyInstaller åbner exe-filen
  igen ved hver første import, så serveren læste den nye exe ved de gamle offsets: `zlib.error: Error -3 ...
  incorrect header check` i `hent_tekst` (1.9.3-server, 1.9.8 installeret 14.10.2026). Rettet tre steder:
  1. `installer.running_from()` nægter at installere, mens en proces kører fra mappen, og beder om Quit af Claude.
  2. Serveren tjekker exe'ens størrelse og tid ved hvert kald og beder ellers om en genstart af Claude.
  3. `claude`-importen sker ved opstart.

  Test: `tests/update_test.py OLD NEW JOB` bytter mappen ud under en kørende server
  (`dist-1.9.4` -> `dist-1.9.8` giver zlib-fejlen, `dist-1.9.9` -> hvad som helst giver genstart-beskeden).
  Mellem to næsten ens builds sker fejlen ikke, fordi modulerne ligger samme sted. `tests/cancel_test.py`
  dækker `annuller`.
- **Én mappe pr. version (v1.9.10):** `Programs\Videosprog\<version>\Videosprog.exe` og `current.txt`. En
  opdatering rører aldrig en mappe, noget kører fra, så den virker, mens Claude kører den gamle MCP-server. Den
  gamle server fortsætter og beder om genstart, når `current.txt` ikke længere er dens egen mappe.
  - Genveje i Start og på skrivebordet, fastgjorte ikoner på proceslinjen og Claudes config peges om ved hver
    installation.
  - Ved hver start (og efter installation) slettes mapper, ingen proces kører fra (`installer.cleanup()`). Den
    nuværende og den forrige version bliver, og det samme gælder den flade 1.9.9-installation, indtil dens server er
    stoppet.
  - Kun samme version, der kører, giver "i brug". Er appens vindue åbent, beder installationen om at lukke det
    først, ellers ville den nye bare vise den gamle (ét vindue).
  - `Videosprog.exe --install [--connect]` installerer uden brugerflade (tests og den kommende selvopdatering;
    exit 0/2/1).

  Test i en sandkasse (midlertidig LOCALAPPDATA/APPDATA/USERPROFILE):
  `tests/install_test.py dist-1.9.9/Videosprog dist-1.9.10/Videosprog`.

## Klip før undertekster og dub (v1.9)
"Klip videoen først" (og `start_video(klip=true)`) tilføjer trinene Claude foreslår klip -> brugeren gennemser dem
-> klipper videoen, før dub/indbrænding. `app/subadubalingo/cutting.py`:
- **Forslag:** lange stille stykker (mindst 20 s mellem blokke og mest stille ifølge lydkurven, 250 ms opløsning)
  og Claudes linjer `<blok>-<blok> <kategori> <grund>` (start, pause, slut, upassende, offtopic, uklart; også
  `mm:ss-mm:ss`). Claude klipper i hele blokke, fordi den kun ser teksten. Svaret får et afsnit `=== klip ===`;
  via MCP bruges `foreslaa_klip` (og `godkend_klip` under gennemsynet).
- **Gennemsyn:** `app/subadubalingo/review.py`, en tidslinje i venstre kolonne med lydkurve, taleblokke og farvede klip.
  Man kan trække i kanterne (med snap til blokgrænser), trække nye klip, dobbeltklikke for at beholde et klip og
  zoome med musehjulet. Til hvert klip vises billeder før og efter, og "Se overgangen" laver et klip på 2x4 s til
  standardafspilleren. Ved MCP-jobs spejles listen til `jobs/<id>/cuts.json`, og `cut_decision.json` kommer fra Claude.
- **Klip:** ét ffmpeg-pass (`select` + `setpts` minus summen af tidligere klip, så variabel framerate bevares;
  lyden i små frames med 25 ms fade ved hver samling). Grafen ligger i en fil (`-/filter_complex`), fordi
  mange klip ellers sprænger Windows' grænse for kommandolinjens længde. Prøver QSV først, ellers x264.
  Målt på webinaret (31 min) 12.10.2026: 221 s med QSV, længden præcis, lyden synkron inden for 10 ms.
- **Tidskoder:** srt'erne (originalsproget + oversættelser, der passer til dets blokke) flyttes til
  `<navn> - klippet - ... undertekster.srt`. En blok, der ligger mere end halvt inde i et klip, fjernes.
  `textstate.record_cut` husker, at den klippede video er lavet af netop denne original, så den genbruges til
  flere sprog ("Klip videoen forfra" laver den igen).

## Claude-skill'ens version (v1.9.3)
`claude.SKILL_VERSION` er den skill-version, appen leveres med (tælles kun op, når SKILL_MD ændres). `settings.skill_installed` er den version, brugeren har bekræftet at have lagt i Claude.
- **Første gang** (`skill_installed` = 0, v1.9.8): guiden hedder "Læg Claude-skill'en ind" i stedet for "Opdatér". Trinnet "Slet den gamle \"videosprog\", hvis den er der" er med, fordi 0 betyder ukendt, ikke fraværende (lagt ind via Indstillinger, "Senere" eller før versionen blev husket).
- **Ved opdatering:** er `skill_installed` lavere, viser appen ved første start (ikke når Claude starter den) guiden `SkillGuide`: Gem skill-filen (til Overførsler, med brugerens lærte ord), Customize -> Skills, slet den gamle, upload. Den forsvinder med "Jeg har gjort det".
- **Failsafe:** skill'en skriver `skill=N` i svarfilens første linje og sender `skill: N` med i `hent_tekst`. Mangler nummeret, eller er det for lavt, viser appen guiden efter kørslen, og MCP-serveren beder Claude sige det til brugeren. Et gyldigt nummer opdaterer `skill_installed` af sig selv.

## Byg
```
python -m venv .venv; .venv\Scripts\pip install pyinstaller numpy
.\fetch-tools.ps1
.\build.ps1        # -> dist\SubaDubaLingo\ og dist\SubaDubaLingo <version>.zip
```
Tests: `tests/smoke.py` (motoren uden GUI), `tests/gui_flow.py` (App-objektet med alle fire valg),
`tests/pause_test.py` (pause fryser whisper, luk under pause dræber den).

## Målinger (Kaspers i7-13850HX, RTX 2000 Ada, Intel UHD; 12.10.2026)
Klip 5:00-6:00 af superbrugerwebinaret (60 s lyd):

| Model / enhed | Tid |
|---|---|
| large-v3 q5_0, CPU 8 tråde / 20 tråde | 55 s / 43 s |
| large-v3-turbo q5_0, CPU 8 tråde / 20 tråde | 37 s / 26 s |
| turbo, Vulkan på Intel UHD (32 EU) | 42 s |
| turbo, Vulkan på RTX 2000 | 3 s |

- På klippet 5:00 var turbo bedst ("ERN-felter" mod "ER-indfælde"), men på tre andre minutter (0:00, 15:00, 25:00)
  var large-v3 klart bedre ("Superbrugere webinar" mod "Superbro Prebinar", "få den printet" mod "gå den krændor").
  **Large-v3 er standard fra v1.1.0**; turbo kan vælges som "Hurtigst". Hele webinaret med large-v3 på RTX 2000: 2,7 min.
- Alle modeller fejlhører fagord ("fløv" for flow, "memberkær" for Membercare) og hallucinerer tv-tekstkrediteringer
  ("Danske tekster af ..."). Derfor filtreres kendte hallucinationer, og `rettelser.txt` (Indstillinger ->
  "Rediger rettelser") retter ord bagefter. Whisper får ikke en ordliste som prompt; det gav dårligere resultater i
  diktafon-pipelinen i Personal-Assistant.
- Hele webinaret (31 min): turbo på RTX 2000 1,1 min; på CPU med 8 tråde ca. 0,45x realtid (ca. 70 min).
  Beam search vs. greedy gjorde ingen forskel på CPU (encoderen dominerer).
- Indbrænding af hele webinaret med Quick Sync: 3,5 min. Dub med 397 blokke inkl. indbrænding: 5,7 min.
- En typisk kontor-bærbar (i5-1345U, Iris Xe 80 EU, 16 GB) er ikke målt. Programmet logger tid og enhed
  for hver transskription ("transcribed ... device=...").

## Kendte forbehold
- **App Control for Business (WDAC) er slået til på EG-maskiner.** Programmet og værktøjerne er usignerede;
  om de må køre, afhænger af politikken (Admin by Request hjælper ikke mod WDAC). Systemtjekket ved opstart
  melder det, hvis et værktøj blokeres.
- Test fra en Claude desktop-session: alt, hvad sessionen skriver under `%LOCALAPPDATA%`, havner i
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\` (MSIX), så modeller, log og indstillinger
  derfra ses ikke af en exe, man selv starter. Læg testmodeller i `dist\SubaDubaLingo\models\`
  (forsvinder ved næste build) eller `app\models\` (kørsel fra kilde).
- Kørsel fra kilde (også testene) bruger den rigtige profil: første import af `subadubalingo.common` laver
  `~/.subadubalingo` og kopierer indstillinger og ordliste fra `~/.videosprog` (sket på Kaspers pc 15.10.2026, hvor
  omdøbningen fejlede, fordi Claudes gamle MCP-servere holdt loggen åben; `~/.videosprog` er urørt).
- Ingen drag-and-drop (tkinter kræver tkdnd); brug "Vælg video...".
- Brugerflade (v1.9.8, gennemgang 14.10.2026): alle dialoger åbner
  midt over appen (`place_over`, inden for skærmens arbejdsområde, `work_area`); beskedbokse har altid et
  parent-vindue og flyttes midt over det (`boxes`/`_box_over`: en WH_CBT-hook, for Windows centrerer ellers
  MessageBox på skærmen; hooken må ikke kalde Tk); opmærksomhed blinker proceslinjen uden at tage fokus. Hovedvinduet holdes inden for
  arbejdsområdet (`initial_geometry`) og starter maksimeret, når skærmen er under 760 logiske px høj (fx
  1920x1200 ved 150 %). Et job fra Claude venter i indbakken, mens en dialog er åben. Pause er slået fra under
  brugerens ture; Annullér spørger først. Claude-kortet er 1/4 bredde, mens det venter, og 2/5 på brugerens tur;
  færdig-visningen får hele bredden. I klipgennemgangen får tidslinjen den ekstra højde, og klip 1 er valgt.
  Trin 2 i Claude-kortet bliver først grønt, når en svarfil dukker op. Anden start finder vinduet på
  klassen `TkTopLevel` (installationsvinduet har samme klasse og titel, men lever kun før appen starter).
- Én stemme pr. person (v1.9.7, standard, `subadubalingo/speakers.py`): stemmeaftryk pr. sætning (sherpa-onnx, WeSpeaker ResNet34, 26 MB, hentes som de andre modeller; kun sætninger >= 3 s), average-linkage-klynger (cosinus 0,66), og to klynger med centroid >= 0,75 og under 8 Hz forskel i stemmeleje slås sammen (præsentatoren kom ellers ud som to personer). Køn efter stemmeleje; stemmerne deles ud efter taletid: sprogets egne stemmer først, derefter de samme flyttet ±2,5/4,5 halvtone (ffmpeg asetrate+atempo). Webinaret: Jesper nst, værten nst -2,5, Peter nst +2,5, Helle lisa, Marianne alma. Indstillinger: én stemme pr. person / mand-kvinde / samme stemme.
- Mand/kvinde i dubben (v1.9.6, `engine.GENDER_VOICES`, `subadubalingo/pitch.py`): stemmelejet i den originale tale måles pr. sætning (autokorrelation, numpy). Under 160 Hz = mand, over 175 Hz = kvinde, ellers samme som sætningen før; enkeltstående skift glattes ud. Svensk nst (~80 Hz) og lisa (~186 Hz), engelsk ryan og lessac. Dansk og norsk har kun én stemme i Piper 1.2 (no_NO-nvcc kræver en nyere Piper). Webinaret: 230 sætninger mand, 21 kvinde (Marianne og Helle ramt rigtigt; mindst én tvivlsom ved 12:46). Kan slås fra i Indstillinger.
- Dubben indtales i hele sætninger (v1.9.5, `engine.sentences`): blokke samles til . ? ! eller en pause > 0,7 s i originalen, og hver sætning indtales igen i det tempo, der gør den lige så lang som originalen (0,87x-1,5x, Pipers length_scale i trin af 0,02). Før kom stemmen før tid og ventede midt i sætningen; svensk dub af webinaret, første 10 min: 16 af 75 sætninger havde en pause > 0,6 s midt i, nu 0. Sætninger, der ikke kan nå det selv ved 1,5x, skubber de næste lidt (17 af 251 på webinaret startede mere end 0,5 s for sent).
