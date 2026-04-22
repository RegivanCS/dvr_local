; ============================================================
; DVR Local — Inno Setup Script
; Gera: DVR_Local_Setup_v1.1.exe
;
; Pré-requisito para compilar:
;   Inno Setup 6+ em: https://jrsoftware.org/isinfo.php
; ============================================================

#define AppName    "DVR Local"
#define AppVersion "1.1"
#define AppPublisher "RegivanCS"
#define AppURL     "https://github.com/RegivanCS/dvr_local"
#define AppExe     "dvr_launcher.exe"

[Setup]
AppId={{7A4F2E3B-9C1D-4E5F-8B2A-3D6E7F0A1B2C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\DVR Local
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=DVR_Local_Setup_v{#AppVersion}
SetupIconFile=assets\dvr_icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Pede elevação apenas para instalar em Program Files
PrivilegesRequired=admin

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english";            MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked
Name: "startupicon"; Description: "Iniciar automaticamente com o Windows";  GroupDescription: "Atalhos:"

[Files]
; Executável gerado pelo PyInstaller
Source: "..\..\dist\dvr_launcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; ffmpeg (se existir na pasta build\windows\ffmpeg\)
#ifexist "ffmpeg"
Source: "ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\DVR Local";        Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar";      Filename: "{uninstallexe}"
Name: "{commondesktop}\DVR Local"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\DVR Local";   Filename: "{app}\{#AppExe}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Iniciar DVR Local agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM dvr_launcher.exe /T"; Flags: runhidden; RunOnceId: "KillDvr"

[Code]
function FfmpegExists: Boolean;
begin
  Result := DirExists(ExpandConstant('{src}\ffmpeg'));
end;
