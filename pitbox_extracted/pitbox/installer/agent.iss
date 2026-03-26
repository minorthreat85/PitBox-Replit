; Inno Setup Script for PitBox Agent
; Fastest Lap Racing Lounge - Sim PC Agent Installer
; Version from version.ini (single source of truth with pitbox_common/version.py)

#define AppName "PitBox Agent"
#define AppVersion ReadIni(AddBackslash(SourcePath) + "..\version.ini", "Version", "Version", "0.1.0")
#define AppPublisher "Fastest Lap Racing Lounge"
#define AppURL "https://fastestlap.racing"
#define AppExeName "PitBoxAgent.exe"
#define AgentPort "9631-9638"

[Setup]
; Stable AppId - never change for upgrades
AppId={{8F7A2E3B-9C5D-4A1E-B6F8-3D9E7C2A1F5B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName=C:\PitBox\Agent
DefaultGroupName=PitBox
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.txt
OutputDir=..\dist
OutputBaseFilename=PitBoxAgentSetup_{#AppVersion}
; Uncomment when agent_icon.ico is in installer folder: SetupIconFile=agent_icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardBackColor=#000000

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "firewallrule"; Description: "Add Windows Firewall rule for Agent (ports {#AgentPort})"; GroupDescription: "Network Configuration:"; Flags: checkedonce

[Files]
Source: "..\dist\PitBoxAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
; Note: Add icon files when available

[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\presets\steering"; Permissions: users-modify
Name: "{app}\presets\assists"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "Start PitBox Agent"
Name: "{group}\Configure Agent"; Filename: "notepad.exe"; Parameters: """{app}\config\agent_config.json"""; Comment: "Edit Agent configuration"
Name: "{group}\View Agent Logs"; Filename: "{app}\logs"; Comment: "Open logs folder"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Initialize config on first install (only if config doesn't exist)
Filename: "{app}\{#AppExeName}"; Parameters: "--init"; Flags: runhidden; StatusMsg: "Creating default configuration..."; Check: not FileExists(ExpandConstant('{app}\config\agent_config.json'))
; Add firewall rule
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""PitBox Agent"" dir=in action=allow protocol=TCP localport={#AgentPort}"; Flags: runhidden; StatusMsg: "Adding Windows Firewall rule..."; Tasks: firewallrule
[UninstallRun]
; Remove firewall rule on uninstall
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""PitBox Agent"""; Flags: runhidden

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
    'PitBox Agent has been installed.',
    'To add this rig to the controller, turn on Enrollment Mode in the controller Web UI, then start PitBox Agent.');
  WizardForm.Color := $000000;
  if WizardForm.MainPanel <> nil then WizardForm.MainPanel.Color := $000000;
  if WizardForm.InnerPage <> nil then WizardForm.InnerPage.Color := $000000;
  if WizardForm.WelcomePage <> nil then WizardForm.WelcomePage.Color := $000000;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    StopService('PitBoxAgent');
  end;
  if CurStep = ssPostInstall then
  begin
    if ServiceExists('PitBoxAgent') then
      StartService('PitBoxAgent');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  NssmPath: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService('PitBoxAgent');
    if ServiceExists('PitBoxAgent') then
    begin
      NssmPath := ExpandConstant('{app}\bin\nssm.exe');
      if FileExists(NssmPath) then
        Exec(NssmPath, 'remove PitBoxAgent confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
      else
        Exec('sc.exe', 'delete PitBoxAgent', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;
