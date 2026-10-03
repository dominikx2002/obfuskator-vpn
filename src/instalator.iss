; Instalator Obfuskator VPN (Inno Setup 6). Buduje go pakiet.cmd:
;   ISCC /DAppVersion=2.0.1 /DSourceDir=<folder z aplikacja> /DOutDir=<gdzie zapisac> instalator.iss
; Instalacja dla biezacego uzytkownika - sam instalator nie pyta o uprawnienia administratora
; (pyta dopiero aplikacja przy pierwszym starcie, bo tworzy wirtualna karte sieciowa).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error "Podaj /DSourceDir=..."
#endif
#ifndef OutDir
  #define OutDir "."
#endif
#define AppName "Obfuskator VPN"
#define AppExe "ObfuskatorVPN.exe"

[Setup]
AppId={{986D711A-2DBC-4C49-9D8C-C893B4553A6C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Dominik Serafin
AppPublisherURL=https://github.com/dominikx2002/obfuskator-vpn
AppSupportURL=https://github.com/dominikx2002/obfuskator-vpn/issues
AppUpdatesURL=https://github.com/dominikx2002/obfuskator-vpn/releases
DefaultDirName={localappdata}\Programs\ObfuskatorVPN
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=ObfuskatorVPN-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; aplikacje (dzialajaca jako administrator) zamyka [Code] przez jej port sterowania
CloseApplications=no

[Languages]
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; stare pliki biblioteki po aktualizacji (inne wersje Pythona/bibliotek)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Uruchom {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; jako administrator: rozlacza VPN, zamyka aplikacje, usuwa zadanie harmonogramu
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall"; Flags: shellexec waituntilterminated; Verb: "runas"; RunOnceId: "Sprzatanie"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: files; Name: "{app}\settings.json"

[Code]
{ Dzialajaca aplikacja (jako administrator) - prosimy ja o zamkniecie przez port sterowania. }
procedure QuitRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "try { $c = New-Object Net.Sockets.TcpClient(''127.0.0.1'', 10855); ' +
    '$s = $c.GetStream(); $b = [Text.Encoding]::ASCII.GetBytes(\"quit`n\"); $s.Write($b, 0, $b.Length); ' +
    '$c.Close(); Start-Sleep -Seconds 4 } catch {}"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningApp();
  Result := '';
end;
