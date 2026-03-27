; PitBox Unified Installer
; Supports Agent, Controller, or Both roles
; Includes NSSM, automatic service installation, firewall rules

#define MyAppName "PitBox"
#define MyAppVersion ReadIni(AddBackslash(SourcePath) + "..\version.ini", "Version", "Version", "0.1.0")
#define MyAppPublisher "Fastest Lap"
#define MyAppURL "https://github.com/minorthreat85/PitBox"

[Setup]
AppId={{F8E9A3C1-4B2D-4E7F-9A1C-8D5E6F7A8B9C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName=C:\PitBox
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
; Versioned filename so GitHub Releases and update_pitbox.ps1 can distribute updates (e.g. PitBoxInstaller_1.4.1.exe)
OutputBaseFilename=PitBoxInstaller_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\bin\PitBoxAgent.exe
; Black/white/red theme. Uncomment and add agent_icon.ico to installer folder for custom icon:
; SetupIconFile=agent_icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "agent"; Description: "Sim PC (Agent)"
Name: "controller"; Description: "Admin PC (Controller)"
Name: "both"; Description: "Both Agent and Controller (same machine)"

[Components]
Name: "agent"; Description: "PitBox Agent"; Types: agent both
Name: "controller"; Description: "PitBox Controller"; Types: controller both
Name: "nssm"; Description: "NSSM (Service Manager)"; Types: agent controller both; Flags: fixed

[Tasks]
Name: "startupagent"; Description: "Start Agent automatically on user login (Scheduled Task - runs as logged-in user, NOT as service)"; Components: agent; Flags: checkedonce
Name: "firewallagent"; Description: "Add Windows Firewall rule for Agent (ports 9631-9638)"; Components: agent; Flags: checkedonce
Name: "openbrowser"; Description: "Open Web UI after installation"; Components: controller; Flags: checkedonce

[Files]
; Executables
Source: "..\dist\PitBoxAgent.exe"; DestDir: "{app}"; Components: agent; Flags: ignoreversion
Source: "..\dist\PitBoxController.exe"; DestDir: "{app}"; Components: controller; Flags: ignoreversion

; VERSION.txt (for update checks)
Source: "..\dist\VERSION.txt"; DestDir: "{app}"; Flags: ignoreversion

; START/STOP scripts
Source: "..\dist\START.cmd"; DestDir: "{app}"; Components: controller; Flags: ignoreversion
Source: "..\dist\STOP.cmd"; DestDir: "{app}"; Components: controller; Flags: ignoreversion

; PitBoxUpdater.exe (installer-based updater; include only if built - allows unified installer to build even if PitBoxUpdater failed)
#ifexist "..\dist\PitBoxUpdater.exe"
Source: "..\dist\PitBoxUpdater.exe"; DestDir: "{app}\updater"; Components: agent controller; Flags: ignoreversion
#endif

; PitBoxTray.exe (system tray launcher; optional - skip if not built)
#ifexist "..\dist\PitBoxTray.exe"
Source: "..\dist\PitBoxTray.exe"; DestDir: "{app}"; Components: controller; Flags: ignoreversion
#endif

; PitBox icon (for shortcuts and taskbar)
#ifexist "..\assets\pitbox.ico"
Source: "..\assets\pitbox.ico"; DestDir: "{app}"; Components: controller; Flags: ignoreversion
#endif

; Updater script (fallback for Settings -> Updates "Download update & restart"; must be at {app}\tools\update_pitbox.ps1)
Source: "..\dist\tools\update_pitbox.ps1"; DestDir: "{app}\tools"; Components: controller; Flags: ignoreversion

; NSSM (bundled)
Source: "..\tools\nssm.exe"; DestDir: "{app}\tools"; Components: nssm; Flags: ignoreversion

; Example configs (for reference only, not installed by default)
Source: "..\examples\agent_config.Sim1.json"; DestDir: "{app}\examples"; Components: agent; Flags: ignoreversion
Source: "..\examples\controller_config.json"; DestDir: "{app}\examples"; Components: controller; Flags: ignoreversion

; Agent folder: config (with latest agent_config.json) and logs (Dirs creates empty logs)
Source: "..\dist\Agent\config\agent_config.json"; DestDir: "{app}\Agent\config"; Components: agent; Flags: ignoreversion

[Dirs]
Name: "{app}"
Name: "{app}\updater"
Name: "{app}\tools"
Name: "{app}\examples"
Name: "{app}\downloads"
Name: "{app}\Agent\config"
Name: "{app}\Agent\logs"
Name: "C:\ProgramData\PitBox"
Name: "C:\ProgramData\PitBox\logs"

[Icons]
; Start Menu shortcuts
Name: "{group}\PitBox"; Filename: "{app}\PitBoxTray.exe"; IconFilename: "{app}\pitbox.ico"; Components: controller
Name: "{group}\PitBox Web UI"; Filename: "http://pitbox:9630"; IconFilename: "{app}\pitbox.ico"; Components: controller
Name: "{group}\Start PitBox"; Filename: "{app}\START.cmd"; WorkingDir: "{app}"; Components: controller
Name: "{group}\Stop PitBox"; Filename: "{app}\STOP.cmd"; WorkingDir: "{app}"; Components: controller
Name: "{group}\Controller Logs"; Filename: "C:\ProgramData\PitBox\logs"; Components: controller
Name: "{group}\Update PitBox"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\tools\update_pitbox.ps1"""; WorkingDir: "{app}\tools"; Components: controller
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop shortcuts
Name: "{commondesktop}\PitBox"; Filename: "{app}\PitBoxTray.exe"; IconFilename: "{app}\pitbox.ico"; Components: controller; Tasks: openbrowser
Name: "{commondesktop}\Stop PitBox"; Filename: "{app}\STOP.cmd"; WorkingDir: "{app}"; Components: controller

; NOTE: Agent auto-start is via Scheduled Task (created in [Code]), NOT Startup folder.
; This ensures Agent runs as logged-in user - critical for AC to show game window.

[Registry]
; Register PitBoxTray to start with Windows (controller PC only)
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PitBox"; ValueData: """{app}\PitBoxTray.exe"""; Components: controller; Flags: uninsdeletevalue

[Run]
; Note: Service installation and config creation happens in [Code] section
; This section is for optional post-install actions

[Code]
var
  ControllerServiceInstalled: Boolean;

// Helper: Execute command and wait
function ExecAndWait(const Filename, Params, WorkingDir: String; ShowCmd: Integer; Wait: TExecWait): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(Filename, Params, WorkingDir, ShowCmd, ewWaitUntilTerminated, ResultCode);
  if Result then
    Result := (ResultCode = 0);
end;

// Helper: Check if service exists
function ServiceExists(ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Exec('sc.exe', 'query "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0);
end;

// Helper: Stop service if running
procedure StopService(ServiceName: String);
var
  ResultCode: Integer;
begin
  if ServiceExists(ServiceName) then
  begin
    Log('Stopping service: ' + ServiceName);
    Exec('sc.exe', 'stop "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(2000); // Wait for service to stop
  end;
end;

// Helper: Remove service via NSSM
procedure RemoveService(ServiceName: String);
var
  NssmPath: String;
  ResultCode: Integer;
begin
  NssmPath := ExpandConstant('{app}\tools\nssm.exe');
  
  if ServiceExists(ServiceName) then
  begin
    Log('Removing service via NSSM: ' + ServiceName);
    
    // Stop first
    StopService(ServiceName);
    
    // Remove via NSSM
    if FileExists(NssmPath) then
    begin
      Exec(NssmPath, 'remove "' + ServiceName + '" confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end
    else
    begin
      // Fallback to sc.exe if NSSM not found
      Exec('sc.exe', 'delete "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
    
    Sleep(1000);
  end;
end;

// Create default config by calling EXE with --init
function CreateDefaultConfig(ExePath, ConfigPath: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  
  if not FileExists(ConfigPath) then
  begin
    Log('Creating default config: ' + ConfigPath);
    Result := Exec(ExePath, '--init --config "' + ConfigPath + '"', ExtractFileDir(ExePath), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    
    if Result and (ResultCode = 0) then
    begin
      Log('Config created successfully');
    end
    else
    begin
      Log('Config creation failed or config already exists');
      Result := False;
    end;
  end
  else
  begin
    Log('Config already exists, skipping: ' + ConfigPath);
    Result := True; // Not an error
  end;
end;

// Agent does NOT run as a service - it runs as the logged-in user
// This ensures AC launches with a visible window in the user's session

// Install Controller service via NSSM
function InstallControllerService(): Boolean;
var
  NssmPath, ExePath, ConfigPath, LogsDir: String;
  ResultCode: Integer;
begin
  Result := False;
  
  NssmPath := ExpandConstant('{app}\tools\nssm.exe');
  ExePath := ExpandConstant('{app}\PitBoxController.exe');
  ConfigPath := ExpandConstant('{app}\controller_config.json');
  LogsDir := ExpandConstant('{commonappdata}\PitBox\logs');
  
  Log('Installing Controller service via NSSM');
  
  // Remove existing service if present
  if ServiceExists('PitBoxController') then
  begin
    Log('Controller service already exists, removing first');
    RemoveService('PitBoxController');
  end;
  
  // Install service
  if not Exec(NssmPath, 'install PitBoxController "' + ExePath + '" --service --config "' + ConfigPath + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Failed to install Controller service');
    Exit;
  end;
  
  Sleep(500);
  
  // Set display name
  Exec(NssmPath, 'set PitBoxController DisplayName "Fastest Lap PitBox Controller"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // Set description
  Exec(NssmPath, 'set PitBoxController Description "Admin controller for Fastest Lap PitBox lounge management system - serves web UI on http://127.0.0.1:9630"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // Set working directory
  Exec(NssmPath, 'set PitBoxController AppDirectory "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // Set startup type
  Exec(NssmPath, 'set PitBoxController Start SERVICE_AUTO_START', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // Configure logging
  Exec(NssmPath, 'set PitBoxController AppStdout "' + LogsDir + '\PitBoxController.out.log"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NssmPath, 'set PitBoxController AppStderr "' + LogsDir + '\PitBoxController.err.log"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // Configure log rotation
  Exec(NssmPath, 'set PitBoxController AppRotateFiles 1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NssmPath, 'set PitBoxController AppRotateOnline 1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NssmPath, 'set PitBoxController AppRotateSeconds 86400', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(NssmPath, 'set PitBoxController AppRotateBytes 10485760', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  Log('Controller service installed successfully');
  Result := True;
  ControllerServiceInstalled := True;
end;

// Remove PitBoxAgent service if it exists (Agent must NOT run as service)
procedure RemoveAgentServiceIfExists();
begin
  if ServiceExists('PitBoxAgent') then
  begin
    Log('Removing existing PitBoxAgent service - Agent must run as user, not SYSTEM');
    RemoveService('PitBoxAgent');
    Sleep(1500);
  end;
end;

// Create Scheduled Task for Agent (ONLOGON, Run only when user is logged on)
function CreateAgentScheduledTask(): Boolean;
var
  AgentExe, AgentConfig, TaskName, TaskRun, UserName: String;
  ResultCode: Integer;
begin
  Result := False;
  AgentExe := ExpandConstant('{app}\PitBoxAgent.exe');
  AgentConfig := ExpandConstant('{app}\Agent\config\agent_config.json');
  TaskName := 'PitBox Agent';
  UserName := ExpandConstant('{username}');
  
  if UserName = '' then
    UserName := GetEnv('USERNAME');
  
  if UserName = '' then
  begin
    Log('Could not determine username for Scheduled Task');
    Exit;
  end;
  
  // TaskRun: path to exe + args, properly quoted
  TaskRun := '"' + AgentExe + '" --config "' + AgentConfig + '"';
  
  Log('Creating Scheduled Task for PitBox Agent (ONLOGON, user: ' + UserName + ')');
  
  // Delete existing task first
  Exec('schtasks.exe', '/Delete /TN "' + TaskName + '" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
  
  // Create task: /sc ONLOGON = run when user logs on
  // /ru USERNAME without /rp = "Run only when user is logged on" (user session, NOT SYSTEM)
  if Exec('schtasks.exe', '/Create /TN "' + TaskName + '" /TR "' + TaskRun + '" /SC ONLOGON /RU "' + UserName + '" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
    begin
      Log('Scheduled Task created successfully');
      Result := True;
    end
    else
      Log('schtasks returned error code: ' + IntToStr(ResultCode));
  end
  else
    Log('Failed to execute schtasks');
end;

// Remove Agent Scheduled Task
procedure RemoveAgentScheduledTask();
var
  ResultCode: Integer;
begin
  Log('Removing PitBox Agent Scheduled Task');
  Exec('schtasks.exe', '/Delete /TN "PitBox Agent" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then
    Log('Task may not have existed (exit ' + IntToStr(ResultCode) + ')');
end;

// Add firewall rules for Agent (TCP API + UDP enrollment broadcast)
procedure AddAgentFirewallRule();
var
  ResultCode: Integer;
begin
  Log('Adding firewall rules for Agent (TCP 9631-9638 + UDP 9640 enrollment)');

  // TCP rule for agent API (controller polls agent)
  Exec('netsh.exe', 'advfirewall firewall delete rule name="PitBox Agent"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('netsh.exe', 'advfirewall firewall add rule name="PitBox Agent" dir=in action=allow protocol=TCP localport=9631-9638 description="Fastest Lap PitBox Agent API"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 0 then
    Log('TCP firewall rule added successfully')
  else
    Log('Failed to add TCP firewall rule (non-fatal)');

  // UDP rule for enrollment broadcast (controller sends UDP 9640 to sim PCs for auto-pair)
  Exec('netsh.exe', 'advfirewall firewall delete rule name="PitBox Agent Enrollment"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('netsh.exe', 'advfirewall firewall add rule name="PitBox Agent Enrollment" dir=in action=allow protocol=UDP localport=9640 description="Fastest Lap PitBox Agent enrollment broadcast"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 0 then
    Log('UDP enrollment firewall rule added successfully')
  else
    Log('Failed to add UDP enrollment firewall rule (non-fatal)');
end;

// Remove firewall rule for Agent
procedure RemoveAgentFirewallRule();
var
  ResultCode: Integer;
begin
  Log('Removing firewall rule for Agent');
  Exec('netsh.exe', 'advfirewall firewall delete rule name="PitBox Agent"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Add hosts file entry so controller is reachable at http://pitbox:9630/
procedure AddHostsEntry();
var
  ResultCode: Integer;
  Cmd: String;
begin
  Log('Adding hosts entry: 127.0.0.1 pitbox');
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "' +
    '$h = ''C:\Windows\System32\drivers\etc\hosts''; ' +
    'if (!(Select-String -Path $h -Pattern ''pitbox'' -Quiet)) ' +
    '{ Add-Content $h ''127.0.0.1    pitbox'' }"';
  if Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Log('Hosts entry added: pitbox -> 127.0.0.1')
    else
      Log('Hosts entry script returned ' + IntToStr(ResultCode) + ' (non-fatal)');
  end
  else
    Log('Could not run PowerShell for hosts entry (non-fatal)');
end;

// Remove pitbox hosts entry on uninstall
procedure RemoveHostsEntry();
var
  ResultCode: Integer;
  Cmd: String;
begin
  Log('Removing hosts entry for pitbox');
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "' +
    '$h = ''C:\Windows\System32\drivers\etc\hosts''; ' +
    '$lines = Get-Content $h | Where-Object { $_ -notmatch ''pitbox'' }; ' +
    'Set-Content $h $lines"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Start service
function StartService(ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Log('Starting service: ' + ServiceName);
  Result := Exec('sc.exe', 'start "' + ServiceName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  if Result then
  begin
    Log('Service started successfully: ' + ServiceName);
    Sleep(2000); // Give service time to start
  end
  else
  begin
    Log('Failed to start service: ' + ServiceName);
  end;
end;

// Called before/during/after install
procedure CurStepChanged(CurStep: TSetupStep);
var
  AgentExe, ControllerExe: String;
  AgentConfig, ControllerConfig: String;
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    if IsComponentSelected('controller') then
      StopService('PitBoxController');
  end;
  if CurStep = ssPostInstall then
  begin
    AgentExe := ExpandConstant('{app}\PitBoxAgent.exe');
    ControllerExe := ExpandConstant('{app}\PitBoxController.exe');
    AgentConfig := ExpandConstant('{app}\Agent\config\agent_config.json');
    ControllerConfig := ExpandConstant('{app}\controller_config.json');
    
    // Create default configs if needed (Agent uses Agent\config\agent_config.json)
    if IsComponentSelected('agent') and FileExists(AgentExe) then
    begin
      Log('Ensuring Agent config at Agent\config\agent_config.json');
      if not FileExists(AgentConfig) then
      begin
        if FileExists(ExpandConstant('{app}\examples\agent_config.Sim1.json')) then
        begin
          Log('Copying example to Agent\config\agent_config.json');
          FileCopy(ExpandConstant('{app}\examples\agent_config.Sim1.json'), AgentConfig, True);
        end
        else
          CreateDefaultConfig(AgentExe, AgentConfig);
      end
      else
        Log('Agent config already exists at Agent\config\agent_config.json');
    end;
    
    if IsComponentSelected('controller') and FileExists(ControllerExe) then
    begin
      Log('Creating Controller default config if needed');
      CreateDefaultConfig(ControllerExe, ControllerConfig);
    end;
    
    // Agent: Remove any existing service, create Scheduled Task (NO SERVICE - runs as user)
    if IsComponentSelected('agent') then
    begin
      // CRITICAL: Remove PitBoxAgent service if it exists (causes AC to run headless)
      RemoveAgentServiceIfExists();
      
      // Add firewall rule if selected
      if WizardIsTaskSelected('firewallagent') then
      begin
        AddAgentFirewallRule();
      end;
      
      // Create Scheduled Task (ONLOGON, Run only when user is logged on)
      if WizardIsTaskSelected('startupagent') then
      begin
        if CreateAgentScheduledTask() then
        begin
          // Run the task immediately so agent starts without requiring logoff/logon
          Log('Starting PitBox Agent now via Scheduled Task run...');
          Exec('schtasks.exe', '/Run /TN "PitBox Agent"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          if ResultCode = 0 then
            Log('PitBox Agent started successfully')
          else
          begin
            // Fallback: launch exe directly in user session
            Log('schtasks /Run returned ' + IntToStr(ResultCode) + ', launching agent exe directly');
            ShellExec('open', ExpandConstant('{app}\PitBoxAgent.exe'),
              '--config "' + ExpandConstant('{app}\Agent\config\agent_config.json') + '"',
              ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
          end;
        end;
      end;

      Log('Agent will auto-start via Scheduled Task (user session, NOT service)');
    end;
    
    // Controller: Install as Windows Service (admin PC only)
    if IsComponentSelected('controller') then
    begin
      // Add hosts entry so the UI is reachable at http://pitbox:9630/
      AddHostsEntry();

      if InstallControllerService() then
      begin
        // Start service
        StartService('PitBoxController');
        
        Sleep(3000); // Give controller time to fully start
        
        // Launch system tray app (opens PitBox in an app window automatically)
        if FileExists(ExpandConstant('{app}\PitBoxTray.exe')) then
        begin
          Log('Launching PitBox tray launcher');
          ShellExec('open', ExpandConstant('{app}\PitBoxTray.exe'), '', ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
        end
        else if WizardIsTaskSelected('openbrowser') then
        begin
          Log('PitBoxTray.exe not found, opening browser directly');
          Exec('cmd.exe', '/c start http://pitbox:9630', '', SW_HIDE, ewNoWait, ResultCode);
        end;
      end;
    end;
  end;
end;

// Called before uninstall
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    // Agent: Remove service if it exists (migration from old installs), remove Scheduled Task, remove firewall rule
    RemoveAgentServiceIfExists();
    RemoveAgentScheduledTask();
    RemoveAgentFirewallRule();

    // Controller: Stop and remove service, remove hosts entry
    if ServiceExists('PitBoxController') then
    begin
      Log('Uninstalling Controller service');
      RemoveService('PitBoxController');
    end;
    RemoveHostsEntry();
  end;
end;

procedure InitializeWizard();
begin
  ControllerServiceInstalled := False;
end;

// No custom installation summary
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := '';
end;
