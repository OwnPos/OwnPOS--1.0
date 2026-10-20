; Inno Setup 6.4 or newer: turns dist\OwnPOS\ into ONE installer file, OwnPOS-Setup-x.y.z.exe
; Build: build_exe.bat does everything (it asks for the installer password and passes it in).
;
; PASSWORD PROTECTION: the installer asks for a password before it installs, and its files are encrypted
; (Encryption=yes: XChaCha20 with a key derived from the password), so nobody can unpack the program without
; it. The password is NEVER written in this file: build_exe.bat passes it with /DInstallPassword=...
; If it is missing, the build stops.
;
; What the installer does:
; - puts the program in C:\Program Files\OwnPOS;
; - adds ONE desktop shortcut "OwnPOS";
; - registers OwnPOS in Windows Settings > Apps for clean updates and uninstall;
; - never touches store data: that is in the PostgreSQL database on the server, and each PC's own settings
;   (connection, station number) are in %LOCALAPPDATA%\OwnPOS, which install, update and uninstall leave alone.

#define MyAppName "OwnPOS"
#define MyAppExe "OwnPOS.exe"
#include "version.iss"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef InstallPassword
  #error The installer password is missing: run build_exe.bat (or ISCC /DInstallPassword=...)
#endif

[Setup]
; the AppId must never change: it is how Windows knows a new version replaces the old one.
; It is different from Pasal POS's, so the two programs never replace each other.
AppId={{8C1F4A62-5D3B-4E97-B0A4-2F7E91C6D358}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Aayush Niroula
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
DefaultDirName={autopf}\OwnPOS
DisableDirPage=auto
DisableProgramGroupPage=yes
CreateAppDir=yes
OutputDir=..\dist\installer
OutputBaseFilename=OwnPOS-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
SetupIconFile=..\ownpos\ui\assets\ownpos.ico
Password={#InstallPassword}
Encryption=yes

[Files]
Source: "..\dist\OwnPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\{#MyAppExe}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent runasoriginaluser
