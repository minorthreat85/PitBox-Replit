# PitBox HTTP API Protocol

This document specifies the HTTP API for communication between PitBox Controller and Agents.

## Overview

- **Transport**: HTTP/1.1
- **Authentication**: Bearer token (except `/ping`)
- **Content-Type**: `application/json`
- **Default Ports**: Agent 9600, Controller 9600

## Agent Endpoints

### GET /ping

Health check endpoint (no authentication required).

**Request**: None

**Response** (200 OK):
```json
{
  "status": "ok",
  "agent_id": "Sim1"
}
```

**Error Codes**:
- None (always returns 200 if agent is running)

---

### GET /status

Get current agent and AC status.

**Authentication**: Required

**Request**: None

**Response** (200 OK):
```json
{
  "agent_id": "Sim1",
  "ac_running": true,
  "pid": 12345,
  "uptime_sec": 120.5
}
```

**Fields**:
- `agent_id` (string): Agent identifier
- `ac_running` (boolean): Whether AC is currently running
- `pid` (int|null): Process ID if running, null otherwise
- `uptime_sec` (float|null): Seconds since AC started, null if not running

**Error Codes**:
- 401: Invalid or missing token

---

### GET /process

Get detailed process information.

**Authentication**: Required

**Request**: None

**Response** (200 OK):
```json
{
  "running": true,
  "pid": 12345,
  "exe_path": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\acs.exe",
  "uptime_sec": 120.5
}
```

**Fields**:
- `running` (boolean): Whether AC is currently running
- `pid` (int|null): Process ID if running
- `exe_path` (string): Path to acs.exe from config
- `uptime_sec` (float|null): Seconds since AC started

**Error Codes**:
- 401: Invalid or missing token

---

### POST /start

Start Assetto Corsa, optionally applying a steering preset first.

**Authentication**: Required

**Request**: Optional JSON object
```json
{
  "steering_preset": "1 Race"
}
```

**Fields**:
- `steering_preset` (string, optional): Name of steering preset to apply before launch (without .ini extension)

**Response** (200 OK):
```json
{
  "success": true,
  "pid": 12345,
  "message": "Started with PID 12345"
}
```

**Behavior**:
1. If `steering_preset` is provided:
   - Validates preset exists in AC savedsetups directory
   - Backs up existing `controls.ini` to `controls.ini.bak`
   - Writes preset content to `controls.ini.tmp`
   - Atomically replaces `controls.ini` with temp file
   - Verifies `controls.ini` exists and has non-zero size
   - Only then launches acs.exe
2. If preset application fails, returns 400 error and does NOT launch AC
3. If already running, returns success without applying preset (idempotent)

**Success Conditions**:
- AC was not running and started successfully
- AC was already running (idempotent)
- If steering preset specified: preset applied successfully before launch

**Error Codes**:
- 400: Invalid steering preset name or preset not found (AC NOT launched)
- 401: Invalid or missing token
- 404: acs.exe not found at configured path
- 500: Failed to start process or apply preset

---

### POST /stop

Stop Assetto Corsa.

**Authentication**: Required

**Request**: Empty JSON object
```json
{}
```

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Stopped successfully"
}
```

**Success Conditions**:
- AC was running and stopped successfully
- AC was not running (idempotent)

**Behavior**:
1. Send graceful termination signal (SIGTERM)
2. Wait up to 5 seconds for process to exit
3. If still running, force kill (SIGKILL)

**Error Codes**:
- 401: Invalid or missing token
- 500: Failed to stop process

---

### GET /presets/steering

List available steering presets from AC savedsetups directory.

**Authentication**: Required

**Request**: None

**Response** (200 OK):
```json
{
  "items": ["1 Race", "2 Kids", "10 Expert"]
}
```

**Fields**:
- `items` (array[string]): Available steering preset names (without .ini extension), naturally sorted

**Behavior**:
- Scans AC savedsetups directory (configured in `paths.ac_savedsetups`)
- Returns all `.ini` files without extension
- Natural sort: "1 Race" before "10 Expert" before "2 Kids"
- Security: Only returns files actually inside savedsetups directory (path traversal protection)
- Returns empty array if directory doesn't exist or is empty

**Error Codes**:
- 401: Invalid or missing token

---

### GET /presets

List available steering and assists presets (legacy endpoint).

**Authentication**: Required

**Request**: None

**Response** (200 OK):
```json
{
  "steering": ["1 Race", "2 Kids", "10 Expert"],
  "assists": ["Kids", "Adults", "Expert"]
}
```

**Fields**:
- `steering` (array[string]): Available steering preset names from AC savedsetups directory
- `assists` (array[string]): Available assists preset names (without .cmpreset extension)

**Behavior**:
- Returns empty arrays if preset directories don't exist or are empty
- Steering presets from AC savedsetups, naturally sorted
- Assists presets from managed templates directory

**Error Codes**:
- 401: Invalid or missing token

---

### POST /apply_steering_preset

Apply a steering preset.

**Authentication**: Required

**Request**:
```json
{
  "name": "Kids"
}
```

**Fields**:
- `name` (string): Preset name (without .ini extension)

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Applied Kids.ini"
}
```

