; Inno Setup script for the Windows installer.  Build after PyInstaller:
;     iscc packaging\installer.iss
; Installs for the current user (no admin needed) with a Start menu shortcut.
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "2.2.0"
#endif

[Setup]
AppId={{6A0F2C61-3E7B-4F1C-9D2E-57C1B7A0E911}
AppName=Whisper Transcriber
AppVersion={#AppVersion}
AppPublisher=Victor Blanco
DefaultDirName={localappdata}\Programs\Whisper Transcriber
DefaultGroupName=Whisper Transcriber
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Whisper-Transcriber-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Whisper Transcriber.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Whisper Transcriber\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Whisper Transcriber"; Filename: "{app}\Whisper Transcriber.exe"
Name: "{userdesktop}\Whisper Transcriber"; Filename: "{app}\Whisper Transcriber.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Whisper Transcriber.exe"; Description: "Open Whisper Transcriber"; Flags: nowait postinstall
