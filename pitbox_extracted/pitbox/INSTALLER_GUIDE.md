# PitBox Unified Installer Guide

## Overview

The PitBox Unified Installer is a single installer that can deploy either:
- **Sim PC (Agent)** - For managing Assetto Corsa on sim PCs
- **Admin PC (Controller)** - For the central admin/management PC
- **Both** - Install both components on the same machine (advanced)

### What the Installer Does Automatically

✅ **Installs Binaries** - Copies PitBox executables to `C:\PitBox\installed\bin\`  
✅ **Bundles NSSM** - Includes NSSM service manager (for Controller only)  
✅ **Creates Folders** - Sets up `bin`, `config`, `logs`, `tools`  
✅ **Creates Default Configs** - Generates initial configuration files  
✅ **Agent Startup** - Creates Scheduled Task (ONLOGON, runs as user - NOT service)  
✅ **Controller Service** - Installs Controller as Windows Service  
✅ **Configures Services** - Sets automatic startup, log rotation (Controller)  
✅ **Adds Firewall Rules** - Opens port 9600 for Agent (optional)  
✅ **Opens Web UI** - Launches browser to controller UI (optional)  

**No manual PowerShell scripts required!**

### IMPORTANT: Agent vs Controller

| Component | Runs As | Why |
|-----------|---------|-----|
| **Agent (Sim PCs)** | Logged-in user | Must launch AC with visible window |
| **Controller (Admin PC)** | Windows Service (SYSTEM) | Background operation, no game launch |

---

## Quick Start

### Sim PC Installation (Agent)

1. **Run Installer**
   - Double-click `PitBoxInstaller.exe`
   - Select **"Sim PC (Agent)"**
   - Check "Start Agent automatically on user login" (recommended)
   - Check "Add Windows Firewall rule" (recommended)
   - Click Install

2. **Edit Configuration** (REQUIRED)
   ```
   Edit: C:\PitBox\installed\config\agent.json
   ```
   Set:
   - `agent_id`: "Sim1", "Sim2", etc.
   - `token`: Generate secure token (see below)
   - `paths`: Verify AC installation paths

3. **Start Agent**
   - **Option A**: Restart Windows (Agent will auto-start)
   - **Option B**: Manually start from Start Menu → "Start PitBox Agent"

4. **Verify**
   ```powershell
   Get-Process PitBoxAgent
   # Should show: Running as YOUR USERNAME (not SYSTEM)
   ```

### Admin PC Installation (Controller)

1. **Run Installer**
   - Double-click `PitBoxInstaller.exe`
   - Select **"Admin PC (Controller)"**
   - Check "Open Web UI after installation" (optional)
   - Click Install

2. **Edit Configuration** (REQUIRED)
   ```
   Edit: C:\PitBox\installed\config\controller.json
   ```
   Set:
   - `agents`: List all sim PCs with IPs and tokens
   - Tokens must match what you set on agents

3. **Restart Service**
   ```
   services.msc → Find "Fastest Lap PitBox Controller" → Restart
   ```

4. **Access Web UI**
   ```
   http://127.0.0.1:9600
   ```

---

## Detailed Installation Steps

### Prerequisites

- **Windows 10/11** (64-bit)
- **Administrator privileges** (required for service installation)
- **PitBoxInstaller.exe** (from build output)

### Installation Wizard

#### Step 1: Welcome Screen

Click "Next" to begin.

#### Step 2: Installation Type

Choose your role:

**Option A: Sim PC (Agent)**
- For sim PCs running Assetto Corsa
- Creates Startup folder shortcut (runs as user, NOT as service)
- Opens port 9600 in firewall

**Option B: Admin PC (Controller)**
- For the central management PC
- Installs PitBoxController service
- Serves web UI on http://127.0.0.1:9600

**Option C: Both Agent and Controller**
- Install both on the same machine
- For testing or single-PC setups
- Agent runs as user, Controller runs as service
- Both share port 9600 (Agent binds 0.0.0.0, Controller binds localhost)

#### Step 3: Select Tasks

**For Agent**:
- ☑ Start Agent automatically on user login (Scheduled Task - runs as user) - **Recommended**
- ☑ Add Windows Firewall rule (port 9600) - **Recommended**

**For Controller**:
- ☐ Open Web UI after installation - Optional

#### Step 4: Ready to Install

Review the summary and click "Install".

The installer will:
1. Copy files to `C:\PitBox\installed\`
2. Create configuration files
3. Install NSSM to `tools` folder (Controller only)
4. Create Scheduled Task for Agent (if selected) - runs at user logon, NOT as service
5. Install Controller Windows Service (if selected)
6. Configure Controller service with log rotation
7. Add firewall rule(s) if selected
8. Start Controller service (if applicable)

#### Step 5: Finish

Installation complete! **Important**: You must edit the configuration files before the services will work correctly.

---

## Post-Installation Configuration

### Agent Configuration (REQUIRED)

Edit: `C:\PitBox\installed\config\agent.json`

```json
{
  "agent_id": "Sim1",  // ← Change this (Sim1, Sim2, Sim3, etc.)
  "token": "CHANGE_ME_IMMEDIATELY",  // ← Generate secure token
  "listen_host": "0.0.0.0",
  "port": 9600,
  "paths": {
    "acs_exe": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe",
    "ac_savedsetups": "C:\\Users\\info\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups",
    "ac_controls_ini": "C:\\Users\\info\\Documents\\Assetto Corsa\\cfg\\controls.ini",
    // ... verify all paths
  }
}
```

**Generate Secure Token**:
```powershell
# Run in PowerShell
[System.Web.Security.Membership]::GeneratePassword(32,8)
```

**After editing, start the Agent**:
- Restart Windows (Agent auto-starts via Startup folder)
- OR manually: Start Menu → "Start PitBox Agent"

### Controller Configuration (REQUIRED)

Edit: `C:\PitBox\installed\config\controller.json`

```json
{
  "ui_host": "127.0.0.1",
  "ui_port": 9630,
  "allow_lan_ui": false,
  "poll_interval_sec": 1.5,
  "agents": [
    {
      "id": "Sim1",
      "host": "192.168.1.101",  // ← Change to actual IP
      "port": 9600,
      "token": "MATCH_AGENT_TOKEN"  // ← Must match agent's token
    },
    {
      "id": "Sim2",
      "host": "192.168.1.102",
      "port": 9600,
      "token": "MATCH_AGENT_TOKEN"
    }
    // ... add all sim PCs
  ]
}
```

**After editing, restart the service**:
```
Win+R → services.msc → Find "Fastest Lap PitBox Controller" → Right-click → Restart
```

---

## Installation Paths

The installer creates the following structure:

```
C:\PitBox\installed\
├── bin\
│   ├── PitBoxAgent.exe          # Agent executable (windowless)
│   └── PitBoxController.exe     # Controller executable (windowless)
├── config\
│   ├── agent.json               # Agent configuration (edit this!)
│   └── controller.json          # Controller configuration (edit this!)
├── logs\
│   ├── PitBoxAgent.out.log      # Agent stdout log
│   ├── PitBoxAgent.err.log      # Agent error log
│   ├── PitBoxController.out.log # Controller stdout log
│   └── PitBoxController.err.log # Controller error log
├── tools\
│   └── nssm.exe                 # Service manager
└── examples\
    ├── agent_config.Sim1.json   # Example agent config
    └── controller_config.json   # Example controller config
