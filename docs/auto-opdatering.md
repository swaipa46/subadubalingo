# Selvopdatering

SubaDubaLingo finder selv en ny version, henter den og installerer den med ét klik fra brugeren. Det sker uden at gå
i stykker, mens Claude kører MCP-serveren (se zlib-fejlen 15.10.2026 i README).

Bygget 16.10.2026 (branch `feature/autoupdate`). Koden: `app/subadubalingo/update.py` (net, tjek, udpakning, skift),
`app/subadubalingo/autoupdate.py` (hvornår, og båndet i vinduet), `installer.place`/`installer.activate`,
`tools/publish.ps1`.

## 1. Hvor ligger udgivelserne?

**Besluttet 15.10.2026:** som GitHub Releases i det offentlige repo `swaipa46/subadubalingo`, hvor koden også
ligger. Det kræver ingen login, ingen SharePoint og ingen opsætning hos brugeren.

- Manifestet hentes fra `https://github.com/swaipa46/subadubalingo/releases/latest/download/latest.json`
  (`update.MANIFEST_URL`), og zip'en ved siden af (samme mappe, navnet fra manifestet).
- Zip'en lægges op som `SubaDubaLingo-<version>.zip` uden mellemrum, for GitHub laver mellemrum i filnavne om til
  punktummer.

Fravalgt: SharePoint/OneDrive-mappe (brugeren skal synkronisere), Azure Storage (kræver et abonnement og en ejer), og
et privat repo (et token i appen er udelukket).

## 2. Manifest og signatur

`latest.json` ved siden af zip'en:

```json
{"version": "2.1.0", "zip": "SubaDubaLingo-2.1.0.zip", "size": 134217728, "sha256": "...",
 "notes": "Kort på dansk til brugeren", "skill": 5, "signature": "<ed25519 over de andre felter>"}
```

- `signature` er Ed25519 over alle de andre felter, sorteret og kompakt (`update.manifest_bytes`).
  - Den offentlige nøgle står i appen (`update.PUBLIC_KEY`), og den private ligger hos Kasper, DPAPI-beskyttet i
    `%USERPROFILE%\.subadubalingo-release`.
  - Den ren-Python Ed25519 (`ed25519.py`) er testet mod RFC 8032.
  - En udgivelse, som ikke er signeret med Kaspers nøgle, installeres aldrig, heller ikke hvis GitHub-kontoen
    overtages.
- `size` og `sha256` fanger en afbrudt eller ødelagt download og en zip, der er byttet ud på serveren. De er
  dækket af signaturen.
- `notes` vises som tooltip på båndet. `skill` er til orientering, for den nye version viser selv SkillGuide, hvis
  dens skill er nyere end den, brugeren har lagt ind.

## 3. Installationsform: én mappe pr. version

Opdateringen rører aldrig en mappe, der er i brug (v1.9.10):

```
%LOCALAPPDATA%\Programs\SubaDubaLingo\
    2.0.0\SubaDubaLingo.exe     <- Claude kører måske stadig MCP-serveren herfra
    2.1.0\SubaDubaLingo.exe     <- ny, pakket ud af opdateringen
    current.txt                 "2.0.0", indtil brugeren trykker Genstart nu
```

