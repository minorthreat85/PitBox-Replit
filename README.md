# PitBox

Professional LAN-based management system for Assetto Corsa racing lounges with up to 8 simulator PCs.

---

## 📍 Port & Identity Map (v1.5.0+)

**Canonical Port Scheme** (strictly enforced):

| Service | Port | Identity |
|---------|------|----------|
| Emperor/server-manager | 9600 | (reserved, do not touch) |
| **PitBoxController** | **9630** | Web UI & REST API |
| Sim1 Agent | 9631 | agent_id: `Sim1`, token: `sim1` |
| Sim2 Agent | 9632 | agent_id: `Sim2`, token: `sim2` |
| Sim3 Agent | 9633 | agent_id: `Sim3`, token: `sim3` |
| Sim4 Agent | 9634 | agent_id: `Sim4`, token: `sim4` |
| Sim5 Agent | 9635 | agent_id: `Sim5`, token: `sim5` |
| Sim6 Agent | 9636 | agent_id: `Sim6`, token: `sim6` |
| Sim7 Agent | 9637 | agent_id: `Sim7`, token: `sim7` |
| Sim8 Agent | 9638 | agent_id: `Sim8`, token: `sim8` |

**Key Rules**:
- **Agent port** = `9630 + Sim Number` (e.g., Sim1=9631, Sim5=9635)
- **Token** must match agent_id (lowercase): `Sim1` → token `sim1`
- **Controller Web UI**: http://localhost:9630 (NOT 9600)
- **Validation**: Strict enforcement at agent startup (invalid configs refused)

📖 **Full details**: See `PORT_IDENTITY_MAP.md`  
🔧 **Migration**: Use `scripts/migrate_ports_and_ids.ps1` for existing installs

---

## Production Installation (Recommended)

### Install Agent (on Each Sim PC)

**3 Simple Steps:**

1. **Run Installer**
   ```
   Double-click: PitBoxAgentInstaller.exe
   ```
   - Installs to `C:\PitBox\Agent\`
   - Adds Windows Firewall rule (port 9600)
   - Creates Start Menu shortcuts

2. **Configure**
   - Installer opens config file automatically
   - Edit `C:\PitBox\Agent\config\agent_config.json`:
     - Change `"token"` to a secure random string (use PowerShell command shown in installer)
     - Set `"agent_id"` to `"Sim1"`, `"Sim2"`, etc. to match this PC
     - Verify file paths match your system

3. **Start Agent**
   - Start Menu → PitBox → PitBox Agent
   - Or run: `C:\PitBox\Agent\bin\PitBoxAgent.exe`

✅ Done! Agent is now running and waiting for controller commands.

"Launch Session" always starts Assetto Corsa by running `paths.acs_exe` directly (no Content Manager or scripts).

---

### Install Controller (on Admin PC)

**3 Simple Steps:**

1. **Run Installer**
   ```
   Double-click: PitBoxControllerInstaller.exe
   ```
   - Installs to `C:\PitBox\Controller\`
   - Optionally adds Windows Firewall rule (port 9600, only if LAN UI needed)
   - Creates Start Menu shortcuts

2. **Configure**
   - Installer opens config file automatically
   - Edit `C:\PitBox\Controller\config\controller_config.json`:
     - Change all `"token"` values to match the tokens you set on agents
     - Update `"host"` IP addresses to match your sim PCs (192.168.1.101-108)
     - Set `"allow_lan_ui": true` if you want to access UI from other PCs

3. **Start Controller**
   - Start Menu → PitBox → PitBox Controller
   - Or run: `C:\PitBox\Controller\PitBoxController.exe`
   - Browser opens automatically to: http://127.0.0.1:9600

✅ Done! Open the web UI and start managing your sims.

---

## Sim Display (Kiosk)

On each sim PC, open a browser (or kiosk) to the **Controller** to show only the assigned server preset: track, server ID, and car selection. The sim does not read local AC files; it only calls the Controller API.

**URL on each sim** (use the Admin/Controller PC’s IP or hostname and port, e.g. 9630):

```
http://192.168.1.200:9630/sim?agent_id=Sim5
```

Example: `http://192.168.1.200:9630/sim?agent_id=Sim5` when the Admin/Controller PC is at 192.168.1.200.

### Fullscreen and hiding the taskbar