```

---

## Startup Configuration

### Agent (User-Session, NOT a Service)

| Property | Value |
|----------|-------|
| **Runs As** | Logged-in user (e.g., `info`) |
| **Executable** | `C:\PitBox\installed\bin\PitBoxAgent.exe` |
| **Arguments** | `--config "C:\PitBox\installed\config\agent.json"` |
| **Startup** | Startup folder shortcut |
| **Shortcut Path** | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitBox Agent.lnk` |
| **Why User Session?** | AC must launch with visible window (SYSTEM causes headless launch) |

### Controller (Windows Service)

| Property | Value |
|----------|-------|
| **Service Name** | `PitBoxController` |
| **Display Name** | "Fastest Lap PitBox Controller" |
| **Runs As** | SYSTEM (Windows Service) |
| **Executable** | `C:\PitBox\installed\bin\PitBoxController.exe` |
| **Arguments** | `--service --config "C:\PitBox\installed\config\controller.json"` |
| **Startup Type** | Automatic |
| **Working Directory** | `C:\PitBox\installed\bin` |
| **Web UI** | http://127.0.0.1:9600 |
| **Stdout Log** | `C:\PitBox\installed\logs\PitBoxController.out.log` |
| **Stderr Log** | `C:\PitBox\installed\logs\PitBoxController.err.log` |
| **Log Rotation** | Daily, max 10 MB |
| **Why Service?** | Admin PC, no game launch, must start before user login |

