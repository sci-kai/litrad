; Inno Setup script of the litrad Windows installer.
; Installs for the current user only (no administrator rights), adds a Start
; menu entry and optionally a desktop shortcut. Uninstalling keeps your data
; (%LOCALAPPDATA%\litrad) and says so.
;
;   iscc /DAppVersion=0.3.0 /DSourceDir=C:\build\dist\litrad ^
;        /DOutputDir=C:\build\out /DOutputName=litrad-0.3.0-windows-x86_64 ^
;        packaging\windows\litrad.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\litrad"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#ifndef OutputName
  #define OutputName "litrad-" + AppVersion + "-windows-x86_64"
#endif

[Setup]
AppId={{05EC8161-C270-4A6F-817F-2014D25A8047}
AppName=litrad
AppVersion={#AppVersion}
AppVerName=litrad {#AppVersion}
AppPublisher=Kai Horny
DefaultDirName={localappdata}\Programs\litrad
DefaultGroupName=litrad
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
UninstallDisplayIcon={app}\litrad.exe
UninstallDisplayName=litrad
; an update or uninstall first closes a running litrad
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
Name: "{autoprograms}\litrad"; Filename: "{app}\litrad.exe"
Name: "{autodesktop}\litrad"; Filename: "{app}\litrad.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\litrad.exe"; Description: "{cm:LaunchProgram,litrad}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('litrad was removed. Your profiles, settings and backups are kept in '
      + ExpandConstant('{localappdata}\litrad') + '. Delete that folder yourself '
      + 'if you no longer need them; installing litrad again uses them.',
      mbInformation, MB_OK);
end;
