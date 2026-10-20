# Sådan kommer en ny version ud

Siden 2026-10-20 laves releases på McHoense (Linux) af Claude. Appen er kun til Windows: GitHub Actions bygger og
tester på Windows, og den sidste kontrol sker i Windows-test-VM'en på boksen (`~/winvm`). Signeringsnøglen ligger på
boksen (`~/.subadubalingo-release/signing-key.hex`, kun læsbar for `repo`; Kaspers beslutning, boksen har ingen andre
brugere). Kasper siger ja i chatten, før en version lægges ud.

1. **Claude** hæver `APP_VERSION` i `app/subadubalingo/common.py` og pusher til `main`. CI (`ci.yml`) kører alle
   `tests/test_*.py` og skærmbillede-scripts i begge temaer (artifact `screenshots`).
2. **Release-build:** `gh workflow run release-build.yml --repo swaipa46/subadubalingo`. Den bygger zip'en
   (artifact `release-zip`, 30 dage) og udgiver ikke noget.
3. **Hent og tjek versionen:** `python3 tools/release.py prepare` (nyeste grønne release-build på main, eller
   `--run-id N`). Den stopper, hvis versionen ikke er nyere end seneste udgivelse.
4. **Tjek i Windows-VM'en.** VM'en deles med Lex247-arbejdet (ejer `lex`) og kører altid (startes af
   `boot_all.sh`); stop den ikke. Tag den med `~/winvm/slot.sh take suba` (siger den "busy", så vent eller sig til
   Kasper; aldrig `--force`), og forny med samme kommando under længere arbejde. Kopiér zip'en ind (scp, port 2222,
   til `C:\t`), installér med `SubaDubaLingo.exe --install` oven på den forrige version, og start appen fra
   skrivebordsgenvejen (noVNC på :8006; en app startet over SSH vises ikke på skrivebordet). Rør ikke `C:\Lex247`.
   Tjekliste:
   - Appen starter og viser den nye version.
   - Det nye i versionen virker (fx en kort video igennem, nye dialoger).
   - Opdateringsbanneret og Indstillinger ser rigtige ud.
   Luk appen, ryd `C:\t`, og giv VM'en fri med `~/winvm/slot.sh release suba`. Skærmbilleder til Kasper.
5. **Udgiv:** når Kasper har sagt ja i chatten til netop den version:
   `python3 tools/release.py publish --zip "dist-release/run-N/SubaDubaLingo X.Y.Z.zip" --notes "..." --yes`.
   Den skriver og signerer `latest.json`, tjekker signaturen som appen gør, og kører `gh release create` på den
   commit, zip'en er bygget fra. Uden `--yes` viser den kun kommandoen.

Godt at vide:

- Nøglen lægges ind én gang med `python3 tools/release_key.py restore` (de 64 tegn fra backuppen i Kaspers password
  manager; Kasper taster dem selv). `python3 tools/release_key.py public` skal give samme nøgle som `PUBLIC_KEY` i
  `app/subadubalingo/update.py`.
- `tools\release.ps1` og `tools\publish.ps1` (den gamle vej fra Kaspers pc) virker stadig, hvis det bliver nødvendigt.
- På CI kører ikke de scripts, der kræver en Whisper-model, Kaspers optagelser eller to byggede programmapper.
  Listen står øverst i `tests/ci_run.py`.
