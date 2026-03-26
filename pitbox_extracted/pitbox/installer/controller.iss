; Inno Setup Script for PitBox Controller
; Fastest Lap Racing Lounge - Admin PC Controller Installer
; Version from version.ini (single source of truth with pitbox_common/version.py)

#define AppName "PitBox Controller"
#define AppVersion ReadIni(AddBackslash(SourcePath) + "..\version.ini", "Version", "Version", "0.1.0")
#define AppPublisher "Fastest Lap Racing Lounge"
#define AppURL "https://fastestlap.racing"
#define AppExeName "PitBoxController.exe"
#define ControllerPort "9630"

[Setup]
; Stable AppId - never change for upgrades
AppId={{7E6B9D4C-8A3F-4B2E-A5D7-2F8C6B1E9A4D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName=C:\PitBox\Controller
DefaultGroupName=PitBox
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.txt
OutputDir=..\dist
OutputBaseFilename=PitBoxControllerSetup_{#AppVersion}
; Uncomment when controller_icon.ico is in installer folder: SetupIconFile=controller_icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewallrule"; Description: "Add Windows Firewall rule for Controller UI (port {#ControllerPort}) - only needed if allow_lan_ui=true"; GroupDescription: "Network Configuration:"; Flags: unchecked
Name: "autostart"; Description: "Launch PitBox Controller at startup"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\PitBoxController.exe"; DestDir: "{app}"; Flags: ignoreversion
; NSSM for service management - uncomment when nssm.exe is in ..\tools\
; Source: "..\tools\nssm.exe"; DestDir: "{app}\bin"; Flags: ignoreversion

[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "Start PitBox Controller Web UI"
Name: "{group}\Configure Controller"; Filename: "notepad.exe"; Parameters: """{app}\config\controller_config.json"""; Comment: "Edit Controller configuration"
Name: "{group}\View Controller Logs"; Filename: "{app}\logs"; Comment: "Open logs folder"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{commonstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: autostart

[Run]
; Initialize config on first install (only if config doesn't exist)
Filename: "{app}\{#AppExeName}"; Parameters: "--init"; Flags: runhidden; StatusMsg: "Creating default configuration..."; Check: not FileExists(ExpandConstant('{app}\config\controller_config.json'))
; Add firewall rule (optional)
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""PitBox Controller UI"" dir=in action=allow protocol=TCP localport={#ControllerPort}"; Flags: runhidden; StatusMsg: "Adding Windows Firewall rule..."; Tasks: firewallrule
; Optionally launch controller
Filename: "{app}\{#AppExeName}"; Description: "Launch PitBox Controller now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Remove firewall rule on uninstall
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""PitBox Controller UI"""; Flags: runhidden

[Code]
var
  ResultPage: TOutputMsgWizardPage;

function ServiceExists(ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Exec('sc.exe', 'query "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0);
end;

procedure StopService(ServiceName: String);
var
  ResultCode: Integer;
begin
  if ServiceExists(ServiceName) then
  begin
    Log('Stopping service: ' + ServiceName);
    Exec('net.exe', 'stop "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(2000);
  end;
end;

function StartService(ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Log('Starting service: ' + ServiceName);
  Result := Exec('net.exe', 'start "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if Result then
    Sleep(2000);
end;

procedure InitializeWizard;
begin
  ResultPage := CreateOutputMsgPage(wpInfoAfter,
    'Installation Complete',
    'PitBox Controller has been installed.',
    'Web UI: http://127.0.0.1:9630' + #13#10 + #13#10 +
    'Turn on Enrollment Mode in the Web UI to add sim rigs.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    StopService('PitBoxController');
  end;
  if CurStep = ssPostInstall then
  begin
    if ServiceExists('PitBoxController') then
      StartService('PitBoxController');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  NssmPath: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService('PitBoxController');
    if ServiceExists('PitBoxController') then
    begin
      NssmPath := ExpandConstant('{app}\bin\nssm.exe');
      if FileExists(NssmPath) then
        Exec(NssmPath, 'remove PitBoxController confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
      else
        Exec('sc.exe', 'delete PitBoxController', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;
