# Costruisce Script Follower per Windows 10/11 (64 bit): cartella portable in uno zip (nessuna installazione).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1      (dalla cartella del progetto)
#
# Serve: Python 3.11-3.13 da python.org (con «py» launcher) e la connessione a Internet.
# MODEL_URL vuota = senza modello vocale incluso (si scarica poi dal programma).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Build = Join-Path $Root "build\windows"
$Cache = Join-Path $Root "build\cache"
New-Item -ItemType Directory -Force -Path $Build, $Cache, (Join-Path $Root "dist") | Out-Null
$Version = (Select-String -Path "scriptfollower\__init__.py" -Pattern '__version__\s*=\s*"(.*)"').Matches[0].Groups[1].Value
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

$Py = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$PyArgs = if ($Py -eq "py") { @("-3") } else { @() }
Say "ambiente Python"
& $Py @PyArgs -m venv "$Build\venv"
& "$Build\venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$Build\venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip non riuscito" }

$ModelUrl = if ($null -ne $env:MODEL_URL) { $env:MODEL_URL } else { "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip" }
$ModelDir = ""
if ($ModelUrl) {
    Say "modello vocale"
    $ModelZip = Join-Path $Cache "model.zip"
    if (-not (Test-Path $ModelZip)) { Invoke-WebRequest -Uri $ModelUrl -OutFile $ModelZip -UseBasicParsing }
    $ModelRoot = Join-Path $Build "model"
    Remove-Item -Recurse -Force $ModelRoot -ErrorAction SilentlyContinue
    Expand-Archive -Path $ModelZip -DestinationPath $ModelRoot
    $ModelDir = (Get-ChildItem -Path $ModelRoot -Directory | Select-Object -First 1).FullName
    $env:SCRIPTFOLLOWER_MODEL_DIR = $ModelDir
}

Say "PyInstaller"
Remove-Item -Recurse -Force "dist\ScriptFollower" -ErrorAction SilentlyContinue
& "$Build\venv\Scripts\pyinstaller.exe" packaging\scriptfollower.spec --noconfirm --distpath dist --workpath "$Build\pyi"
if ($LASTEXITCODE) { throw "PyInstaller non riuscito" }

Say "prova di avvio"
$Report = Join-Path $Build "self-test.txt"
$env:QT_QPA_PLATFORM = "offscreen"
if ($ModelDir) { $env:SCRIPTFOLLOWER_REQUIRE_MODEL = "1" }
$p = Start-Process -FilePath "dist\ScriptFollower\ScriptFollower.exe" -ArgumentList "--self-test", "`"$Report`"" -Wait -PassThru -NoNewWindow
Remove-Item Env:\QT_QPA_PLATFORM
Get-Content $Report -Encoding UTF8
if ($p.ExitCode -ne 0) { throw "la prova di avvio ha trovato problemi" }

Say "zip portable"
$Zip = "dist\ScriptFollower-$Version-windows-portable.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\ScriptFollower" -DestinationPath $Zip -CompressionLevel Optimal
Say "fatto: $Zip ($([math]::Round((Get-Item $Zip).Length / 1MB)) MB)"
