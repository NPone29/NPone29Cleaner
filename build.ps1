# Compile NPone29 Cleaner en un seul fichier : dist\NPone29Cleaner.exe
# Utilisation :  powershell -ExecutionPolicy Bypass -File build.ps1   (ou double-clic sur build.bat)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '> Création de l''environnement virtuel (.venv)...'
    python -m venv .venv
}
$py = '.venv\Scripts\python.exe'

Write-Host '> Installation des dépendances...'
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-dev.txt

Write-Host '> Lancement des tests...'
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Les tests ont échoué : compilation annulée.' }

if (-not (Test-Path 'assets\icon.ico')) {
    Write-Host '> Génération de l''icône...'
    & $py tools\make_icon.py
}

Write-Host '> Compilation avec PyInstaller (1 à 2 minutes)...'
# Dossier de travail hors du projet : OneDrive verrouille sinon les fichiers temporaires pendant la synchro.
$work = Join-Path $env:TEMP 'NPone29Cleaner-build'
& $py -m PyInstaller --noconfirm --clean --workpath $work NPone29Cleaner.spec
if ($LASTEXITCODE -ne 0) { throw 'La compilation a échoué.' }

$exe = Get-Item 'dist\NPone29Cleaner.exe'
Write-Host ''
Write-Host ("OK : {0} ({1:N1} Mo)" -f $exe.FullName, ($exe.Length / 1MB)) -ForegroundColor Green