**Behavior**:
1. Validate preset exists in `managed_steering_templates`
2. Backup existing preset in `ac_savedsetups` (if present) as `.ini.bak`
3. Copy template to `ac_savedsetups`

**Error Codes**:
- 401: Invalid or missing token
- 404: Preset not found
- 500: File I/O error

---

### POST /apply_assists_preset

Apply an assists preset.

**Authentication**: Required

**Request**:
```json
{
  "name": "Kids"
}
```

**Fields**:
- `name` (string): Preset name (without .cmpreset extension)

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Applied Kids.cmpreset"
}
```

**Behavior**:
1. Validate preset exists in `managed_assists_templates`
2. Backup existing preset in `cm_assists_presets` (if present) as `.cmpreset.bak`
3. Copy template to `cm_assists_presets`

**Error Codes**:
- 401: Invalid or missing token
- 404: Preset not found
- 500: File I/O error

---

### GET /debug/controls_hash

Get information about AC's controls.ini file for debugging.

**Authentication**: Required

**Request**: None

**Response** (200 OK):
```json
{
  "path": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa\\cfg\\controls.ini",
  "exists": true,
  "size": 4567,
  "mtime": 1707753600.123,
  "mtime_iso": "1707753600.123",
  "sha256": "a1b2c3d4e5f6..."
}
```

**Fields**:
- `path` (string): Full path to controls.ini
- `exists` (boolean): Whether file exists
- `size` (int, optional): File size in bytes
- `mtime` (float, optional): Last modified timestamp (Unix epoch)
- `mtime_iso` (string, optional): Last modified timestamp as string
- `sha256` (string, optional): SHA256 hash of file content
- `error` (string, optional): Error message if file info cannot be retrieved

**Behavior**:
- Returns information about the current controls.ini file
- Used for testing/debugging to verify preset application
- Hash changes when preset is applied

**Error Codes**:
- 401: Invalid or missing token

**Note**: This endpoint is for local LAN debugging only.

---

## Controller Endpoints

### GET /api/status

Get status of all agents including steering presets.

**Authentication**: None (local only)

**Request**: None

**Response** (200 OK):
```json
{
  "agents": [
    {
      "agent_id": "Sim1",
      "online": true,
      "error": null,
      "ac_running": true,
      "pid": 12345,
      "uptime_sec": 120.5,
      "available_presets": {
        "steering": ["1 Race", "2 Kids", "10 Expert"],
        "assists": ["Kids", "Adults", "Expert"]
      },
      "steering_presets": ["1 Race", "2 Kids", "10 Expert"],
      "last_check": "2026-02-12T10:30:00"
    }
  ]
}
```

**Fields**:
- `agent_id` (string): Agent identifier
- `online` (boolean): Agent reachable
- `error` (string|null): Error type ("UNREACHABLE", "UNAUTHORIZED", "OTHER")
- `ac_running` (boolean): AC running status
- `pid` (int|null): AC process ID
- `uptime_sec` (float|null): AC uptime in seconds
- `available_presets` (object): Available preset names (legacy)
- `steering_presets` (array[string]): Steering presets from agent /presets/steering endpoint (cached, updated every ~30s)
- `last_check` (string): ISO timestamp of last status check

**Caching**:
- `steering_presets` are fetched on initial connection
- Then refreshed every 20 polls (~30 seconds with 1.5s interval)
- Cached between fetches to reduce network traffic

---

### POST /api/start

Start one or more sims, optionally with a steering preset.

**Authentication**: None (local only)

**Request**:
```json
{
  "sim_ids": ["Sim1", "Sim3"],
  "steering_preset": "1 Race"
}
```

OR

```json
{
  "all": true,
  "steering_preset": "1 Race"
}
```

**Fields**:
- `sim_ids` (array[string], optional): List of sim IDs to start
- `all` (boolean, optional): Start all configured sims
- `steering_preset` (string, optional): Name of steering preset to apply before launch

**Response** (200 OK):
```json
{
  "results": {
    "Sim1": {
      "success": true,
      "pid": 12345,
      "message": "Started with PID 12345"
    },
    "Sim3": {
      "success": false,
      "message": "Failed to apply preset: Preset 'invalid' not found"
    }
  }
}
```

**Behavior**:
- If `steering_preset` is provided, forwards to agent `/start` endpoint
- If agent returns 400 (preset error), includes error message in results
- Controller does NOT apply presets itself, only forwards to agent

**Error Codes**:
- 400: Must specify 'sim_ids' or 'all=true', or invalid sim IDs

---

### POST /api/stop

Stop one or more sims.

**Authentication**: None (local only)

**Request**: Same as `/api/start`

**Response**: Same structure as `/api/start`

---

### POST /api/apply_steering

Apply steering preset to a sim.

**Authentication**: None (local only)

**Request**:
```json
{
  "sim_id": "Sim1",
  "preset_name": "Kids"
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Applied Kids.ini"
}
```

**Error Codes**:
- 500: Agent request failed

---

### POST /api/apply_assists

Apply assists preset to a sim.

**Authentication**: None (local only)

**Request**: Same structure as `/api/apply_steering`

**Response**: Same structure as `/api/apply_steering`

---

### POST /api/refresh_presets

Manually refresh steering presets from all agents.

**Authentication**: None (local only)

**Request**: None (empty body)

**Response** (200 OK):
```json
{
  "status": "ok",
  "message": "Presets refreshed"
}
```

**Behavior**:
- Forces immediate fetch of `/presets/steering` from all agents
- Updates cached preset data
- Next `/api/status` call will include updated presets

**Use Case**:
- User adds/removes preset files on agents
- User wants immediate update without waiting 30 seconds

---

### GET /api/health

Health check for controller.

**Authentication**: None (local only)

**Request**: None

**Response** (200 OK):
```json
{
  "status": "ok"
}
```

---

## Authentication

Agent endpoints (except `/ping`) require Bearer token authentication.

**Header**:
```
Authorization: Bearer <token>
```

**Example**:
```
Authorization: Bearer changeme_agent_token
```

**Error Response** (401):
```json
{
  "detail": "Invalid token"
}
```

## Error Handling

All endpoints return errors in consistent format:

```json
{
  "detail": "Human-readable error message"
}
```

**Common Status Codes**:
- 200: Success
- 400: Bad request (invalid JSON, missing fields)
- 401: Unauthorized (invalid/missing token)
- 404: Resource not found (preset, acs.exe)
- 500: Internal server error

## Polling Strategy

Controller polls agents every 1.5 seconds (configurable) using:

1. GET /ping (no auth) - Check if agent is online
2. GET /status (with auth) - Get AC status
3. GET /presets (with auth) - Get available presets

Polling is concurrent with 1.0 second timeout per agent.

## Network Configuration

**Agent** (Sim PCs):
- Bind to `0.0.0.0:9600` (accept connections from LAN)
- Firewall: Allow inbound TCP 9600

**Controller** (Admin PC):
- Bind to `127.0.0.1:9600` by default (localhost only)
- If `allow_lan_ui=true`: Bind to `0.0.0.0:9600` (accept connections from LAN)
- Firewall: Allow inbound TCP 9600 if LAN UI enabled

## Security Considerations

1. **No TLS**: HTTP only (LAN-only deployment)
2. **Bearer Tokens**: Must be changed from defaults
3. **No Rate Limiting**: Trust LAN environment
4. **No CSRF Protection**: Controller UI assumes trusted LAN
5. **Path Traversal**: Preset names validated to prevent `../` attacks
