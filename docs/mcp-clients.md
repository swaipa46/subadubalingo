# Brug med andre AI-værktøjer (v2.1.1, tag over i v2.2.0)

SubaDubaLingo er en lokal MCP-server (JSON-RPC over stdio), så ethvert AI-værktøj, der kan starte en lokal
MCP-server, kan styre appen: Claude (desktop-appen og Claude Code), Gemini CLI, OpenAI Codex CLI, GitHub Copilot i
VS Code og Cursor. ChatGPT-appen kan ikke (den bruger kun MCP-servere på nettet).

I appen: Indstillinger -> **Brug med andre AI-værktøjer** viser opsætningen til hvert værktøj med en Kopiér-knap
(`app/subadubalingo/mcpclients.py`). Teksterne ligger i `assets/locales/<sprog>/mcpclients.json`.

## Status

| Værktøj | Status | Opsætning |
|---|---|---|
| Claude (desktop-appen) | Testet (siden v1.7) | Knappen "Forbind med Claude" |
| Claude Code | Testet 16.10.2026 gennem launcheren (headless, `claude -p`: værktøjer, `guide`, `find_videos`) | `claude mcp add` |
| Gemini CLI | Burde virke. Testen 16.10.2026 blev stoppet af login (se nedenfor) | `~/.gemini/settings.json` |
| Codex CLI | Burde virke (ikke testet) | `~/.codex/config.toml` |
| VS Code / GitHub Copilot | Burde virke (ikke testet) | brugerens `mcp.json` |
| Cursor | Burde virke (ikke testet) | `~/.cursor/mcp.json` |

Gemini CLI 0.37.2 på Kaspers pc kunne ikke logge ind: `IneligibleTierError: This client is no longer supported for
Gemini Code Assist for individuals` (Google henviser til Antigravity). Det er kontoen, ikke SubaDubaLingo. Når
Gemini virker igen, testes det som i "Test" nedenfor.

## Launcheren: en sti, der aldrig ændrer sig

Alle værktøjer undtagen Claude desktop peger på

    %LOCALAPPDATA%\Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe

som læser `current.txt` ved siden af sig og starter `<version>\SubaDubaLingo.exe --mcp` med de samme stdin, stdout og
stderr (`tools/mcp_launcher.cs`, 11 KB, bygges af `build.ps1` med .NET Frameworks `csc.exe`, som alle Windows 10/11
har). Installationen og selvopdateringen (`installer.activate`) kopierer den fra versionsmappen. Værktøjernes egne
opsætninger skal brugeren selv rette, så de må ikke pege på en versionsmappe.

- Launcheren skriver intet på stdout (MCP er JSON-RPC over stdout); fejl går til stderr. Målt mod 2.1.0-exe'en:
  stdout er byte for byte det samme som exe'en direkte, og den koster ca. 60 ms ved start.
- Hvorfor en exe og ikke en `.cmd`: en `.cmd` med `@echo off` var også ren på stdout, men Node (som Claude Code,
  Gemini CLI og VS Code bruger) nægter at starte en `.cmd` uden en shell (`spawn EINVAL`, målt med Node 25), og en
  `.cmd` åbner et konsolvindue.
- Serveren kører i et job object, så den stopper, hvis værktøjet dræber launcheren. Appen, som serveren starter,
  bryder ud af jobbet og bliver ved med at køre.
- Kører et værktøj launcheren under en opdatering, omdøbes den gamle (`SubaDubaLingo-mcp.exe.old-<tid>`), og
  `installer.cleanup` sletter den, når den er fri.

**Claude desktop** peger stadig på versionens exe (`<version>\SubaDubaLingo.exe --mcp`). Installationen skriver
Claudes opsætning om ved hver installation og opdatering, så den har ikke brug for launcheren, og det sparer en
proces. Beskeden "SubaDubaLingo er opdateret, genstart Claude" virker som før.

## Serveren er sin egen manual

