# PitBox: Enrollment / Pairing with Auto-Discovery (LAN)

## CRITICAL CONSTRAINT: DO NOT MODIFY FILE PATHS

### ABSOLUTE RULE

The following config fields are **LOCAL, MACHINE-SPECIFIC, AND IMMUTABLE**.  
Enrollment / pairing must **NOT modify, overwrite, generate, or centralize** them:

- `paths.acs_exe`
- `paths.ac_cfg_dir`
- `paths.savedsetups_dir`
- `paths.cm_assists_presets_dir`
- `listen_host`
- `port`

These values already exist in each agent's `agent_config.json` and must remain untouched.

**Enrollment logic is allowed to read them** (e.g. to display or validate), but **never change them**.

- The **controller** only adds/updates entries in **controller_config.json** (agents list: id, host, port, token). The controller never writes to any agent's `agent_config.json`.
- The **agent** never modifies its own `agent_config.json` for enrollment; it only sends a LAN beacon (agent_id, port) for discovery.

### Reference agent_config.json structure (DO NOT change these keys on the agent)

```json
{
  "agent_id": "Sim5",
  "token": "sim5",
  "listen_host": "0.0.0.0",
  "port": 9635,
  "paths": {
    "acs_exe": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe",
    "ac_cfg_dir": "%USERPROFILE%\\Documents\\Assetto Corsa\\cfg",
    "savedsetups_dir": "%USERPROFILE%\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups",
    "cm_assists_presets_dir": "%LOCALAPPDATA%\\AcTools Content Manager\\Presets\\Assists"
  }
}
```

### Auto-discovery flow

1. **Agent** (optional): Sends UDP beacon on LAN with `agent_id` and `port` (no token on the wire).
2. **Controller**: Listens for beacons, exposes list of discovered agents.
3. **User**: Clicks "Add" on a discovered agent and enters the **token** (from the agent machine's `agent_config.json`).
4. **Controller**: Appends the agent to `controller_config.json` (id, host, port, token) and saves. No agent config file is modified.
