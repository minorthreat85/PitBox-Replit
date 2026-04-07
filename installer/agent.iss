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
; PitBox Agent binary
Source: "..\dist\PitBoxAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
; Mumble 1.3.4 MSI bundled for offline silent install
Source: "assets\mumble-1.3.4.msi"; DestDir: "{app}\bin"; Flags: ignoreversion

[Dirs]
Name: "{app}\bin"
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

// ---------------------------------------------------------------------------
// Mumble detection
// ---------------------------------------------------------------------------

function IsMumbleInstalled: Boolean;
var
  DisplayVersion: String;
begin
  Result := False;

  // Primary check: 32-bit uninstall key (Mumble 1.3.4 is a 32-bit installer)
  if RegQueryStringValue(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Mumble',
      'DisplayVersion', DisplayVersion) then
  begin
    Log('Mumble registry (WOW6432Node) DisplayVersion: ' + DisplayVersion);
    if Pos('1.3.4', DisplayVersion) > 0 then
    begin
      Log('Mumble 1.3.4 already installed (WOW6432Node registry).');
      Result := True;
      Exit;
    end;
  end;

  // Secondary check: native 64-bit uninstall key
  if RegQueryStringValue(HKLM,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Mumble',
      'DisplayVersion', DisplayVersion) then
  begin
    Log('Mumble registry (native) DisplayVersion: ' + DisplayVersion);
    if Pos('1.3.4', DisplayVersion) > 0 then
    begin
      Log('Mumble 1.3.4 already installed (native registry).');
      Result := True;
      Exit;
    end;
  end;

  // Fallback: check executable presence
  if FileExists('C:\Program Files (x86)\Mumble\mumble.exe') then
  begin
    Log('Mumble exe found at C:\Program Files (x86)\Mumble\mumble.exe — treating as installed.');
    Result := True;
    Exit;
  end;

  Log('Mumble 1.3.4 not detected on this machine.');
end;

// ---------------------------------------------------------------------------
// Mumble silent install
// ---------------------------------------------------------------------------

function InstallMumble: Boolean;
var
  MsiPath: String;
  ResultCode: Integer;
begin
  Result := True;

  if IsMumbleInstalled then
  begin
    Log('Skipping Mumble install — already present.');
    Exit;
  end;

  MsiPath := ExpandConstant('{app}\bin\mumble-1.3.4.msi');
  Log('Mumble MSI path: ' + MsiPath);

  if not FileExists(MsiPath) then
  begin
    Log('ERROR: Bundled Mumble MSI not found at ' + MsiPath);
    MsgBox(
      'Bundled Mumble MSI is missing: ' + MsiPath + #13#10 +
      'Mumble voice comms will not be available. Please reinstall PitBox Agent.',
      mbError, MB_OK);
    Result := False;
    Exit;
  end;

  Log('Starting silent Mumble 1.3.4 install...');
  if not Exec('msiexec.exe',
              '/i "' + MsiPath + '" /qn /norestart',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('ERROR: Failed to launch msiexec for Mumble install.');
    MsgBox(
      'Failed to start the Mumble installer.' + #13#10 +
      'Mumble voice comms will not be available.',
      mbError, MB_OK);
    Result := False;
    Exit;
  end;

  Log('msiexec exit code: ' + IntToStr(ResultCode));

  // 0 = success, 3010 = success + reboot required (acceptable for LAN deployment)
  if (ResultCode = 0) or (ResultCode = 3010) then
  begin
    Log('Mumble 1.3.4 installed successfully (exit code ' + IntToStr(ResultCode) + ').');
    if ResultCode = 3010 then
      Log('Note: A reboot may be required to complete the Mumble install.');
    Result := True;
  end
  else
  begin
    Log('ERROR: Mumble MSI returned non-zero exit code: ' + IntToStr(ResultCode));
    MsgBox(
      'Mumble installation failed with exit code ' + IntToStr(ResultCode) + '.' + #13#10 +
      'Mumble voice comms may not be available. You can install Mumble 1.3.4 manually later.',
      mbError, MB_OK);
    Result := False;
  end;
end;

// ---------------------------------------------------------------------------
// Service helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Wizard hooks
// ---------------------------------------------------------------------------

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
    // Install Mumble before starting PitBox Agent
    Log('--- Mumble pre-check ---');
    InstallMumble;
    Log('--- Mumble pre-check done ---');

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