---

## Managing Components

### Managing Agent (User-Session Process)

**Check Status**:
```powershell
Get-Process PitBoxAgent
# Verify: User column should show YOUR USERNAME (not SYSTEM)
```

**Start Agent**:
- Start Menu → "Start PitBox Agent"
- OR double-click Startup shortcut: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PitBox Agent.lnk`

**Stop Agent**:
```powershell
Stop-Process -Name PitBoxAgent
```

**View Logs**:
```powershell
Get-Content C:\PitBox\installed\logs\PitBoxAgent.out.log -Tail 50
```

### Managing Controller (Windows Service)

**Using Windows Services UI**:
1. **Open Services**: `Win+R` → `services.msc` → Enter
2. **Find Service**: "Fastest Lap PitBox Controller"
3. **Right-click** for options: Start, Stop, Restart, Properties

**Using PowerShell**:

**Check Status**:
```powershell
Get-Service PitBoxController
```

**Start Service**:
```powershell
Start-Service PitBoxController
```

**Stop Service**:
```powershell
Stop-Service PitBoxController
```

**Restart Service**:
```powershell
Restart-Service PitBoxController
```

**View Logs**:
```powershell
Get-Content C:\PitBox\installed\logs\PitBoxController.out.log -Tail 50
```

---

## Verification

### Agent Verification

1. **Check Process Status**:
   ```powershell
   Get-Process PitBoxAgent | Select-Object Name, Id, @{N="User";E={(Get-WmiObject Win32_Process -Filter "ProcessId = $($_.Id)").GetOwner().User}}
   # Should show: User = YOUR USERNAME (e.g., "info"), NOT "SYSTEM"
   ```

2. **Test API**:
   ```powershell
   $token = "your_agent_token"
   Invoke-RestMethod -Uri http://localhost:9600/ping -Headers @{Authorization="Bearer $token"}
   # Should return: {"message": "pong"}
   ```

3. **Check Logs**:
   ```powershell
   Get-Content C:\PitBox\installed\logs\PitBoxAgent.out.log -Tail 20
   # Should show startup messages
   ```

4. **CRITICAL: Verify Not Running as SYSTEM**:
   - If Agent runs as SYSTEM, AC will launch headless (no window)
   - Task Manager → Details tab → Find `PitBoxAgent.exe` → Check "User Name" column
   - Must show your username (e.g., `info`), NOT `SYSTEM`

### Controller Verification

1. **Check Service Status**:
   ```powershell
   Get-Service PitBoxController
   # Should show: Running
   ```

2. **Access Web UI**:
   ```
   Open browser: http://127.0.0.1:9600
   # Should show PitBox Controller interface
   ```

3. **Check Agent Status in UI**:
   - Agents should appear as "Online" if configured correctly
   - If "Offline", check agent configs and tokens

---

## Troubleshooting

### Agent Won't Start

**Check Error Log**:
```powershell
Get-Content C:\PitBox\installed\logs\PitBoxAgent.err.log
```

**Common Issues**:

| Error | Solution |
|-------|----------|
| "FATAL ERROR: PitBoxAgent is running as SYSTEM" | DO NOT run as service. Use Startup folder shortcut instead |
| "Config file not found" | Config should exist at `C:\PitBox\installed\config\agent.json` |
| "Port already in use" | Another process using port 9600 |
| "Token required" | Edit config, set secure token |

### AC Launches Headless (No Window)

**Symptom**: `acs.exe` process exists, but no game window appears.

**Cause**: Agent running as SYSTEM instead of user.

**Solution**:
1. Kill Agent process:
   ```powershell
   Stop-Process -Name PitBoxAgent -Force
   ```
2. Verify no Agent service exists:
   ```powershell
   Get-Service PitBoxAgent -ErrorAction SilentlyContinue
   # Should return nothing
   ```
3. Start Agent from Startup folder or Start Menu
4. Verify running as user:
   ```powershell
   Get-Process PitBoxAgent | Select-Object Name, Id, @{N="User";E={(Get-WmiObject Win32_Process -Filter "ProcessId = $($_.Id)").GetOwner().User}}
   ```

### Controller Service Won't Start

**Check Error Log**:
```powershell
Get-Content C:\PitBox\installed\logs\PitBoxController.err.log
```

**Common Issues**:
- Config file missing
- Port 9600 already in use
- Invalid configuration

### Config File Missing

If config file wasn't created by installer:

```powershell
cd C:\PitBox\installed\bin
.\PitBoxAgent.exe --init --config C:\PitBox\installed\config\agent.json
# Edit the created file
notepad C:\PitBox\installed\config\agent.json
```

### Firewall Blocking Connections

**Check if rule exists**:
```powershell
Get-NetFirewallRule -DisplayName "PitBox Agent"
```

**Manually add rule**:
```powershell
New-NetFirewallRule -DisplayName "PitBox Agent" `
    -Direction Inbound `
    -LocalPort 9600 `
    -Protocol TCP `
    -Action Allow
