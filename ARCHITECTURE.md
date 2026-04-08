# PitBox Architecture: User-Session vs Service

## Critical Design Principle

**PitBoxAgent MUST run as the logged-in user, NEVER as SYSTEM.**

Running Agent as SYSTEM causes Assetto Corsa to launch headless (process exists, but no window appears).

---

## Component Execution Context

| Component | Runs As | Why |
|-----------|---------|-----|
| **PitBoxAgent** | Logged-in user (e.g., `info`) | Must launch AC with visible window |
| **PitBoxController** | SYSTEM (Windows Service) | Admin PC, no game launch, background operation |

---

## Sim PC (Agent) - Expected Flow

### 1. System Boot
```
Windows boots
↓
Auto-login as sim user (e.g., "info")
↓
User session starts
```

### 2. Startup Sequence
```
Steam.exe starts (via Registry or Startup folder)
↓
PitBoxAgent.exe starts (via Scheduled Task - ONLOGON, Run only when user is logged on)
↓
Agent listens on its assigned port (9631–9638, one per sim PC)
  (Runs as logged-in user - CRITICAL for AC to show game window)
```

### 3. Game Launch
```
Controller sends POST /start
↓
Agent runs: acs.exe
↓
AC window appears on screen (CRITICAL)
↓
Agent brings AC window to foreground (Win32 focus routine)
↓
AC is the active, focused window ✅
```

### 4. Task Manager Verification
```
Details tab:
  - PitBoxAgent.exe → User: info
  - acs.exe → User: info
```

---

## Admin PC (Controller) - Expected Flow

### 1. System Boot
```
Windows boots
↓
PitBoxController service starts (SYSTEM)
↓
Web UI available at http://127.0.0.1:9600
```

### 2. Why Service is OK Here
- Controller doesn't launch games
- Runs on admin PC (not sim PC)
- Needs to start before user login
- Web UI must be always available

---

## Installation Details

### Agent Installation (Sim PCs)
1. Files copied to `C:\PitBox\`
2. **Scheduled Task** created (NOT Windows Service):
   - Task Name: PitBox Agent
   - Trigger: ONLOGON (when user logs on)
   - Run as: Logged-in user (NOT SYSTEM)
   - Command: `PitBoxAgent.exe --config "C:\PitBox\agent_config.json"`
3. Any existing PitBoxAgent **service** is removed (migration from old installs)
4. Firewall rule added (TCP 9631–9638 inbound on each sim, TCP 9630 inbound on admin PC)
5. **CRITICAL**: Agent must run as user so AC shows game window

### Controller Installation (Admin PC)
1. Files copied to `C:\PitBox\`
2. Windows Service created via NSSM:
   - Service Name: `PitBoxController`
   - Startup: Automatic
   - Runs as: SYSTEM
   - Port: 9630 (Controller on Admin PC)
3. Service starts immediately
4. **IMPORTANT**: Edit each sim's agent_config.json to set the correct agent_port (9631 for Sim1, 9632 for Sim2, … 9638 for Sim8)

---

## Safety Mechanisms

### 1. Runtime User Check (Agent)
Agent checks `USERNAME` environment variable at startup:
```python
if username in ['SYSTEM', 'LOCAL SERVICE', 'NETWORK SERVICE']:
    print("FATAL ERROR: PitBoxAgent is running as SYSTEM")
    sys.exit(1)
