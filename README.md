# Convey Agent
# Download Here
https://github.com/mohdghousetiltil/convey-ai-agent/releases/download/v0.1.0/ConveyAgent-Setup-0.1.0.exe

Convey Agent is a Windows desktop app for Section 32 review, AI-assisted document analysis, and TriConvey autofill.

## Windows Desktop Model

The packaged app is a local desktop install:

- the installer places `TriConveyAgent.exe` in `Program Files`
- the app starts its own local backend on the client machine
- durable data lives in `%LOCALAPPDATA%\TriConveyAgent`
- updates replace the installed app without wiping `.env`, settings, or the local database

What clients do:

- download `ConveyAgent-Setup-<version>.exe`
- double-click it
- click through setup
- sign in and finish setup inside the app

What clients do not need to do:

- install Python
- install Node.js or npm packages
- clone the repository
- create a virtual environment
- run `pip install`
- edit `.env` by hand

Durable client data:

- `%LOCALAPPDATA%\TriConveyAgent\.env`
- `%LOCALAPPDATA%\TriConveyAgent\config\settings.json`
- `%LOCALAPPDATA%\TriConveyAgent\convey.db`
- `%LOCALAPPDATA%\TriConveyAgent\updates\`

## Build The Desktop App

From the repo root:

```powershell
.\build_desktop.ps1
```

This builds:

```text
dist\TriConveyAgent.exe
```

The build script now:

- reads the app version from `pyproject.toml`
- injects Windows version metadata into the exe
- uses `installer\TriConveyAgent.ico` automatically when present
- optionally code-signs the exe if signing environment variables are set

## Build The Windows Installer

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) on the build machine, then run:

```powershell
.\build_installer.ps1
```

This builds a versioned installer:

```text
dist\installer\ConveyAgent-Setup-<version>.exe
```

The installer:

- installs to `C:\Program Files\TriConveyAgent`
- creates Start Menu shortcut
- optionally creates Desktop shortcut
- upgrades existing installs in place
- closes the running app during update when needed
- keeps `%LOCALAPPDATA%\TriConveyAgent` untouched

On first sign-in, the app can collect setup values directly in the UI:

- OpenAI API key
- Anthropic API key
- preferred AI provider and model
- TriConvey executable path

That means you can ship one installer to clients and let them finish setup from Settings instead of asking them to edit files.

## Auto-Update Flow

The desktop app can check GitHub Releases and notify clients when a new installer is available.

The release repository is managed internally in the app build.

Optional settings:

- `Automatically check for updates on startup`
- `Include prerelease builds`

Update behaviour:

1. App checks GitHub Releases.
2. If a newer version exists, the user sees `Update Available`.
3. Clicking `Download and install` downloads the installer into `%LOCALAPPDATA%\TriConveyAgent\updates\`.
4. The app launches the installer and closes itself.
5. Inno Setup replaces the installed app in place.

## Code Signing

Code signing is strongly recommended for client distribution.

Benefits:

- fewer SmartScreen warnings
- more trust during install and update
- better experience for non-technical clients

Optional signing environment variables:

```powershell
$env:TRICONVEY_SIGN_CERT_FILE="C:\path\certificate.pfx"
$env:TRICONVEY_SIGN_CERT_PASSWORD="your-password"
$env:TRICONVEY_SIGNTOOL_PATH="C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe"
$env:TRICONVEY_TIMESTAMP_URL="http://timestamp.digicert.com"
```

Optional branding variables:

```powershell
$env:TRICONVEY_APP_PUBLISHER="Your Company Name"
$env:TRICONVEY_APP_PUBLISHER_URL="https://github.com/your-org/triconvey-agent"
```

If `installer\TriConveyAgent.ico` exists, both the desktop build and installer will use it.

## Release Workflow

Recommended release flow:

1. Bump `version` in `pyproject.toml`
2. Commit and push
3. Build `.\build_desktop.ps1`
4. Build `.\build_installer.ps1`
5. Publish the versioned installer to GitHub Releases
6. Clients receive in-app update notification

## Environment File

Installed builds should keep API keys in:

```text
%LOCALAPPDATA%\TriConveyAgent\.env
```

The app writes these values for the installed desktop app automatically. Clients should not need to edit this file manually.

Example:

```env
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

The desktop launcher checks `%LOCALAPPDATA%\TriConveyAgent\.env` before any `.env` next to the exe so updates do not wipe client configuration.