- **Pakket ud, ikke skiftet:** `update.stage` pakker zip'en ud i `~/.subadubalingo/updates/` og kopierer den
  ind i sin egen versionsmappe med `installer.place`. Det er den samme kode, som installation fra zip bruger: den
  kopierer til `<version>.new`, tjekker at exe'en og `_internal\base_library.zip` er der, og omdøber til
  `<version>`.
  - Navne i zip'en, der peger uden for mappen (`..`, absolutte stier), afvises.
  - Både `/` og `\` virker som skilletegn, for Compress-Archive i Windows PowerShell skriver `\`.
- **Skift:** `installer.activate` skriver `current.txt` og peger genvejene (Start, skrivebord og fastgjorte ikoner
  på proceslinjen) og Claudes MCP-indgang på den nye exe. Indgangen tilføjes ikke, hvis brugeren ikke havde den.
- **Claude kører videre:** en MCP-server, som Claude stadig kører fra den gamle mappe, ser at `current.txt` ikke
  længere er dens egen version. Den beder Claude om at bede brugeren genstarte Claude (`mcp_server._outdated`).
- **Oprydning:** ved hver start fjerner `installer.cleanup()` mapper, ingen proces kører fra. Disse bliver altid:
  - den nuværende og den forrige version, så man kan gå tilbage;
  - den nyeste version over den nuværende, dvs. en opdatering, der er pakket ud og venter på genstart.

## 4. Flow i appen

1. **Hvornår:** ved start, kun i den installerede kopi, højst én gang i døgnet (`Settings.update_checked`), og kun
   når "Søg efter opdateringer" er slået til (`Settings.check_updates`, standard til, i Indstillinger nederst).
2. **Tjek:** en baggrundstråd henter manifestet med 5 s timeout. Er brugeren offline, eller er der en netværksfejl,
   sker der intet: kun en linje i loggen, og der prøves igen ved næste start. En udgivelse, der ikke består et tjek
   (signatur, felter, størrelse, SHA-256, zip), afvises med en advarsel i loggen og prøves igen næste dag.
3. **Download:** er versionen nyere, hentes zip'en til `~/.subadubalingo/updates/`. Det sker ikke, mens en video
   bliver lavet, så downloaden ikke tager båndbredde fra en dub. Derefter pakkes den ud som i afsnit 3.
4. **Båndet:** under headeren står "NY VERSION 2.1.0 er klar · Genstart for at opdatere. Dine indstillinger
   bevares." med knapperne Genstart nu, Senere og ×. Det er ikke en popup, så brugeren ikke bliver afbrudt.
   - Udseende: mockup `design/mockups/*/update-banner.png` og specifikationen i `assets/ui/README.md`.
   - Rammen er `frames/update-banner`. Mangler den i temaet, bruges "din tur"-rammen (`frames/callout`).
   - Senere og × skjuler båndet indtil næste start.
   - Båndet kommer igen efter skift af tema eller sprog.
   - En opdatering, der er pakket ud men ikke skiftet til, huskes (`updates/ready.json`). Den vises igen ved næste
     start uden ny download.
5. **Genstart nu:**
   - Knappen er grå, mens en video bliver lavet (tooltip "Når videoen er færdig").
   - Ellers skifter den som i afsnit 3, slipper programmets enkelt-instans-mutex og starter den nye exe med
     `--updated-from <gammel version>`, og så lukker den gamle.
   - Den nye viser én gang "LEVEL UP Version 2.1.0 · Opdateret fra 2.0.0. Dine indstillinger er bevaret." og
     SkillGuide, hvis dens skill er nyere.
6. **Startet af Claude** (`--from-claude`): et job afbrydes aldrig. Genstart nu er grå under kørslen, og en download
   venter, til den er færdig.

Til test kan miljøvariablen `SUBADUBALINGO_UPDATE_URL` pege på et andet manifest. Signaturen tjekkes stadig mod
`PUBLIC_KEY`, så det åbner ikke for andre udgivelser.

## 5. Udgivelse (Kasper)

```powershell
tools\publish.ps1 -Notes "Kort på dansk til brugeren"
```

1. Den kører `build.ps1` (til `dist-release\`), med versionen fra `common.APP_VERSION`.
2. Den kopierer zip'en til `dist-release\release-<version>\SubaDubaLingo-<version>.zip`.
3. Den skriver `latest.json` med version, zip-navn, størrelse, SHA-256, noter og skill-version.
4. Den signerer med `tools\release_key.py sign` og tjekker signaturen med appens egen `update.manifest_ok`.
5. Den skriver den `gh release create v<version> ...`-kommando, der skal køres. Scriptet uploader ikke selv noget.

Flag:

- `-SkipBuild` genbruger en zip, der allerede er bygget.
- `-Zip <sti>` bruger en zip fra release-build-workflowet (version og skill fra `build-info.json` ved siden af).
  `tools\release.ps1` gør det for dig, se [release.md](release.md).
- `-DryRun` bygger og signerer ikke. Den skriver et usigneret manifest og kommandoerne, så man kan se, hvad der
  ville ske.

Nøglen:

- `tools\release_key.py public` skriver den offentlige nøgle. Den skal være den samme som `update.PUBLIC_KEY`.
- Er pc'en ny, gendan nøglen med `restore` fra backup'en i password manageren.

## 6. Test

- `tests/test_autoupdate.py` kører uden GUI og uden net. Den starter en lokal HTTP-server (`http.server` i en
  tråd), som serverer falske udgivelser signeret med en engangsnøgle, der laves i testen (`update.PUBLIC_KEY`
  patches). Testen kører i en sandkasse med midlertidig LOCALAPPDATA, APPDATA og USERPROFILE. Den dækker:
  - god opdatering: pakket ud ved siden af uden skift, oprydning, skift, Claudes indgang, genveje;
  - forkert signatur og anden nøgle;
  - forkert SHA-256 og forkert størrelse;
  - zip med `..` og zip uden exe;
  - netværksfejl (ingen server, 404) og et manifest, der ikke er JSON;
  - ældre og samme version;
  - reglen om én gang i døgnet;
  - at gamle indstillingsfiler stadig kan indlæses.
- `tests/autoupdate_shots.py --theme light|night [--ui D:\SubaDubaLingo\assets\ui]` tager billeder af båndet og
  Indstillinger og tjekker knapperne. Billederne gemmes i `design/implemented/<tema>/autoupdate-*.png`.
  - Billederne: klar, under en kørsel, efter genstart, og Indstillinger.
  - Knapperne: Genstart nu er grå under en kørsel, Senere virker, og Genstart nu skifter, slipper mutex'en, starter
    den nye og lukker vinduet.
- `tests/install_test.py` dækker stadig installation fra zip (`install()` = `place()` + `activate()`).

## 7. Overvejet og fravalgt

- **Velopack** (Squirrels efterfølger, har Python-binding) henter, laver delta-pakker og genstarter. Den er fravalgt
  indtil videre:
  - Den installerer i sin egen `current\`-mappe og løser ikke problemet med Claudes MCP-server, der låser mappen.
  - Den skal bygges ind i build og install.
  - Den er værd at se på igen, hvis zip'en på ca. 130 MB bliver et problem (delta-opdateringer).
- **Opdatering i stilhed uden at spørge:** nej. Brugeren skal vide, hvorfor programmet ser anderledes ud, og et job
  må aldrig blive afbrudt.
- **Authenticode-signering af exe'en:** den ville hjælpe med SmartScreen og WDAC, men kræver et EG-kodecertifikat.
  Ed25519 over manifestet dækker selve opdateringen.
- **"Søg nu"-knap i Indstillinger:** ikke lavet. Tjekket sker ved start, og det er nok, når der kun udgives en
  gang imellem.
