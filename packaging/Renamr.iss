#define MyAppName "Renamr"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.6"
#endif
#define MyAppPublisher "Renamr"
#define MyAppExeName "Renamr.exe"

#ifndef SetupIconPath
  #define SetupIconPath "..\assets\Renamr.ico"
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\Renamr"
#endif

#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "Renamr-Setup-x64"
#endif

[Setup]
AppId={{A8CA8821-84DF-4430-857D-1774F485AFA8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Renamr
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousGroup=no
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#SetupIconPath}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} installer
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove the previous branded executable and shortcuts during an in-place
; upgrade. The AppId intentionally remains unchanged to preserve upgrade state.
Type: files; Name: "{app}\SequenceRenamer.exe"
Type: files; Name: "{userprograms}\Sequence Renamer\Sequence Renamer.lnk"
Type: dirifempty; Name: "{userprograms}\Sequence Renamer"
Type: files; Name: "{autodesktop}\Sequence Renamer.lnk"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
