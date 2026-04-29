#ifndef MyAppName
  #define MyAppName "Convey Agent"
#endif
#ifndef MyAppVersion
#define MyAppVersion "0.0.125"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Convey Agent"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "ConveyAgent.exe"
#endif
#ifndef MyAppPublisherURL
  #define MyAppPublisherURL "https://github.com"
#endif

[Setup]
AppId={{D3A0A734-4A6F-4C34-8D8A-3B0606D06E41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppPublisherURL}
AppSupportURL={#MyAppPublisherURL}
AppUpdatesURL={#MyAppPublisherURL}
DefaultDirName={autopf}\ConveyAgent
DefaultGroupName=Convey Agent
DisableProgramGroupPage=yes
OutputBaseFilename=ConveyAgent-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
#ifexist "..\public\favicon.ico"
SetupIconFile=..\public\favicon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\ConveyAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion

[Dirs]
Name: "{localappdata}\TriConveyAgent"

[Icons]
Name: "{group}\Convey Agent"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Convey Agent"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Convey Agent"; Flags: nowait postinstall skipifsilent