```

### 2. Scheduled Task (Not Service)
Installer creates **Scheduled Task** (ONLOGON), NOT:
- ❌ Windows Service
- ❌ Task Scheduler with "Run whether user is logged on or not"
- ❌ Elevated or SYSTEM context

---

## Sim PC Setup Checklist

### Required Configuration
- [ ] Windows auto-login enabled for sim user
- [ ] Steam set to start on login
- [ ] PitBoxAgent shortcut in Startup folder (created by installer)
- [ ] User has permission to run executables

### Verify Installation
1. Log in as sim user
2. Open Task Manager → Details tab
3. Check: `PitBoxAgent.exe` → User column shows `info` (not SYSTEM)
4. Open Controller web UI
5. Launch AC from a sim PC
6. Verify: AC window appears on sim PC screen

---

## Troubleshooting

### Problem: AC Launches Headless
**Symptom**: `acs.exe` process exists, but no window appears.

**Cause**: Agent running as SYSTEM.

**Check**:
```powershell
Get-Process PitBoxAgent | Select-Object Name, Id, SessionId
Get-Process acs -ErrorAction SilentlyContinue | Select-Object Name, Id, SessionId
```

**Solution**:
1. Kill any SYSTEM-context Agent:
   ```powershell
   Stop-Process -Name PitBoxAgent -Force
   ```
2. Remove any services or scheduled tasks
3. Start Agent manually to test:
   ```
   C:\PitBox\installed\bin\PitBoxAgent.exe --config "C:\PitBox\installed\config\agent.json"
   ```
4. Verify Task Manager shows Agent running as your user
5. Test AC launch from Controller UI

---

## What Was Changed (Feb 2026)

### Previous (INCORRECT) Architecture
- Agent ran as Windows Service
- Ran as SYSTEM user
- AC launched headless

### Current (CORRECT) Architecture
- Agent runs via Startup folder
- Runs as logged-in user
- AC launches with visible window

### Files Modified
1. `installer/pitbox.iss` - Removed Agent service logic, added Startup shortcut
2. `agent/main.py` - Added SYSTEM user detection and fatal error on detection
3. `PitBoxAgent.spec` - Kept `console=False` for windowless operation (but still user-session)

### Files Unchanged
- `controller/main.py` - Still supports `--service` flag (Controller DOES run as service)
- `PitBoxController.spec` - Still builds windowless (correct for service)
- NSSM integration - Still used, but only for Controller

---

## FAQ

### Q: Why is Agent windowless but not a service?
**A**: `console=False` means "no console window" (no black CMD window). It does NOT mean "run as SYSTEM". Agent runs windowless in the user session, which is correct.

### Q: Does Controller still run as a service?
**A**: YES. Controller runs on the admin PC and doesn't launch games, so running as SYSTEM is fine.

### Q: Can I use Task Scheduler instead of Startup folder?
**A**: NO. Task Scheduler with "Run whether user is logged on or not" runs as SYSTEM, which breaks AC.

### Q: What about security/auto-restart?
**A**: For a sim lounge, user-session is correct. If Agent crashes, it won't auto-restart, but:
- Sim PCs are managed/monitored
- Agent is stable (no complex logic)
- User can restart Agent manually from Start Menu

### Q: Does AC always come to the foreground after launch?
**A**: YES. Agent includes a Win32 window-focus routine that polls for the AC window and brings it to foreground using `SetForegroundWindow` and related APIs. This ensures AC is always the focused window, even if Chrome or other apps were active. See `FOREGROUND_FOCUS_FIX.md` for details.

### Q: Why don't agents use port 9600?
**A**: Port 9600 conflicts with SimHub motion telemetry on sim PCs. Agents MUST use ports 9631–9638 (Sim1=9631, Sim2=9632, … Sim8=9638). The Controller runs on port 9630 (Admin PC). See `PORT_SCHEME.md` for details.

---

## Summary

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| Agent runs as user | Startup folder shortcut | ✅ Implemented |
| Agent never runs as SYSTEM | Runtime check + exit | ✅ Implemented |
| No Agent service | Service logic removed from installer | ✅ Implemented |
| AC window appears | User-session context | ✅ Fixed |
| AC gains foreground focus | Win32 focus routine | ✅ Implemented |
| Controller as service | NSSM + automatic startup | ✅ Kept (correct) |
| SimHub motion compatibility | Port scheme (Controller=9630, Sims=9631–9638) | ✅ Implemented |

**PitBox is now production-ready for sim lounge deployment.**
