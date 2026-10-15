# Downloads the external tools into bin\ (not in git): whisper.cpp (Vulkan build, also runs on CPU),
# Piper and ffmpeg (gyan.dev essentials: has libass, libx264 and Quick Sync).
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$dl = New-Item -ItemType Directory -Force vendor-dl
function Get-Zip($url, $zip, $dest) {
    if (-not (Test-Path "$dl\$zip")) { Invoke-WebRequest $url -OutFile "$dl\$zip" }
    Remove-Item -Recurse -Force "$dl\x" -ErrorAction SilentlyContinue
    Expand-Archive "$dl\$zip" "$dl\x"
    New-Item -ItemType Directory -Force $dest | Out-Null
    return "$dl\x"
}
$x = Get-Zip 'https://github.com/SubtitleEdit/support-files/releases/download/whispercpp-194/whisper-vulkan-x64.zip' 'whisper-vulkan-x64.zip' 'bin\whisper'
Copy-Item "$x\*" 'bin\whisper' -Recurse -Force
$x = Get-Zip 'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip' 'piper_windows_amd64.zip' 'bin\piper'
Copy-Item "$x\piper\*" 'bin\piper' -Recurse -Force
$x = Get-Zip 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' 'ffmpeg-essentials.zip' 'bin\ffmpeg'
$b = Get-ChildItem "$x" -Directory | Select-Object -First 1
Copy-Item "$($b.FullName)\bin\ffmpeg.exe", "$($b.FullName)\LICENSE" 'bin\ffmpeg' -Force
Remove-Item -Recurse -Force "$dl\x"
Get-ChildItem bin -Recurse -Filter *.exe | Select-Object FullName
