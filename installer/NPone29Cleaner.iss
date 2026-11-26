; Installateur Windows de NPone29 Cleaner (Inno Setup 6).
; Produit dist\NPone29Cleaner-Setup-<version>.exe à partir de dist\NPone29Cleaner.exe.
; Utilisation : lancez build.bat (qui compile l'exe puis cet installateur), ou
;   ISCC.exe /DAppVersion=1.3.0 installer\NPone29Cleaner.iss

#ifndef AppVersion
  #error "Passez la version : ISCC /DAppVersion=x.y.z installer\NPone29Cleaner.iss"
#endif

#define AppName "NPone29 Cleaner"
#define AppExe "NPone29Cleaner.exe"
#define BackupTask "NPone29 Cleaner - Sauvegarde"

[Setup]
; Identifiant fixe : une nouvelle version remplace l'ancienne au lieu de s'installer à côté.
AppId={{01ED4AE3-E880-45E9-ADE0-84B92C8ACB35}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=NPone29
AppPublisherURL=https://github.com/NPone29/NPone29Cleaner
AppSupportURL=https://github.com/NPone29/NPone29Cleaner/issues
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Installation de {#AppName}
; Dossier fixe (Program Files) : la sauvegarde automatique planifiée y retrouve toujours l'application.
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Installation dans Program Files pour tous les utilisateurs. L'application, elle, s'ouvre sans droits
; administrateur et propose de se relancer en administrateur pour les pilotes et les fichiers système.
PrivilegesRequired=admin
; Uniquement pour les tests automatisés (/CURRENTUSER) : l'installation normale passe par l'administrateur.
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=NPone29Cleaner-Setup-{#AppVersion}
; Ferme l'application (ou une sauvegarde automatique en cours) avant de remplacer ses fichiers.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; runasoriginaluser : l'application s'ouvre normalement, sans les droits administrateur de l'installateur.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; Mise à jour lancée depuis l'application (/SILENT /RELAUNCH=1) : la rouvrir une fois installée.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[UninstallRun]
; La sauvegarde automatique planifiée ne doit pas survivre à la désinstallation.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#BackupTask}"" /F"; Flags: runhidden; RunOnceId: "SupprimerTacheSauvegarde"

[Messages]
french.FinishedLabel=L'installation de [name] est terminée.%n%nVos réglages et vos sauvegardes sont conservés lors des mises à jour et de la désinstallation.

[Code]
// Mise à jour lancée depuis l'application : /SILENT /RELAUNCH=1 /WAITPID=<pid>,<pid>
const
  SYNCHRONIZE = $00100000;
  WAIT_SECONDS = 90;

var
  Installed: Boolean;

function OpenProcess(dwDesiredAccess: Cardinal; bInheritHandle: Boolean; dwProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function GetTickCount: Cardinal;
  external 'GetTickCount@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

// Attend (au plus WAIT_SECONDS en tout) que l'application qui a lancé la mise à jour soit fermée.
procedure WaitForApplication;
var
  Pids: String;
  Pid, Deadline, Now: Cardinal;
  P: Integer;
  H: THandle;
begin
  Deadline := GetTickCount + WAIT_SECONDS * 1000;
  Pids := ExpandConstant('{param:WAITPID|}') + ',';
  while Pids <> '' do
  begin
    P := Pos(',', Pids);
    Pid := StrToIntDef(Trim(Copy(Pids, 1, P - 1)), 0);
    Delete(Pids, 1, P);
    if Pid <> 0 then
    begin
      H := OpenProcess(SYNCHRONIZE, False, Pid);
      if H <> 0 then
      begin
        Log(Format('Attente de la fermeture de l''application (processus %d)', [Pid]));
        Now := GetTickCount;
        if Now < Deadline then
          WaitForSingleObject(H, Deadline - Now);
        CloseHandle(H);
      end;
    end;
  end;
end;

function InitializeSetup: Boolean;
begin
  WaitForApplication;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Installed := True;
end;

// Mise à jour impossible (fichier bloqué, annulation…) : on rouvre l'ancienne version, toujours en place.
procedure DeinitializeSetup;
var
  Code: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  if ShouldRelaunch and (not Installed) and FileExists(Exe) then
  begin
    Log('Mise à jour non effectuée : relance de la version actuelle.');
    ExecAsOriginalUser(Exe, '', '', SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;
