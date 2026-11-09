; Inno Setup script of the litresearch Windows installer.
; Installs for the current user only (no administrator rights), adds a Start
; menu entry and optionally a desktop shortcut. Uninstalling keeps your data
; (%LOCALAPPDATA%\litresearch) and says so.
;
;   iscc /DAppVersion=0.3.0 /DSourceDir=C:\build\dist\litresearch ^
;        /DOutputDir=C:\build\out /DOutputName=litresearch-0.3.0-windows-x86_64 ^
;        packaging\windows\litresearch.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\litresearch"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#ifndef OutputName
  #define OutputName "litresearch-" + AppVersion + "-windows-x86_64"
#endif

[Setup]
AppId={{05EC8161-C270-4A6F-817F-2014D25A8047}
AppName=litresearch
AppVersion={#AppVersion}
AppVerName=litresearch {#AppVersion}
AppPublisher=Kai Horny
DefaultDirName={localappdata}\Programs\litresearch
DefaultGroupName=litresearch
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
LicenseFile={#SourcePath}\..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\litresearch.exe
UninstallDisplayName=litresearch
; an update or uninstall first closes a running litresearch
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; an update replaces the program files completely (never touches your data)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\litresearch"; Filename: "{app}\litresearch.exe"
Name: "{autodesktop}\litresearch"; Filename: "{app}\litresearch.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\litresearch.exe"; Description: "{cm:LaunchProgram,litresearch}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('litresearch was removed. Your profiles, settings and backups are kept in '
      + ExpandConstant('{localappdata}\litresearch') + '. Delete that folder yourself '
      + 'if you no longer need them; installing litresearch again uses them.',
      mbInformation, MB_OK);
end;