```

### Port 9600 Already in Use

**Find process using port**:
```powershell
netstat -ano | findstr :9600
```

**Kill process** (use PID from netstat):
```powershell
Stop-Process -Id <PID> -Force
```

### Service Starts Then Immediately Stops

**Check Event Viewer**:
```powershell
Get-EventLog -LogName Application -Source "PitBoxAgent" -Newest 10
```

**Common causes**:
- Invalid configuration file
- Missing dependencies (shouldn't happen with PyInstaller)
- Port conflict

---

## Upgrading

### Upgrade Existing Installation

1. **Stop Components** (before running installer):
   ```powershell
   # Stop Agent (if running)
   Stop-Process -Name PitBoxAgent -Force -ErrorAction SilentlyContinue
   
   # Stop Controller service (if installed)
   Stop-Service PitBoxController -ErrorAction SilentlyContinue
   ```

2. **Run New Installer**:
   - Run `PitBoxInstaller.exe`
   - Select same role as before
   - Installer will:
     - Stop existing Controller service (if applicable)
     - Remove old Controller service
     - Install new executables
     - Update Startup shortcut (Agent)
     - Reinstall Controller service with new executable
     - **Preserve your config files**
     - Start Controller service

3. **Verify Upgrade**:
   ```powershell
   # Agent
   Get-Process PitBoxAgent -ErrorAction SilentlyContinue
   
   # Controller
   Get-Service PitBoxController -ErrorAction SilentlyContinue
   ```

**Note**: Configuration files in `config\` folder are **never overwritten** during upgrades.

---

## Uninstalling

### Using Windows Add/Remove Programs

1. **Open Settings**: `Win+I` → Apps → Apps & features
2. **Find**: "PitBox"
3. **Click**: Uninstall
4. **Confirm**: Click Uninstall again

The uninstaller will:
- Stop Agent process (if running)
- Stop Controller service (if running)
- Remove Controller service via NSSM
- Remove Startup folder shortcut (Agent)
- Remove firewall rules
- Delete all files from `C:\PitBox\installed\`
- Remove Start Menu shortcuts

### Using Uninstall Shortcut

1. **Start Menu**: Search "PitBox"
2. **Click**: "Uninstall PitBox"
3. **Confirm**: Click Yes

---

## Firewall Configuration

### Agent Firewall Rule

If you selected "Add Windows Firewall rule" during installation:

**Rule Details**:
- Name: "PitBox Agent"
- Direction: Inbound
- Protocol: TCP
- Port: 9600
- Action: Allow

**Verify Rule**:
```powershell
Get-NetFirewallRule -DisplayName "PitBox Agent" | Format-List *
```

**Manually Remove Rule**:
```powershell
Remove-NetFirewallRule -DisplayName "PitBox Agent"
```

### Controller Firewall

By default, the controller binds to `localhost` only and does not need a firewall rule.

If you want LAN access:
1. Edit `C:\PitBox\installed\config\controller.json`
2. Set `"allow_lan_ui": true`
3. Restart service
4. Add firewall rule:
   ```powershell
   New-NetFirewallRule -DisplayName "PitBox Controller UI" `
       -Direction Inbound `
       -LocalPort 9600 `
       -Protocol TCP `
       -Action Allow
   ```

---

## Advanced Configuration

### Disable Agent Auto-Start

**Remove Startup shortcut**:
```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\PitBox Agent.lnk"
```

**Re-enable (restore shortcut)**:
- Re-run installer and select "Start Agent automatically on user login"

### Modify Controller Service Configuration

**Using NSSM**:
```powershell
cd C:\PitBox\installed\tools
.\nssm.exe edit PitBoxController
```

This opens a GUI where you can modify:
- Executable path
- Arguments
- Working directory
- Log files
- Startup type
- Dependencies

**View Service Configuration**:
```powershell
cd C:\PitBox\installed\tools
.\nssm.exe dump PitBoxController
```

**Change Service Startup Type**:
```powershell
# To Manual
Set-Service PitBoxController -StartupType Manual

