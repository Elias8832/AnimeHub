# Build the Windows package (TODO 74, Decision 40) into desktop\target\:
#   powershell -ExecutionPolicy Bypass -File desktop\windows\build.ps1
# Result: AnimeHub-windows-x64.zip, one AnimeHub\ folder with
#   AnimeHub.exe   the desktop wrapper (window + hub)
#   python\        Python's official embeddable build - users install no Python
#   app\           hub.py, animehub\, web\, mpv\, LICENSE
#   README.txt     what to install next to it (mpv, Git for Windows, ani-cli)
#   Add to Start menu.cmd   puts a Start-menu shortcut to AnimeHub.exe
# Needs cargo (Rust; the GNU toolchain also wants binutils' windres for the
# icon). The Python zip is downloaded once into desktop\target\tools\ and
# checked against the hash pinned here.
$ErrorActionPreference = "Stop"
$PyVersion = "3.12.10"
$PySha256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"  # python.org's MD5 cross-checked 2026-10-25

$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Out = Join-Path $Repo "desktop\target"
$Stage = Join-Path $Out "windows\AnimeHub"
$Zip = Join-Path $Out "AnimeHub-windows-x64.zip"

Push-Location (Join-Path $Repo "desktop")
try { cargo build --release; if ($LASTEXITCODE) { throw "cargo build failed" } } finally { Pop-Location }

$Tools = Join-Path $Out "tools"
$PyZip = Join-Path $Tools "python-$PyVersion-embed-amd64.zip"
if (-not (Test-Path $PyZip)) {
    New-Item -ItemType Directory -Force $Tools | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile "$PyZip.part" -UseBasicParsing
    Move-Item "$PyZip.part" $PyZip
}
if ((Get-FileHash $PyZip -Algorithm SHA256).Hash -ne $PySha256) {
    Remove-Item $PyZip
    throw "$PyZip does not match the pinned SHA-256 - removed, run again"
}

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force "$Stage\app" | Out-Null
Copy-Item (Join-Path $Out "release\anime-hub-desktop.exe") "$Stage\AnimeHub.exe"
Expand-Archive $PyZip "$Stage\python"
# The embeddable Python takes sys.path from its ._pth file only (not the
# script's folder): add the app folder, relative to python\.
$Pth = Get-ChildItem "$Stage\python\python*._pth" | Select-Object -First 1
Add-Content $Pth.FullName "../app" -Encoding Ascii
foreach ($item in "hub.py", "animehub", "web", "mpv", "LICENSE") {
    Copy-Item -Recurse (Join-Path $Repo $item) "$Stage\app\"
}
Get-ChildItem "$Stage\app" -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot "README.txt") "$Stage\README.txt"
# Set-Content writes CRLF lines, which cmd.exe wants (the source may be LF).
Get-Content (Join-Path $PSScriptRoot "start-menu.cmd") | Set-Content "$Stage\Add to Start menu.cmd" -Encoding Ascii

if (Test-Path $Zip) { Remove-Item $Zip }
# Entry by entry, named with "/": Windows PowerShell 5.1's CreateFromDirectory
# writes "\" separators, which unzippers outside Windows warn about.
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$Base = Split-Path $Stage -Parent
$Archive = [IO.Compression.ZipFile]::Open($Zip, [IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem $Stage -Recurse -File | ForEach-Object {
        $Name = $_.FullName.Substring($Base.Length).TrimStart("\", "/").Replace("\", "/")
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive, $_.FullName, $Name, [IO.Compression.CompressionLevel]::Optimal)
    }
} finally { $Archive.Dispose() }
"Built: $Zip ({0:N1} MB)" -f ((Get-Item $Zip).Length / 1MB)
