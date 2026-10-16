# Makes a release for the self-update (docs/auto-opdatering.md, section 5): builds with build.ps1, writes latest.json
# (version, zip name, sha256, size, notes, skill version), signs it with tools/release_key.py sign, and prints the
# gh release create command to run. It never uploads anything itself.
#
#   tools\publish.ps1 -Notes "Short note for the user (Danish)"          build, sign, print the command
#   tools\publish.ps1 -Notes "..." -SkipBuild                      reuse the zip build.ps1 made in -Out
#   tools\publish.ps1 -Notes "..." -DryRun                         no build, no signing: the manifest (unsigned) and
#                                                                  the command, to see what would happen
#
# The zip is uploaded as SubaDubaLingo-<version>.zip: GitHub turns spaces in asset names into dots, and the app takes
# the zip name from the manifest (update.py fetches it next to latest.json under releases/latest/download/).
param(
    [Parameter(Mandatory = $true)][string]$Notes,
    [string]$Out = 'dist-release',
    [switch]$SkipBuild,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
[Environment]::CurrentDirectory = $root   # .NET calls (WriteAllText) resolve relative paths from here
if (-not [IO.Path]::IsPathRooted($Out)) { $Out = Join-Path $root $Out }
$name = 'SubaDubaLingo'
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'D:\SubaDubaLingo\.venv\Scripts\python.exe' }   # a worktree uses the main venv
$version = (Select-String -Path app\subadubalingo\common.py -Pattern 'APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$skill = (Select-String -Path app\subadubalingo\claude.py -Pattern '^SKILL_VERSION = (\d+)').Matches[0].Groups[1].Value
$repo = (Select-String -Path app\subadubalingo\update.py -Pattern '^REPO = "(.+)"').Matches[0].Groups[1].Value
$built = Join-Path $Out "$name $version.zip"
$release = Join-Path $Out "release-$version"
$asset = Join-Path $release "$name-$version.zip"
# The same zip under a fixed name, for the website's download button (releases/latest/download/SubaDubaLingo.zip)
$stable = Join-Path $release "$name.zip"
$manifest = Join-Path $release 'latest.json'
Write-Host "$name $version (skill $skill) -> $repo"

if ($DryRun) {
    Write-Host "[dry run] would run: .\build.ps1 -Out $Out"
} elseif (-not $SkipBuild) {
    & .\build.ps1 -Out $Out
    if ($LASTEXITCODE) { throw 'build.ps1 failed' }
}
New-Item -ItemType Directory -Force $release | Out-Null
if (Test-Path $built) {
    Copy-Item $built $asset -Force
    Copy-Item $built $stable -Force
    $size = (Get-Item $asset).Length
    $sha = (Get-FileHash $asset -Algorithm SHA256).Hash.ToLower()
} elseif ($DryRun) {
    Write-Host "[dry run] no $built yet: size and sha256 are placeholders"
    $size = 1
    $sha = '0' * 64
} else {
    throw "Missing $built (run without -SkipBuild)"
}
# The fields update.py checks; the signature covers all of them (update.manifest_bytes)
$m = [ordered]@{ version = $version; zip = "$name-$version.zip"; size = $size; sha256 = $sha; notes = $Notes
                 skill = [int]$skill }
[IO.File]::WriteAllText($manifest, ($m | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
if ($DryRun) {
    Write-Host "[dry run] would run: $py tools\release_key.py sign $manifest"
} else {
    & $py tools\release_key.py sign $manifest
    if ($LASTEXITCODE) { throw 'signing failed (is the key on this pc? tools\release_key.py public)' }
    # Check it the way the app will, with the public key it ships with
    & $py -c "import json,sys; sys.path.insert(0,'app'); from subadubalingo import update; m=json.load(open(sys.argv[1],encoding='utf-8')); sys.exit(0 if update.manifest_ok(m) else 1)" $manifest
    if ($LASTEXITCODE) { throw 'the signed manifest does not verify with update.PUBLIC_KEY' }
    Write-Host 'Signed, and it verifies with update.PUBLIC_KEY.'
}
Get-Content $manifest -Encoding UTF8 | Write-Host
$notesFile = Join-Path $release 'notes.txt'
[IO.File]::WriteAllText($notesFile, $Notes, [Text.UTF8Encoding]::new($false))
Write-Host ''
Write-Host 'Check the files, then run this to publish (nothing has been uploaded):'
Write-Host "  gh release create v$version `"$asset`" `"$stable`" `"$manifest`" --repo $repo --title `"$name $version`" --notes-file `"$notesFile`""
Write-Host "  git tag v$version; git push origin v$version   (gh creates the tag on the remote if it is missing)"