- **Fullscreen**: Use kiosk-style launch so the page fills the screen with no address bar or tabs.
  - **Launcher script** (on the sim PC): run `scripts\launch_sim_display.ps1` with your agent and Controller URL:
    ```powershell
    .\launch_sim_display.ps1 -AgentId Sim5 -ControllerUrl "http://192.168.1.200:9630"
    ```
  - Or create a shortcut that runs:
    ```text
    "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --app=http://CONTROLPC:9630/sim?agent_id=Sim5
    ```
  - Exit fullscreen: **Alt+F4**.
- **No icon in taskbar**: With Chrome/Edge, `--kiosk --app=...` still usually shows a taskbar button on Windows. To hide it completely you need a **thin wrapper** (e.g. a small Electron app) that opens the same URL in a frameless, fullscreen window with “skip taskbar” set. That can be added as an optional `sim-display` Electron app in the repo if you want.

**Endpoints the sim page uses**:
- **GET** `/api/sims/{agent_id}/server-display` — polled every 1.5s; returns assigned server, track, mode (pickup/slots), cars list, and slots when in slots mode.

Assignments are set from the Control UI via **POST** `/api/assignments/{agent_id}` with body `{ "server_id": "SERVER_01" }`. **GET** `/api/assignments/{agent_id}` returns the current assignment.

---

## Live timing (native)

Live timing is provided **natively** by PitBox: the Controller listens on the AC dedicated-server UDP plugin protocol, normalises the stream into an in-memory session model and renders a leaderboard in the **Live Timing** tab. No external timing app, no `.NET`, no iframe.

