# Builds <Out>\SubaDubaLingo\ (PyInstaller onedir) and a zip to hand out.
# Needs .venv (python -m venv .venv; .venv\Scripts\pip install pyinstaller numpy sv-ttk sherpa-onnx) and bin\ filled by fetch-tools.ps1.
# -Out: another folder than dist, e.g. while Claude desktop holds dist\...\SubaDubaLingo.exe open as MCP server.
param([string]$Out = 'dist')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$name = 'SubaDubaLingo'
$version = (Select-String -Path app\subadubalingo\common.py -Pattern 'APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
foreach ($t in 'bin\whisper\whisper-cli.exe', 'bin\piper\piper.exe', 'bin\ffmpeg\ffmpeg.exe') {
    if (-not (Test-Path $t)) { throw "Missing $t - run fetch-tools.ps1 first" }
}
# The icon goes into the exe (so shortcuts and Explorer show it); the files the window uses ride along as data.
$brand = 'assets\branding'
& .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name $name --icon "$brand\SubaDubaLingo.ico" `
    --add-data "$brand\SubaDubaLingo.ico;$brand" --add-data "$brand\header-logo.png;$brand" `
    --add-data "$brand\header-logo@2x.png;$brand" --add-data 'assets\ui;assets\ui' --add-data 'assets\fonts;assets\fonts' `
    --add-data 'assets\locales;assets\locales' --add-data 'bin;bin' --collect-data sv_ttk --collect-all sherpa_onnx --paths app --distpath $Out --workpath build app\main.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$dir = "$Out\$name"
New-Item -ItemType Directory -Force "$dir\models" | Out-Null
# The user guide in the four languages of the app (installer.guide_name)
foreach ($g in 'Sådan bruger du', 'Så använder du', 'Slik bruker du', 'How to use') {
    Copy-Item "docs\$g SubaDubaLingo.txt" $dir
}
& .venv\Scripts\python.exe tools\make_skill.py "$dir\subadubalingo-skill.zip"
if ($LASTEXITCODE) { throw 'make_skill failed' }
$zip = "$Out\$name $version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $dir -DestinationPath $zip
Get-Item $zip | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB) } }
