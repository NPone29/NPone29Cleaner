# Publie une nouvelle version sur GitHub : les utilisateurs se la verront proposer au démarrage de l'application.
# Utilisation (après build.bat) :
#   powershell -ExecutionPolicy Bypass -File publish.ps1          version finale
#   powershell -ExecutionPolicy Bypass -File publish.ps1 -Beta    pré-version (proposée seulement aux bêta-testeurs)
# Prérequis : GitHub CLI (gh) connecté, version changée dans app\config.py ET version_info.txt, build.bat lancé.

param([switch]$Beta)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$version = (Select-String -Path 'app\config.py' -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$tag = "v$version"
$setup = "dist\NPone29Cleaner-Setup-$version.exe"
$portable = 'dist\NPone29Cleaner.exe'

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh -and (Test-Path 'C:\Program Files\GitHub CLI\gh.exe')) { $gh = 'C:\Program Files\GitHub CLI\gh.exe' }
if (-not $gh) { throw 'GitHub CLI (gh) introuvable : winget install GitHub.cli, puis gh auth login.' }

foreach ($file in $setup, $portable) {
    if (-not (Test-Path $file)) { throw "$file introuvable : lancez d'abord build.bat." }
}
# Le .exe portable doit venir de la même compilation que l'installateur (même version).
$exeVersion = (Get-Item $portable).VersionInfo.ProductVersion
if ($exeVersion -and -not $exeVersion.StartsWith(($version -split '-')[0])) {
    throw "dist\NPone29Cleaner.exe est en version $exeVersion, pas $version : relancez build.bat (et vérifiez version_info.txt)."
}

& $gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) { throw "La version $tag est déjà publiée : changez APP_VERSION dans app\config.py." }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne 'main') { throw "Publiez depuis main (branche actuelle : $branch)." }
if (git status --porcelain) { throw 'Des modifications ne sont pas commitées : commitez-les avant de publier.' }
git push origin main

$ghArgs = @('release', 'create', $tag, $setup, $portable, '--target', 'main',
          '--title', "NPone29 Cleaner $version", '--generate-notes')
if ($Beta) { $ghArgs += '--prerelease' }
Write-Host "> Publication de $tag$(if ($Beta) { ' (bêta)' })..."
& $gh @ghArgs
if ($LASTEXITCODE -ne 0) { throw 'La publication a échoué.' }
Write-Host "OK : $tag publiée. Les utilisateurs se la verront proposer au prochain démarrage." -ForegroundColor Green
