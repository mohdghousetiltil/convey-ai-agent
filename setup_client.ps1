Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$pipExe = Join-Path $venvDir "Scripts\pip.exe"
$uiDir = Join-Path $projectRoot "ui"

Write-Host ""
Write-Host "=== TriConvey Agent Client Setup ===" -ForegroundColor Cyan
Write-Host "  Project: $projectRoot"
Write-Host ""

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python launcher 'py' was not found. Install Python 3.11 and try again."
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install Node.js and try again."
}

if (-not (Test-Path $venvDir)) {
  Write-Host "Creating virtual environment..." -ForegroundColor Yellow
  py -3.11 -m venv $venvDir
}

Write-Host "Upgrading pip..." -ForegroundColor Yellow
& $pythonExe -m pip install --upgrade pip

Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
& $pipExe install -r (Join-Path $projectRoot "requirements.txt")
& $pipExe install -e $projectRoot

Write-Host "Installing UI dependencies..." -ForegroundColor Yellow
Push-Location $uiDir
try {
  npm.cmd install
  Write-Host "Building UI..." -ForegroundColor Yellow
  npm.cmd run build
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Run the desktop app with:" -ForegroundColor Cyan
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python .\desktop_app.py"
Write-Host ""
Write-Host "Or build a distributable exe with:" -ForegroundColor Cyan
Write-Host "  .\build_desktop.ps1"
