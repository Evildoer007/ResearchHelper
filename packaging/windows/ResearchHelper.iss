#define MyAppName "Research Helper"
#define MyAppVersion "0.9.0"
#define MyAppPublisher "Research Helper"
#define MyAppExeName "ResearchHelper.exe"

[Setup]
AppId={{7B9ECF35-92A7-48AE-95E1-B0FAE7EBF611}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Research Helper
DefaultGroupName=Research Helper
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=ResearchHelper-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\..\dist\ResearchHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Research Helper"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Research Helper"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Research Helper"; Flags: nowait postinstall skipifsilent
