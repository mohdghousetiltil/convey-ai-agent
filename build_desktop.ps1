Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$uiDist = Join-Path $projectRoot "ui\dist"
$uiDistIndex = Join-Path $uiDist "index.html"
$desktopExe = Join-Path $projectRoot "dist\ConveyAgent.exe"
$buildCacheDir = Join-Path $projectRoot ".cache\build"
$versionInfoPath = Join-Path $buildCacheDir "ConveyAgent.version.txt"
$iconPath = Join-Path $projectRoot "public\favicon.ico"

function Get-ProjectVersion {
  $pyprojectPath = Join-Path $projectRoot "pyproject.toml"
  $match = Select-String -Path $pyprojectPath -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if (-not $match) {
    throw "Could not find project version in $pyprojectPath"
  }
  return $match.Matches[0].Groups[1].Value
}

function Find-PythonExe {
  $candidates = @(
    $env:TRICONVEY_PYTHON_EXE,
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv-1\Scripts\python.exe")
  ) | Where-Object { $_ -and (Test-Path $_) }

  foreach ($candidate in $candidates) {
    try {
      & $candidate -c "import sys; print(sys.version)" | Out-Null
      if ($LASTEXITCODE -eq 0) {
        return $candidate
      }
    } catch {
      continue
    }
  }

  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  throw "Could not find a working Python executable. Set TRICONVEY_PYTHON_EXE or create a working .venv."
}

function New-VersionInfoFile {
  param(
    [string]$Version,
    [string]$Publisher,
    [string]$OutputPath
  )

  $versionParts = ($Version.Split(".") + @("0", "0", "0", "0"))[0..3]
  $fileVersion = ($versionParts -join ", ")
  $productVersion = ($versionParts -join ", ")
  $content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($fileVersion),
    prodvers=($productVersion),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', '$Publisher'),
            StringStruct('FileDescription', 'Convey Agent Desktop App'),
            StringStruct('FileVersion', '$Version'),
            StringStruct('InternalName', 'ConveyAgent'),
            StringStruct('OriginalFilename', 'ConveyAgent.exe'),
            StringStruct('ProductName', 'Convey Agent'),
            StringStruct('ProductVersion', '$Version')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
  Set-Content -Path $OutputPath -Value $content -Encoding UTF8
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

$pythonExe = Find-PythonExe
$projectVersion = Get-ProjectVersion
$publisher = if ($env:TRICONVEY_APP_PUBLISHER) { $env:TRICONVEY_APP_PUBLISHER } else { "TriConvey Agent" }

Write-Host "Building UI bundle..." -ForegroundColor Yellow
Push-Location (Join-Path $projectRoot "ui")
npm.cmd run build
if ($LASTEXITCODE -ne 0) {
  Pop-Location
  throw "UI build failed."
}
Pop-Location

New-Item -ItemType Directory -Force -Path $buildCacheDir | Out-Null
New-VersionInfoFile -Version $projectVersion -Publisher $publisher -OutputPath $versionInfoPath

& $pythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
  & $pythonExe -m pip install pyinstaller --quiet
}

if (Test-Path $desktopExe) {
  try {
    $lockCheck = [System.IO.File]::Open($desktopExe, 'Open', 'ReadWrite', 'None')
    $lockCheck.Close()
  } catch {
    throw "Desktop executable is currently in use: $desktopExe. Close Convey Agent and run this script again."
  }
}

$env:TRICONVEY_VERSION_FILE = $versionInfoPath
if (Test-Path $iconPath) {
  $env:TRICONVEY_APP_ICON = $iconPath
} else {
  Remove-Item Env:TRICONVEY_APP_ICON -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== Building Convey Agent Desktop ===" -ForegroundColor Cyan
Write-Host "  Python:   $pythonExe"
Write-Host "  Version:  $projectVersion"
Write-Host "  UI dist:  $uiDist"
Write-Host "  Output:   $desktopExe"
Write-Host ""

& $pythonExe -m PyInstaller --noconfirm "$projectRoot\desktop_app.spec"
if ($LASTEXITCODE -ne 0) {
  throw "Desktop executable build failed with exit code $LASTEXITCODE"
}

Invoke-OptionalCodeSign -TargetPath $desktopExe

$exeSize = (Get-Item $desktopExe).Length / 1MB
Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "  Output:  $desktopExe"
Write-Host "  Version: $projectVersion"
Write-Host "  Size:    $([math]::Round($exeSize, 1)) MB"
Write-Host ""
Write-Host "Client data stays in %LOCALAPPDATA%\TriConveyAgent so updates do not wipe settings." -ForegroundColor Cyan
