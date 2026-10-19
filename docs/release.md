# Sådan kommer en ny version ud

Udviklingen sker på en Linux-server, men appen er kun til Windows. Derfor bygger og tester GitHub Actions på Windows,
og Kasper godkender og udgiver fra sin pc, hvor signeringsnøglen ligger.

1. **Udvikleren** hæver `APP_VERSION` i `app/subadubalingo/common.py` og pusher til `main`.
   CI (`.github/workflows/ci.yml`) kører alle `tests/test_*.py` og skærmbillede-scripts i begge temaer.
   Skærmbillederne ligger som artifact `screenshots` på kørslen.
2. **Release-build:** start den med `gh workflow run release-build.yml --repo swaipa46/subadubalingo`
   (eller *Run workflow* under Actions). Den kører testene og `build.ps1` og gemmer zip'en som artifact
   `release-zip` i 30 dage. Den udgiver ikke noget og har ingen nøgle.
3. **Kasper** kører på sin pc:

   ```powershell
   tools\release.ps1              # nyeste lykkede release-build på main
   tools\release.ps1 -RunId 123   # en bestemt kørsel
   tools\release.ps1 -DryRun      # find kørslen, hent zip'en og tjek versionen, uden at installere
   ```

   Scriptet henter zip'en og stopper, hvis versionen ikke er nyere end seneste udgivelse. Så beder det dig lukke
   Claude desktop og installerer den nye version oven på din egen installation (`SubaDubaLingo.exe --install`)
   og starter den.
4. **Tjekliste** (j/n for hvert punkt):
   - Appen starter og viser den nye version.
   - En kort, rigtig video kører igennem.
   - Claude desktop/Cowork starter den nye version. Scriptet viser, hvor `claude_desktop_config.json` peger hen.
   - Opdateringsbanneret og Indstillinger ser rigtige ud.

   Er ét punkt nej, stopper scriptet. Den nye version er så stadig installeret hos dig. Ret fejlen, hæv versionen
   og byg igen.
5. **Udgiv:** er alt ja, spørger scriptet efter en kort note til brugerne. Så kører det
   `tools\publish.ps1 -Zip ...`, der signerer `latest.json` på din pc (se [auto-opdatering.md](auto-opdatering.md),
   afsnit 5). Til sidst viser det `gh release create`-kommandoen og spørger "Udgiv nu? (j/n)". Det er først her,
   noget bliver lagt ud.

Godt at vide:

- Tagget `v<version>` laves af `gh release create` på den commit, zip'en er bygget fra. Et tag-push starter også
  release-build. Den kørsel skal ikke bruges til noget.
- På CI kører ikke de scripts, der kræver en Whisper-model, Kaspers optagelser eller to byggede programmapper.
  Listen står øverst i `tests/ci_run.py`. Kør dem på en pc med modellerne, når de berører det, der er ændret.