**AC dedicated server** — these `[SERVER]` lines are written automatically by PitBox when you save server config or load a preset, but if you maintain `server_cfg.ini` by hand, add:
```ini
UDP_PLUGIN_LOCAL_PORT=9999
UDP_PLUGIN_ADDRESS=127.0.0.1:9996
```
(Use the Controller PC's LAN IP instead of `127.0.0.1` if AC runs on a different machine.)

**Firewall** — open the AC plugin port if AC runs on a different PC than the Controller:
```powershell
New-NetFirewallRule -DisplayName "PitBox AC Plugin" -Direction Inbound -Protocol UDP -LocalPort 9996 -Action Allow
```

---

## Server Config (Presets)

In the Web UI, **Server Config** uses **presets** (server profiles). Each preset is a folder under the AC server presets root (e.g. `presets\SERVER_01\`). The path is set in controller config: **ac_server_root** and **ac_server_presets_root** (or **ac_server_cfg_path** for legacy single-preset).

- **Presets list** (left sidebar): Click a server to select it; **double-click** to start or stop the AC server. Drag rows to reorder; order is saved in the browser. The **gear icon** opens that preset’s folder in Windows Explorer.
- **“PitBox Test Server”** is the label for the **default** preset when no custom name is set.
- **Track panel**: Click the track block to open the track picker; choose track and layout, then OK. Save the config to write `server_cfg.ini` and `entry_list.ini`.

**Naming (glossary)**:
- **Preset** = one server profile (a folder under the presets root, e.g. `SERVER_01`).
- **Server** = in the UI and API, usually the same as preset (e.g. `server_id` = preset folder name).
- **Instance** = the currently selected preset in the dropdown; the sidebar list shows all instances/presets.

---

## Overview

PitBox provides centralized control of multiple Assetto Corsa simulators from a single admin workstation:

- **Controller** (Admin PC): Fastest Lap themed web UI + CLI for managing all sims
- **Agent** (Sim PCs): HTTP service for launching AC and applying presets
- **Zero Python Required**: Self-contained EXE files, no dependencies
- **Auto-Configuration**: First run creates default config with helpful prompts

**Key Features**:
- Launch/exit AC on all sims simultaneously or individually
- Apply steering and assists presets remotely
- Real-time status monitoring (online/offline, AC running/stopped)
- Professional GUI installers with firewall rules and shortcuts
- No cloud dependencies, fully LAN-based

## Architecture

```
┌─────────────────────────────────────────┐
│         Admin PC (Controller)           │
│   ┌─────────────────────────────────┐   │
│   │   Web UI (Fastest Lap Theme)    │   │
│   │   http://127.0.0.1:9600         │   │
│   └─────────────────────────────────┘   │
│              ▲                           │
│              │ HTTP API                  │
└──────────────┼───────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼────┐           ┌───▼────┐
│  Sim1  │    ...    │  Sim8  │
│ Agent  │           │ Agent  │
│:9600   │           │:9600   │
└────────┘           └────────┘
```

## Requirements

**For Production Use (Installers):**
- Windows 10/11 on all PCs
- Assetto Corsa installed on sim PCs
- LAN network with static IP assignments
- Administrator rights (for installation only)

**For Development/Building:**
- **Python 3.11.9 (64-bit, Windows)** - REQUIRED, installed at `C:\Python311\`
  - Download: https://www.python.org/downloads/release/python-3119/
  - Supported range: >=3.11,<3.12
  - **Do NOT use Python 3.12 or 3.13**
- Inno Setup 6 (for building installers)
- Git (for version control)

## Quick Start (Development)

### 1. Clone Repository

```powershell
cd C:\PitBox\dev
git clone <repo-url> pitbox
cd pitbox
```

### 2. Setup Development Environment

```powershell
.\scripts\setup_dev.ps1 -Dev
```

This will:
- Create Python virtual environment
- Install dependencies
- Generate example configs in `examples\`

### 3. Configure Agents and Controller

Edit the generated configs in `examples\`:

**Agent Config** (`agent_config.Sim1.json`):
```json
{
  "agent_id": "Sim1",
  "token": "CHANGE_ME_TO_SECURE_TOKEN",
  "listen_host": "0.0.0.0",
  "port": 9600,
  "paths": {
    "acs_exe": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe",
    "ac_savedsetups": "C:\\Users\\info\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups",
    "cm_assists_presets": "C:\\Users\\info\\AppData\\Local\\AcTools Content Manager\\Presets\\Assists",
    "managed_steering_templates": "C:\\PitBox\\installed\\presets\\steering",
    "managed_assists_templates": "C:\\PitBox\\installed\\presets\\assists"
  }
}
```

**Controller Config** (`controller_config.json`):
```json
{
  "ui_host": "127.0.0.1",
  "ui_port": 9630,
  "allow_lan_ui": false,
  "poll_interval_sec": 1.5,
  "agents": [
    {"id": "Sim1", "host": "192.168.1.101", "port": 9600, "token": "CHANGE_ME"},
    {"id": "Sim2", "host": "192.168.1.102", "port": 9600, "token": "CHANGE_ME"}
  ]
}
```

**⚠️ IMPORTANT**: Change all tokens to secure random strings before deployment!

Generate secure tokens with PowerShell:
```powershell
[System.Web.Security.Membership]::GeneratePassword(32,8)
```

### 4. Run Agent (Dev Mode)

On a sim PC:
```powershell
python -m agent.main --config examples\agent_config.Sim1.json --debug
```

### 5. Run Controller (Dev Mode)

On admin PC:
```powershell
python -m controller.main --config examples\controller_config.json --debug
```

Then open browser to: **http://127.0.0.1:9600**

## Building from Source

### Build Production Installers

```powershell
# Clone repository
cd C:\PitBox\dev
git clone <repo-url> pitbox
cd pitbox

# Setup development environment
.\scripts\setup_dev.ps1 -Dev

# Build everything (EXEs + Installers)
.\scripts\build_release.ps1 -Dev
```

**Requirements:**
- **Python 3.11.9 (64-bit, Windows)** installed at `C:\Python311\`
  - Download: https://www.python.org/downloads/release/python-3119/
  - **CRITICAL**: Do NOT use Python 3.12 or 3.13 - PitBox requires 3.11.9
- Inno Setup 6: https://jsteam.org/inno-setup/

**Output** (in `dist\`):
- `PitBoxAgent.exe` - Standalone agent executable
- `PitBoxController.exe` - Standalone controller executable
- `PitBoxAgentInstaller.exe` - **GUI installer for agents**
- `PitBoxControllerInstaller.exe` - **GUI installer for controller**

### Manual Installation (Without Installers)

If you don't want to use installers, you can run the standalone EXEs:

**Agent:**
```powershell
# Copy to sim PC
copy dist\PitBoxAgent.exe C:\PitBox\Agent\bin\

# First run creates default config
C:\PitBox\Agent\bin\PitBoxAgent.exe

# Edit the config that was created
notepad C:\PitBox\Agent\config\agent_config.json

# Start agent
C:\PitBox\Agent\bin\PitBoxAgent.exe
```

**Controller:**
```powershell
# Copy to admin PC
copy dist\PitBoxController.exe C:\PitBox\Controller\

# First run creates default config
C:\PitBox\Controller\PitBoxController.exe

# Edit the config that was created
notepad C:\PitBox\Controller\config\controller_config.json

# Start controller
C:\PitBox\Controller\PitBoxController.exe
```

### Runbook

Quick reference for config, deploy, and health checks:

**Config location (Agent):**
- `C:\PitBox\Agent\config\agent_config.json`

**Rebuild and deploy Agent in one command:**
```powershell
cd C:\PitBox\dev\pitbox
.\scripts\deploy_agent.ps1 -Dev
```

To also restart the PitBoxAgent service and verify health:
```powershell
.\scripts\deploy_agent.ps1 -Dev -RestartService -VerifyToken "sim5"
```
(Use the token that matches your agent config.)

**Verify Agent health:**
- Call `GET /ping` with `Authorization: Bearer <token>`
- Example: `curl -H "Authorization: Bearer sim5" http://localhost:9600/ping`
- Expect: `{"status":"ok"}`

### Managing Presets

**Copy Preset Templates:**

After installation, copy your curated AC presets:

**Steering Presets** → `C:\PitBox\Agent\presets\steering\`:
- Source: `C:\Users\<user>\Documents\Assetto Corsa\cfg\controllers\savedsetups\`
- Copy and rename your favorite profiles (e.g., `Kids.ini`, `Adults.ini`)

**Assists Presets** → `C:\PitBox\Agent\presets\assists\`:
- Source: `C:\Users\<user>\AppData\Local\AcTools Content Manager\Presets\Assists\`
- Copy and rename your favorite presets (e.g., `Kids.cmpreset`, `Adults.cmpreset`)

### Network Configuration

**Static IPs:**
Configure your router to assign static IPs to sim PCs:
- Sim1: `192.168.1.101`
- Sim2: `192.168.1.102`
- ... through Sim8: `192.168.1.108`

**Firewall Rules:**
The installers add these automatically. If using manual installation:

```powershell
# On sim PCs (Agent):
New-NetFirewallRule -DisplayName "PitBox Agent" -Direction Inbound -Protocol TCP -LocalPort 9600 -Action Allow

# On admin PC (Controller, only if allow_lan_ui=true):
New-NetFirewallRule -DisplayName "PitBox Controller UI" -Direction Inbound -Protocol TCP -LocalPort 9600 -Action Allow
```

**Test Connectivity:**
```powershell
# Check agent reachable
Test-NetConnection -ComputerName 192.168.1.101 -Port 9600

# Test agent ping
Invoke-WebRequest -Uri "http://192.168.1.101:9600/ping"
```

## Controller CLI Commands

The controller can also be used via command-line:

**Check Status**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json status
```

**Start All Sims**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json start --all
```

**Start Single Sim**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json start --sim Sim3
```

**Stop All Sims**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json stop --all
```

**Apply Steering Preset**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json apply-steering --sim Sim3 --name Kids
```

**Apply Assists Preset**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json apply-assists --sim Sim3 --name Kids
```

**List Available Presets**:
```powershell
PitBoxController.exe --config ..\config\controller_config.json presets --sim Sim3
```

## Web UI Usage

The Fastest Lap themed web UI provides:

- **2x4 Grid**: Visual status cards for Sim1–Sim8
- **Status Indicators**: Online/offline, AC running/stopped with PID
- **Launch/Exit Buttons**: Individual sim control
- **Preset Dropdowns**: Select and apply steering/assists presets
- **Bulk Operations**: Select multiple sims for launch/exit
- **Auto-Refresh**: Status updates every 1.5 seconds

**Status Colors**:
- Green border: Online
- Gray border: Offline
- Red border: Error (UNAUTHORIZED, UNREACHABLE)

## Updating PitBox

### Using Installers (Recommended)

Run the new installer - it will:
- Update the EXE file
- **Preserve your existing config files**
- **Preserve your preset files**
- Update shortcuts if needed

Your configuration and presets are safe!

### Manual Update

1. Build new EXEs: `.\scripts\build_release.ps1 -Dev`
2. **Stop all running agents and controller**
3. Copy new EXE files:
   - `dist\PitBoxAgent.exe` → `C:\PitBox\Agent\bin\PitBoxAgent.exe`
   - `dist\PitBoxController.exe` → `C:\PitBox\Controller\PitBoxController.exe`
4. **DO NOT touch `config\` or `presets\` directories**
5. Restart services
6. Check logs for errors

**Never overwrite**:
- `C:\PitBox\Agent\config\agent_config.json`
- `C:\PitBox\Controller\config\controller_config.json`
- `C:\PitBox\Agent\presets\` directory

These files contain your custom configuration and should persist across updates.

## Troubleshooting

### Agent Not Responding

1. **Check Agent is Running**:
   - Look for PitBoxAgent.exe in Task Manager
   - Check `C:\PitBox\Agent\logs\agent.log` for errors
   - Start Menu → PitBox → PitBox Agent

2. **Check Firewall**:
   ```powershell
   Get-NetFirewallRule -DisplayName "PitBox Agent"
   Test-NetConnection -ComputerName <agent-ip> -Port 9600
   ```
   If firewall rule missing, re-run installer or add manually

3. **Verify Token**:
   - Agent token must match controller config
   - Check `C:\PitBox\Agent\config\agent_config.json`
   - Check logs for "Invalid token" errors

4. **Test Ping Endpoint**:
   ```powershell
   Invoke-WebRequest -Uri "http://<agent-ip>:9600/ping"
   ```

5. **Check Config Paths**:
   - Run with `--init` to regenerate default config:
   ```powershell
   C:\PitBox\Agent\bin\PitBoxAgent.exe --init
   ```

### AC Won't Launch

1. **Check acs.exe Path**:
   - Open `agent_config.json`
   - Verify `paths.acs_exe` points to correct location
   - Default: `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\acs.exe`

2. **Check Permissions**:
   - Agent must run as same user who plays AC
   - Check `agent.log` for "Permission denied" errors

3. **Check if AC Already Running**:
   - Only one AC instance allowed per PC
   - Agent will return "Already running" if AC is active

### Presets Not Applying

1. **Check Preset Files Exist**:
   - Verify files in `C:\PitBox\installed\presets\steering\` and `presets\assists\`
   - Presets must have `.ini` or `.cmpreset` extensions

2. **Check Destination Paths**:
   - `ac_savedsetups` must exist
   - `cm_assists_presets` must exist
   - Check `agent.log` for file I/O errors

3. **Check Write Permissions**:
   - Agent must have write access to AC config directories
   - Test: Create a file manually in the destination folder

### Controller UI Not Loading

1. **Check Static Files**:
   - In dev: `controller\static\` must exist
   - In EXE: Static files bundled in `sys._MEIPASS`
   - Check `controller.log` for "Static directory not found"

2. **Check Port Binding**:
   - Default: `127.0.0.1:9600`
   - Verify nothing else is using port 9600
   - Check `controller.log` for "Address already in use"

3. **Clear Browser Cache**:
   - Hard refresh: Ctrl+F5
   - Try incognito/private window

### Logs Not Writing

1. **Check Log Directory**:
   - Agent logs: `C:\PitBox\Agent\logs\agent.log`
   - Controller logs: `C:\PitBox\Controller\logs\controller.log`
   - Directories created automatically if missing

2. **Check Permissions**:
   - Log directories need write permissions
   - Right-click logs folder → Properties → Security

3. **Check Disk Space**:
   - Logs rotate at 10MB (3 backups = 40MB max)
   - Verify disk not full

## API Documentation

See [protocol.md](protocol.md) for complete HTTP API specification.

## Project Structure

```
C:\PitBox\dev\pitbox\
├── agent\              # Agent service code
├── controller\         # Controller service code
│   └── static\         # Web UI files
├── scripts\            # Build and setup scripts
├── examples\           # Example configs
├── protocol.md         # API documentation
├── README.md           # This file
├── requirements.txt    # Python dependencies
└── version.txt         # Version number
```

## Development Notes

**DEV vs INSTALLED**:
- Development work: `C:\PitBox\dev\pitbox\`
- Production runtime: `C:\PitBox\installed\`
- **Never mix these directories**

**Script Safety**:
- All dev scripts require `-Dev` flag to prevent accidental execution
- Example: `.\scripts\build_release.ps1 -Dev`

**Logging**:
- Configured before FastAPI/uvicorn import to avoid stdout crashes in EXE
- Safe for PyInstaller frozen environment

## Security Considerations

1. **LAN-Only**: No internet exposure, no TLS required
2. **Bearer Tokens**: Change from defaults, use 32+ character random strings
3. **No Authentication on Controller UI**: Assumes trusted LAN
4. **Path Traversal Protection**: Preset names validated to prevent `../` attacks
5. **Windows Firewall**: Only open required ports

## License

(Add your license here)

## Support

For issues and questions, see the project repository.

---

**PitBox v1.0.0** - Built for Fastest Lap Racing Lounge
