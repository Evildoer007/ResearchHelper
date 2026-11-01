; Inno Setup 6.3+. Payloads have passed source and capability checks before compilation.
[Setup]
AppId=ResearchHelper.Desktop
AppName=ResearchHelper
AppVersion={#AppVersion}
VersionInfoVersion={#AppNumericVersion}
AppPublisher=ResearchHelper
DefaultDirName={localappdata}\Programs\ResearchHelper
DefaultGroupName=ResearchHelper
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableDirPage=yes
OutputDir={#SetupOutput}
OutputBaseFilename={#SetupName}
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\ResearchHelper.exe
Compression=lzma2
SolidCompression=yes
Uninstallable=NormalInstall
CreateUninstallRegKey=NormalInstall
UsePreviousAppDir=no
UsePreviousGroup=no
UsePreviousTasks=no
UsePreviousLanguage=no
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Check: NormalInstall

[Icons]
Name: "{userprograms}\ResearchHelper"; Filename: "{app}\ResearchHelper.exe"; WorkingDir: "{app}"; Check: NormalInstall
Name: "{userdesktop}\ResearchHelper"; Filename: "{app}\ResearchHelper.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: NormalInstall

[Run]
Filename: "{app}\ResearchHelper.exe"; Description: "启动ResearchHelper"; Flags: nowait postinstall skipifsilent; Check: NormalInstall

[Code]
function NormalInstall: Boolean;
begin
  Result := ExpandConstant('{param:VERIFYINSTALL|0}') <> '1';
end;