Nogle værktøjer ignorerer serverens `instructions`. Derfor:

- **Værktøjsbeskrivelserne** bærer arbejdsgangen. `start_video` har hele forløbet på én linje: `guide` (én gang) ->
  `start_video` -> `wait` -> på `claude_turn`: `get_text`, `save_text`, `suggest_cuts`, `save_chapters` -> på
  `review_cuts`: spørg brugeren, `approve_cuts` -> `wait` til færdig.
- **Værktøjet `guide`** (uden argumenter, eller `section`: `steps`, `text`, `cuts`, `chapters`, `words`, `never`)
  giver hele manualen: trinene, jobtilstandene, reglerne for rettelser, oversættelse, klip (også `filler`) og
  kapitler/resumé, brugerens ordliste (fra `corrections.txt`), hvilket sprog der tales med
  brugeren (appens sprog) og hvad der aldrig må gøres.
- **Prompt og ressourcer** til værktøjer, der bruger dem: prompten `subadubalingo_workflow` (valgfri `video` og
  `request`; en slash-kommando i Gemini CLI og Claude Code) og ressourcerne `subadubalingo://guide` og
  `subadubalingo://guide/<section>`. De meldes i `initialize`; et værktøj uden dem spørger aldrig.
- `initialize`'s `instructions` er der stadig og peger på `guide`.

Claude-skill'en fra version 2.1.x og ældre er fjernet (2.2.0). Manualen (afsnittet `guide.OLD_SKILL`) og
`instructions` siger til en assistent, der stadig har en skill "subadubalingo" eller "videosprog", at den er forældet:
ignorér den, og sig én gang til brugeren, at den kan slettes under Customize -> Skills. `get_text` tager stadig imod
det gamle argument `skill` og ignorerer det (ukendte argumenter droppes, `mcp_server._known_args`).

## Tag over: et job, brugeren startede i appen (v2.2.0)

Starter brugeren en video i appen selv, viser kortet "AI-assistent" kopiér/indsæt til en hvilken som helst AI-chat og
desuden linjen "Har du forbundet en AI-assistent? Skriv til den: Tag over i SubaDubaLingo" med en Kopiér-knap.
Samtidig lægger appen jobbet ud som et job i `claude_turn` (`jobs.publish_app_job`, `job.json` med
`"origin": "app"`). Skriver brugeren sætningen (eller "take over") til et forbundet værktøj:

1. `status` uden `job_id` finder jobbet, der venter på assistenten (`jobs.waiting_job`; standarden for `wait`,
   `status` og `get_text` er nu: det ventende job, ellers det, appen arbejder på, ellers det seneste). Svaret siger,
   at brugeren startede jobbet i appen, og hvordan det overtages.
2. `get_text` overtager det: serveren skriver `jobs/<id>/taken.json`, kortet skifter til AI'ens fremskridt, og appen
   melder herefter `review_cuts` og `done` til assistenten som for dens egne jobs.
3. Resten som altid: `save_text`, `suggest_cuts`, `save_chapters`, `wait`.

Har brugeren allerede sat svar ind fra en chat, droppes de ved overtagelsen (assistenten laver hele teksten). Har
brugeren sat alle dele ind, vinder chatten: jobbet får tilstanden `by_user`, og `get_text`/`save_text` svarer, at
der intet er at gøre. Manualen har afsnittet "Taking over a job started in the app" med sætningen på alle fire sprog
(`guide.TAKE_OVER_PHRASES`, testet mod `gui.parts.mcp_phrase`). Test: `tests/test_take_over.py`.

Klientens navn fra `initialize` (`clientInfo.name`) bestemmer det eneste, der kun gælder Claude: efter en opdatering
får andre værktøjer "genstart <værktøj>" i stedet for Claude desktops "højreklik på ikonet ved uret".

## Opsætning pr. værktøj

Stien er launcherens, her for brugeren `kasper`. Kopiér den fra appen, så stien passer.

