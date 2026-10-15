# Builds <Out>\Videosprog\ (PyInstaller onedir) and a zip to hand out.
# Needs .venv (python -m venv .venv; .venv\Scripts\pip install pyinstaller numpy sv-ttk sherpa-onnx) and bin\ filled by fetch-tools.ps1.
# -Out: another folder than dist, e.g. while Claude desktop holds dist\...\Videosprog.exe open as MCP server.
param([string]$Out = 'dist')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$name = 'Videosprog'
$version = (Select-String -Path app\subadubalingo\common.py -Pattern 'APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
foreach ($t in 'bin\whisper\whisper-cli.exe', 'bin\piper\piper.exe', 'bin\ffmpeg\ffmpeg.exe') {
    if (-not (Test-Path $t)) { throw "Missing $t - run fetch-tools.ps1 first" }
}
& .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name $name `
    --add-data 'bin;bin' --collect-data sv_ttk --collect-all sherpa_onnx --paths app --distpath $Out --workpath build app\main.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$dir = "$Out\$name"
New-Item -ItemType Directory -Force "$dir\modeller" | Out-Null
Copy-Item 'docs\Sådan bruger du Videosprog.txt' $dir
& .venv\Scripts\python.exe tools\make_skill.py "$dir\videosprog-skill.zip"
if ($LASTEXITCODE) { throw 'make_skill failed' }
$zip = "$Out\$name $version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $dir -DestinationPath $zip
Get-Item $zip | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB) } }
