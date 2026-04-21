Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopExe = Join-Path $projectRoot "dist\TriConveyAgent.exe"
$issFile = Join-Path $projectRoot "installer\TriConveyAgent.iss"
$installerOutDir = Join-Path $projectRoot "dist\installer"

function Get-ProjectVersion {
  $pyprojectPath = Join-Path $projectRoot "pyproject.toml"
  $match = Select-String -Path $pyprojectPath -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if (-not $match) {
    throw "Could not find project version in $pyprojectPath"
  }
  return $match.Matches[0].Groups[1].Value
}

function Find-InnoCompiler {
  $candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return $candidate
    }
  }
  $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  return $null
}

function Find-SignTool {
  if ($env:TRICONVEY_SIGNTOOL_PATH -and (Test-Path $env:TRICONVEY_SIGNTOOL_PATH)) {
    return $env:TRICONVEY_SIGNTOOL_PATH
  }
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  return $null
}

function Invoke-OptionalCodeSign {
  param([string]$TargetPath)

  $signtool = Find-SignTool
  if (-not $signtool) {
    return
  }
  if (-not $env:TRICONVEY_SIGN_CERT_FILE) {
    return
  }

  $timestampUrl = if ($env:TRICONVEY_TIMESTAMP_URL) { $env:TRICONVEY_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
  $arguments = @(
    "sign",
    "/fd", "SHA256",
    "/td", "SHA256",
    "/tr", $timestampUrl,
    "/f", $env:TRICONVEY_SIGN_CERT_FILE
  )
  if ($env:TRICONVEY_SIGN_CERT_PASSWORD) {
    $arguments += @("/p", $env:TRICONVEY_SIGN_CERT_PASSWORD)
  }
  $arguments += $TargetPath

  & $signtool @arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Code signing failed for $TargetPath"
  }
}

$projectVersion = Get-ProjectVersion
$publisher = if ($env:TRICONVEY_APP_PUBLISHER) { $env:TRICONVEY_APP_PUBLISHER } else { "TriConvey Agent" }
$publisherUrl = if ($env:TRICONVEY_APP_PUBLISHER_URL) { $env:TRICONVEY_APP_PUBLISHER_URL } else { "https://github.com" }

Write-Host ""
Write-Host "=== Building TriConvey Agent Installer ===" -ForegroundColor Cyan
Write-Host "  Version: $projectVersion"
Write-Host ""

if (-not (Test-Path $issFile)) {
  throw "Installer definition not found: $issFile"
}

if (-not (Test-Path $desktopExe)) {
  Write-Host "Desktop exe not found. Building desktop app first..." -ForegroundColor Yellow
  & (Join-Path $projectRoot "build_desktop.ps1")
}

$iscc = Find-InnoCompiler
if (-not $iscc) {
  throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6 first."
}

New-Item -ItemType Directory -Force -Path $installerOutDir | Out-Null

& $iscc "/O$installerOutDir" "/DMyAppVersion=$projectVersion" "/DMyAppPublisher=$publisher" "/DMyAppPublisherURL=$publisherUrl" $issFile
if ($LASTEXITCODE -ne 0) {
  throw "Installer build failed with exit code $LASTEXITCODE"
}

$installerPath = Join-Path $installerOutDir "TriConveyAgent-Setup-$projectVersion.exe"
if (-not (Test-Path $installerPath)) {
  $installerPath = Get-ChildItem -Path $installerOutDir -Filter "TriConveyAgent-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { $_.FullName }
}
if (-not $installerPath) {
  throw "Installer build finished but no installer executable was found in $installerOutDir"
}

Invoke-OptionalCodeSign -TargetPath $installerPath

Write-Host ""
Write-Host "Installer build complete." -ForegroundColor Green
Write-Host "  Output folder: $installerOutDir"
Write-Host "  Installer:     $installerPath"