**Claude Code** ([docs](https://code.claude.com/docs/en/mcp)), i PowerShell eller Kommandoprompt:

    claude mcp add --transport stdio --scope user subadubalingo -- "C:\Users\kasper\AppData\Local\Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe"

**Gemini CLI** ([docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md)),
i `%USERPROFILE%\.gemini\settings.json` (eller `.gemini\settings.json` i et projekt):

```json
{
  "mcpServers": {
    "subadubalingo": {
      "command": "C:\\Users\\kasper\\AppData\\Local\\Programs\\SubaDubaLingo\\SubaDubaLingo-mcp.exe",
      "args": []
    }
  }
}
```

Gemini CLI's værktøjs-timeout er 10 minutter som standard; `wait` svarer efter højst ca. 45 s.

**Codex CLI** ([docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[config](https://learn.chatgpt.com/docs/config-file/config-reference)), sidst i `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.subadubalingo]
command = 'C:\Users\kasper\AppData\Local\Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe'
args = []
startup_timeout_sec = 30
tool_timeout_sec = 120
```

Timeouts sættes, fordi Codex' dokumentation og kode er uenige om standarden (60 s eller 300 s pr. kald).

**VS Code / GitHub Copilot**
([docs](https://code.visualstudio.com/docs/copilot/reference/mcp-configuration)): Ctrl+Shift+P -> "MCP: Open User
Configuration" (eller `.vscode\mcp.json` i et projekt). Brug Copilot Chat i agent-tilstand.

```json
{
  "servers": {
    "subadubalingo": {
      "type": "stdio",
      "command": "C:\\Users\\kasper\\AppData\\Local\\Programs\\SubaDubaLingo\\SubaDubaLingo-mcp.exe",
      "args": []
    }
  }
}
```

**Cursor** ([docs](https://cursor.com/docs/context/mcp)), i `%USERPROFILE%\.cursor\mcp.json` (eller
`.cursor\mcp.json` i et projekt):

```json
{
  "mcpServers": {
    "subadubalingo": {
      "type": "stdio",
      "command": "C:\\Users\\kasper\\AppData\\Local\\Programs\\SubaDubaLingo\\SubaDubaLingo-mcp.exe",
      "args": []
    }
  }
}
```

Har filen allerede servere, tilføjes kun `"subadubalingo": {...}` under `mcpServers` eller `servers`.

## Test

- `tests/test_mcp_clients.py`: en rå JSON-RPC-session over stdio mod kildeserveren (`guide`, prompts, ressourcer,
  fejlkoder, svar på forespørgsler sendt lige før stdin lukkes), det der kun gælder Claude, hver opsætning som JSON
  eller TOML, og launcheren (kompileret med `csc` om en stub-app: stdout byte for byte, argumenter, exit-kode, intet
  eget output, serveren stopper med den, udskiftning mens den kører).
- `tests/mcpclients_shots.py --theme light|night [--lang en]`: skærmbilleder af kortet i
  `design/implemented/<tema>/mcpclients-<værktøj>.png`.
- Gemini CLI i en midlertidig projektmappe, uden at røre `~/.gemini/settings.json`: læg opsætningen i
  `<mappe>\.gemini\settings.json` med `"trust": true` (så værktøjskald ikke afvises i headless-tilstand), og kør i
  mappen med `GEMINI_CLI_TRUST_WORKSPACE=true` (mappen godkendes kun for den kørsel):
  `gemini --allowed-mcp-server-names subadubalingo -p "List the subadubalingo tools, call guide, then find_videos"`.
- En hel kørsel med en video (`start_video` -> `wait` -> `get_text`/`save_text` -> færdig) kan også køres headless fra
  `gemini -p` eller `claude -p`, så længe appen kan starte på skrivebordet (den er et vindue). Uden `cut` skal
  brugeren ikke gøre noget undervejs; med `cut` venter jobbet på, at brugeren godkender klippene.