# Back to Automatic
Set-Service PitBoxController -StartupType Automatic
```

---

## FAQ

### Q: Do I need to download NSSM separately?

**A**: No, NSSM is bundled in the installer.

### Q: Can I install both Agent and Controller on the same PC?

**A**: Yes, select "Both Agent and Controller" during installation. Agent runs as user (Startup folder), Controller runs as service. Both can run simultaneously as they bind to different interfaces (Agent: 0.0.0.0, Controller: localhost).

### Q: Where are the log files?

**A**: `C:\PitBox\installed\logs\`
- `PitBoxAgent.out.log` / `PitBoxAgent.err.log`
- `PitBoxController.out.log` / `PitBoxController.err.log`

Logs rotate daily and when they exceed 10 MB.

### Q: How do I change the port?

**A**: Edit the config file (`agent.json` or `controller.json`), change the `port` value, and restart the service.

### Q: Can I run multiple Agents on one PC?

**A**: Not recommended with the installer. Each Agent needs a unique port and config. Manual setup would be required.

### Q: Why doesn't Agent run as a service?

**A**: Running Agent as SYSTEM (Windows Service) causes Assetto Corsa to launch headless (process exists but no window appears). Agent MUST run as the logged-in user to show AC's window. Controller runs as a service because it doesn't launch games and needs to start before user login.

### Q: What if Agent is running as SYSTEM?

**A**: Agent will detect this and exit immediately with a clear error message. Start Agent from the Startup folder or Start Menu instead.

### Q: What happens if I don't edit the config after installing?

**A**: The service will start but won't function correctly because:
- Token is set to "CHANGE_ME_IMMEDIATELY" (insecure)
- Paths may not match your system
- Agent ID is default "Sim1" (must be unique per sim)

### Q: How do I access the web UI from another PC?

**A**: 
1. Edit `controller.json`, set `"allow_lan_ui": true`
2. Add firewall rule for port 9600
3. Access via: `http://<controller-ip>:9600`

### Q: Does the installer require internet?

**A**: No, the installer is completely offline. All required files are bundled.

---

## Summary

### What You Get

✅ **One-Click Installation** - Installer handles everything  
✅ **Correct Architecture** - Agent as user, Controller as service  
✅ **No Manual Scripts** - No PowerShell commands needed  
✅ **Bundled NSSM** - No separate downloads (Controller only)  
✅ **Firewall Configuration** - Optional automatic setup  
✅ **Clean Uninstall** - Removes everything properly  
✅ **Safe Upgrades** - Config files preserved  
✅ **AC Window Visible** - Agent runs as user, not SYSTEM  

### Installation Process

1. Run `PitBoxInstaller.exe`
2. Select role (Agent, Controller, or Both)
3. Click Install
4. Edit config files
5. Restart services
6. Done!

### Support

- **Full Guide**: This document
- **Service Guide**: `WINDOWS_SERVICE_GUIDE.md`
- **Quick Setup**: `QUICK_SERVICE_SETUP.md`

---

**PitBox Unified Installer - Complete Solution** ✅

**Current release**: 1.4.1 (test release for update-check flow).  
**Previous**: 1.1.0 (User-Session Agent Fix), 2026-02-13.

**CRITICAL CHANGE**: Agent no longer runs as Windows Service. Runs via Startup folder as logged-in user to ensure AC windows are visible. See `ARCHITECTURE.md` for details.
