# The release gate on Kasper's PC (docs/release.md): takes the zip the release-build workflow made, installs it over
# the real installation here, walks through a short checklist, and only when every answer is yes signs and publishes
# it with tools\publish.ps1 -Zip. Signing stays on this PC (tools\release_key.py, DPAPI); the key is never read here.
#
#   tools\release.ps1                    the newest successful release-build run on main
#   tools\release.ps1 -RunId 123456789   that run
#   tools\release.ps1 -DryRun            only find the run, download the zip and check the version (installs nothing)
#   tools\release.ps1 -Notes "..."       the note for the users (Danish, in latest.json); asked for when left out
param(
    [long]$RunId,
    [string]$Notes,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$name = 'SubaDubaLingo'
$repo = (Select-String -Path app\subadubalingo\update.py -Pattern '^REPO = "(.+)"').Matches[0].Groups[1].Value
# The repo's gh login (the same one its git credential helper uses), unless one is chosen already
$ghDir = Join-Path $HOME '.config\gh-swaipa46'
if (-not $env:GH_CONFIG_DIR -and (Test-Path $ghDir)) { $env:GH_CONFIG_DIR = $ghDir }

function Invoke-Gh {
    $out = & gh @args
    if ($LASTEXITCODE) { throw "gh $($args -join ' ') failed" }
    $out
}

function Ask([string]$question) {
    while ($true) {
        $a = (Read-Host "$question (j/n)").Trim().ToLower()
        if ($a -in 'j', 'ja', 'y', 'yes') { return $true }
        if ($a -in 'n', 'nej', 'no') { return $false }
    }
}

function Stop-Release([string]$why) {
    Write-Host ''
    Write-Host "Ikke udgivet: $why" -ForegroundColor Yellow
    exit 1
}

# --- 1. The run ------------------------------------------------------------------------------------------------
$fields = 'databaseId,headSha,headBranch,createdAt,displayTitle,conclusion,event'
if ($RunId) {
    $run = Invoke-Gh run view $RunId --repo $repo --json $fields | ConvertFrom-Json
    if ($run.conclusion -ne 'success') { throw "Kørsel $RunId er ikke lykkedes ($($run.conclusion))" }
} else {
    $run = @(Invoke-Gh run list --repo $repo --workflow release-build.yml --branch main --status success --limit 1 `
             --json $fields | ConvertFrom-Json)[0]
    if (-not $run) { throw "Ingen lykket release-build-kørsel på main i $repo. Start en: gh workflow run release-build.yml --repo $repo" }
}
$sha = $run.headSha
Write-Host "Kørsel $($run.databaseId): $($run.displayTitle) ($($run.event), $($run.createdAt), $($sha.Substring(0, 7)))"
Write-Host "  https://github.com/$repo/actions/runs/$($run.databaseId)"

# --- 2. The zip ------------------------------------------------------------------------------------------------
$dl = Join-Path $root "dist-release\run-$($run.databaseId)"
if (-not (Get-ChildItem $dl -Filter "$name *.zip" -ErrorAction SilentlyContinue)) {
    Remove-Item -Recurse -Force $dl -ErrorAction SilentlyContinue
    Write-Host "Henter artifact release-zip til $dl ..."
    Invoke-Gh run download $run.databaseId --repo $repo --name release-zip --dir $dl | Out-Null
}
$zip = @(Get-ChildItem $dl -Filter "$name *.zip")
if ($zip.Count -ne 1) { throw "Forventede én $name <version>.zip i $dl, fandt $($zip.Count)" }
$zip = $zip[0]
$version = $zip.BaseName.Substring($name.Length + 1)
$infoFile = Join-Path $dl 'build-info.json'
if (Test-Path $infoFile) {
    $info = Get-Content $infoFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($info.version -ne $version -or $info.sha -ne $sha) { throw "build-info.json passer ikke med zip'en eller kørslen" }
} else {
    throw "build-info.json mangler i artifact'en (bygget før release-build skrev den?)"
}
Write-Host "Zip: $($zip.FullName) ($([math]::Round($zip.Length / 1MB)) MB), version $version, skill $($info.skill)"

# --- 3. Newer than the latest release? -------------------------------------------------------------------------
$latest = & gh release view --repo $repo --json tagName --jq .tagName 2>$null
if ($LASTEXITCODE) { $latest = $null }   # no release yet
$latestVersion = if ($latest) { $latest.TrimStart('v') } else { '0.0.0' }
if ([version]$version -le [version]$latestVersion) {
    Stop-Release "$version er ikke nyere end den seneste udgivelse ($latest). Hæv APP_VERSION i common.py og byg igen."
}
Write-Host "Nyere end seneste udgivelse: $version > $(if ($latest) { $latest } else { '(ingen)' })" -ForegroundColor Green
$localSha = (& git rev-parse HEAD 2>$null)
if ($localSha -and $localSha -ne $sha) {
    Write-Host "Bemærk: denne checkout står på $($localSha.Substring(0, 7)), zip'en er bygget fra $($sha.Substring(0, 7)). Signering og tjek bruger tools\ og app\subadubalingo\update.py herfra." -ForegroundColor Yellow
}

if ($DryRun) {
    Write-Host ''
    Write-Host '[dry run] Resten ville være:'
    Write-Host "  installere $($zip.Name) med $name.exe --install over din installation, og starte den"
    Write-Host '  tjeklisten (j/n), og hvis alt er ja:'
    Write-Host "  tools\publish.ps1 -Zip `"$($zip.FullName)`" -Notes ...  (signerer latest.json her)"
    Write-Host "  gh release create v$version ... --repo $repo  (efter 'Udgiv nu? (j/n)')"
    exit 0
}

# --- 4. Install over the real installation ---------------------------------------------------------------------
function Get-ClaudeDesktop {
    Get-Process claude -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ($_.Path -match '\\WindowsApps\\Claude_' -or $_.Path -match '\\AnthropicClaude\\') }
}
if (Get-ClaudeDesktop) {
    Write-Host ''
    Write-Host 'Claude desktop kører. Installationen retter kun Claudes MCP-opsætning, mens Claude er lukket.'
    Read-Host 'Luk Claude desktop helt (også ikonet ved uret), og tryk Enter'
    if (Get-ClaudeDesktop) { Stop-Release 'Claude desktop kører stadig.' }
}
$unpacked = Join-Path $dl 'unpacked'
Remove-Item -Recurse -Force $unpacked -ErrorAction SilentlyContinue
Expand-Archive $zip.FullName $unpacked
$newExe = Join-Path $unpacked "$name\$name.exe"
if (-not (Test-Path $newExe)) { throw "Ingen $name\$name.exe i zip'en" }
Write-Host "Installerer $version ..."
$p = Start-Process $newExe -ArgumentList '--install' -Wait -PassThru
$log = Join-Path $HOME '.subadubalingo\log.txt'
if ($p.ExitCode -eq 2) { Stop-Release "version $version er allerede installeret og kører. Luk den og prøv igen." }
if ($p.ExitCode -ne 0) { Stop-Release "installationen fejlede (kode $($p.ExitCode)); se $log" }
$base = Join-Path $env:LOCALAPPDATA "Programs\$name"
$current = (Get-Content (Join-Path $base 'current.txt') -Raw).Trim()
$installed = Join-Path $base "$version\$name.exe"
if ($current -ne $version -or -not (Test-Path $installed)) { Stop-Release "current.txt siger $current, ikke $version" }
Write-Host "Installeret: $installed" -ForegroundColor Green
Start-Process $installed

# --- 5. The checklist ------------------------------------------------------------------------------------------
Write-Host ''
Write-Host "Tjekliste for $name $version (svar j eller n):"
$ok = Ask "1. Starter appen, og viser den version $version (Indstillinger)?"
$ok = $ok -and (Ask '2. Kør en kort, rigtig video igennem (lyt igennem / undertekster). Gik det godt?')
if ($ok) {
    # Where Claude desktop's MCP entry points: its own config, and the MSIX copy it may really read
    $cfgs = @((Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'),
              (Join-Path $env:LOCALAPPDATA 'Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json'))
    foreach ($cfg in $cfgs | Where-Object { Test-Path $_ }) {
        $entry = (Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json).mcpServers.subadubalingo
        $cmd = if ($entry) { $entry.command } else { '(ingen subadubalingo-post)' }
        $mark = if ($cmd -eq $installed) { 'ny version' } else { 'IKKE den nye version' }
        Write-Host "   $cfg"
        Write-Host "     command: $cmd  -> $mark"
    }
    $ok = Ask '3. Start Claude desktop/Cowork og bed den bruge SubaDubaLingo. Kører den den NYE version (se ovenfor)?'
}
$ok = $ok -and (Ask '4. Ser opdateringsbanneret og Indstillinger rigtige ud?')
if (-not $ok) { Stop-Release "et punkt i tjeklisten var nej. $name $version er stadig installeret her; ret fejlen og byg en ny version." }

# --- 6. Sign and publish ---------------------------------------------------------------------------------------
if (-not $Notes) { $Notes = Read-Host 'Kort note til brugerne om denne version (dansk)' }
if (-not $Notes) { Stop-Release 'ingen note.' }
& (Join-Path $PSScriptRoot 'publish.ps1') -Notes $Notes -Zip $zip.FullName
if ($LASTEXITCODE) { throw 'publish.ps1 fejlede' }
$release = Join-Path $root "dist-release\release-$version"
$asset, $stable = (Join-Path $release "$name-$version.zip"), (Join-Path $release "$name.zip")
$manifest, $notesFile = (Join-Path $release 'latest.json'), (Join-Path $release 'notes.txt')
$ghArgs = @('release', 'create', "v$version", $asset, $stable, $manifest, '--repo', $repo, '--title', "$name $version",
            '--notes-file', $notesFile, '--target', $sha)
Write-Host ''
Write-Host 'Kommandoen:'
$shown = foreach ($a in $ghArgs) { if ($a -match '\s') { '"' + $a + '"' } else { $a } }
Write-Host ('  gh ' + ($shown -join ' '))
if (-not (Ask 'Udgiv nu?')) { Stop-Release "du sagde nej. Filerne ligger i $release" }
Invoke-Gh @ghArgs
Write-Host "Udgivet: https://github.com/$repo/releases/tag/v$version" -ForegroundColor Green
