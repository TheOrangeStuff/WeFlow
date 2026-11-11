; Inno Setup script for WeFlow. Built by packaging\build_windows.ps1:
;   ISCC.exe /DAppVersion=1.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif

[Setup]
AppId={{6E1B6C0B-6D0B-4C8E-9C59-5B7E2B1F0A11}
AppName=WeFlow
AppVersion={#AppVersion}
AppPublisher=WeFlow
DefaultDirName={autopf}\WeFlow
DefaultGroupName=WeFlow
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=WeFlow-Setup-{#AppVersion}
SetupIconFile=WeFlow.ico
UninstallDisplayIcon={app}\WeFlow.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\WeFlow\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\WeFlow"; Filename: "{app}\WeFlow.exe"
Name: "{autodesktop}\WeFlow"; Filename: "{app}\WeFlow.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WeFlow.exe"; Description: "Start WeFlow"; Flags: nowait postinstall skipifsilent

[Registry]
; Remove the "start at login" entry on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "WeFlow"; Flags: dontcreatekey uninsdeletevalue
