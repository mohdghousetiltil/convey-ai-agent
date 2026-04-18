Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$uiDist = Join-Path $projectRoot "ui\dist"
$uiDistIndex = Join-Path $uiDist "index.html"
$desktopExe = Join-Path $projectRoot "dist\TriConveyAgent.exe"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

# --- Pre-flight checks ---

if (-not (Test-Path $pythonExe)) {
  throw "Virtualenv Python not found at $pythonExe. Create a venv first: python -m venv .venv && .venv\Scripts\pip install -e ."
}

if (-not (Test-Path $uiDist) -or -not (Test-Path $uiDistIndex)) {
  Write-Host "UI bundle not found. Building now..." -ForegroundColor Yellow
  Push-Location (Join-Path $projectRoot "ui")
  npm.cmd run build
  if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "UI build failed."
  }
  Pop-Location
}

# Verify PyInstaller is available
& $pythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
  & $pythonExe -m pip install pyinstaller --quiet
}

# Check if previous exe is in use
if (Test-Path $desktopExe) {
  try {
    $lockCheck = [System.IO.File]::Open($desktopExe, 'Open', 'ReadWrite', 'None')
    $lockCheck.Close()
  } catch {
    throw "Desktop executable is currently in use: $desktopExe. Close Convey Agent and run this script again."
  }
}

# --- Build ---

Write-Host ""
Write-Host "=== Building Convey Agent Desktop ===" -ForegroundColor Cyan
Write-Host "  Python:  $pythonExe"
Write-Host "  UI dist: $uiDist"
Write-Host "  Output:  $projectRoot\dist\TriConveyAgent.exe"
Write-Host ""

& $pythonExe -m PyInstaller --noconfirm "$projectRoot\desktop_app.spec"
if ($LASTEXITCODE -ne 0) {
  throw "Desktop executable build failed with exit code $LASTEXITCODE"
}

$exeSize = (Get-Item $desktopExe).Length / 1MB
Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "  Output: $desktopExe"
Write-Host "  Size:   $([math]::Round($exeSize, 1)) MB"
Write-Host ""
Write-Host "To distribute to clients, provide:" -ForegroundColor Cyan
Write-Host "  1. TriConveyAgent.exe"
Write-Host "  2. A .env file with OPENAI_API_KEY (place next to the exe)"
