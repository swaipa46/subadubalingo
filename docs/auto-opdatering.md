# Selvopdatering: hvad der skal til

Mål: Videosprog finder selv en ny version, henter den og installerer den med ét klik fra brugeren. Det skal ske
uden at gå i stykker, mens Claude kører MCP-serveren (se zlib-fejlen 15.10.2026 i README).

## 1. Hvor ligger udgivelserne?

**Besluttet 15.10.2026:** som GitHub Releases i det offentlige repo `swaipa46/videosprog`, hvor koden også
ligger. Det kræver ingen login, ingen SharePoint og ingen opsætning hos brugeren. Manifestet signeres med ed25519
(`app/vu/ed25519.py`, testet mod RFC 8032). Overvejelserne herunder er bevaret.

Appen skal kunne læse et lille manifest og zip-filen uden at brugeren logger ind. Det er den eneste del, der ikke
kan løses i koden.

| Mulighed | For | Imod |
|---|---|---|
| **Offentligt GitHub-repo kun til udgivelser** (fx `KasperAtEG/videosprog-releases`, kun Releases, ingen kode) | Gratis, stabile URL'er, `releases/latest`-API, ingen login | Zip'en (og dermed koden i exe'en) er offentlig. Kræver et ja fra EG. |
| SharePoint/Teams-mappe, som brugeren synkroniserer med OneDrive | Bliver i EG, ingen godkendelse. Appen læser bare en lokal mappe. | Brugeren skal synkronisere mappen én gang. Det virker ikke, hvis synkroniseringen er slået fra. |
| Azure Storage (statisk blob) | Bliver i EG's Azure, enkle URL'er | Kræver et abonnement og at nogen ejer det |
| Det private repo `KasperAtEG/videosprog` | Allerede der | Brugeren har ikke adgang, og et token i appen er udelukket |

Anbefaling: SharePoint-mappen, hvis det kun er brugeren og en håndfuld kolleger, og et offentligt repo kun til
udgivelser, hvis det skal ud til de ca. 40 i BU'en, og EG siger god for det. Koden nedenfor er ens for begge:
kun "hent manifest" og "hent zip" er forskellige (URL eller filsti).

## 2. Manifest

`latest.json` ved siden af zip'en:

```json
{"version": "1.10.0", "zip": "Videosprog 1.10.0.zip", "sha256": "...", "size": 134217728,
 "notes": "Kort på dansk til brugeren", "skill": 3, "signature": "<ed25519 over de andre felter>"}
```

- `sha256` fanger en afbrudt eller ødelagt download.
- `signature`: SHA-256 fra samme sted beviser ikke, hvem der har lavet filen. Den offentlige nøgle ligger i
  appen, og den private hos Kasper, som signerer i `publish.ps1`. Python har ikke ed25519 i standardbiblioteket.
  Valg: et lille ren-Python ed25519-modul (ca. 100 linjer, kun til verifikation), eller Authenticode-signering
  af exe'en med et EG-kodecertifikat. Authenticode hjælper også med WDAC og SmartScreen, så det er bedst, hvis
  EG har et.

## 3. Installationsform: én mappe pr. version

Fejlen 15.10 opstod, fordi en opdatering byttede mappen ud under en kørende proces. Med automatiske
opdateringer sker det oftere, fordi Claude næsten altid kører. Løsningen er aldrig at røre en mappe, der er i brug:

```
%LOCALAPPDATA%\Programs\Videosprog\
    1.9.9\Videosprog.exe      <- Claude kører måske stadig MCP-serveren herfra
    1.10.0\Videosprog.exe     <- ny
    current.txt               "1.10.0"
```

- Opdatering: pak ud i `1.10.0.new`, tjek at `base_library.zip` og exe'en er der, og omdøb til `1.10.0`.
  Skriv derefter `current.txt`, og peg genvejene (Start og skrivebord) og `claude_desktop_config.json` på den
  nye exe. Den kode findes allerede (`_shortcut`, `_point_claude_at`).
- Den gamle MCP-server kører videre uforstyrret fra `1.9.9\`. Den nye exe-tjek (`_outdated`) skal så kigge på
  `current.txt` i stedet for exe'ens tidsstempel, og beder Claude om en genstart. Først ved næste start af
  Claude bruges den nye.
- Oprydning: ved hver start slettes versionsmapper, som ingen proces kører fra (`installer.running_from`).
  Den nuværende og den forrige bliver altid, så man kan gå tilbage.
- Overgang: den første opdatering flytter den gamle flade installation ind i en versionsmappe. Det sker kun,
  når intet kører fra den. Ellers venter den til næste gang.

## 4. Flow i appen

1. Ved start (og højst én gang i døgnet), i en baggrundstråd: hent manifestet med 5 s timeout. Offline eller
   fejl: stille. Ingen besked, kun en linje i loggen.
2. Er der en nyere version, hentes zip'en i baggrunden til `~/.videosprog/updates/` og tjekkes (størrelse,
   SHA-256, signatur). Ikke mens et job kører, for det skal ikke tage båndbredde fra en dub.
3. Når appen er ledig (intet job, ingen dialog), vises et lille banner i bunden: "Version 1.10.0 er klar:
   <noter>. [Opdatér nu] [Senere]". Det er ikke en popup, for brugeren skal ikke afbrydes.
4. "Opdatér nu": pak ud som i afsnit 3, start den nye exe med `--updated-from 1.9.9`, og luk den gamle. Den nye
   viser "Opdateret til 1.10.0" og, hvis `skill` er steget, den eksisterende SkillGuide.
5. Startes appen af Claude (`--from-claude`), spørges der ikke midt i et job. Banneret venter til bagefter.
6. Indstilling: "Søg efter opdateringer" (standard til) og "Søg nu"-knap under Indstillinger.

## 5. Udgivelse (Kasper)

`publish.ps1 -Version 1.10.0`: kør `build.ps1`, beregn SHA-256, signér manifestet, upload zip og
`latest.json` (gh release create, eller kopi til SharePoint-mappen), og tag commit'en `v1.10.0`.

## 6. Overvejet og fravalgt

- **Velopack** (Squirrels efterfølger, har Python-binding): henter, laver delta-pakker og genstarter. Fravalgt
  indtil videre: den installerer i sin egen `current\`-mappe og løser ikke problemet med Claudes
  MCP-server, der låser mappen. Den skal også bygges ind i build og install. Værd at se på igen, hvis zip'en på
  128 MB bliver et problem (delta-opdateringer).
- **Opdatering i stilhed uden at spørge:** nej. Brugeren skal vide, hvorfor programmet ser anderledes ud, og et job
  må aldrig blive afbrudt.

## Rækkefølge

1. Kasper vælger, hvor udgivelserne skal ligge, og om der skal signeres med ed25519 eller Authenticode.
2. ~~Versionsmapper og `current.txt` i installer.py, inkl. overgang og oprydning.~~ Færdig i v1.9.10
   (`tests/install_test.py`). `Videosprog.exe --install` er klar til at blive kaldt af opdateringen.
3. Manifest, download og verifikation (`vu/update.py`), og test mod en lokal mappe.
4. Banner og indstillinger i GUI'en.
5. `publish.ps1`.
