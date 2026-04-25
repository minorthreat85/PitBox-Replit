# PitBox Controller v1.6.1

Professional LAN-based management system for Assetto Corsa racing lounges with up to 8 simulator PCs.

## Architecture

- **Backend**: Python 3.11 + FastAPI + uvicorn
- **Frontend**: Pre-built static web UI served by FastAPI
- **Config**: `~/.config/PitBox/Controller/controller_config.json` (via XDG_CONFIG_HOME)

## Running

The application starts with:
```
python3 -m controller.main
```

It runs on **port 5000** (configured for Replit). The workflow "Start application" handles this automatically.

## Key Configuration

Config file at: `/home/runner/workspace/.config/PitBox/Controller/controller_config.json`
- `ui_port`: 5000 (Replit webview port)
- `ui_host`: "0.0.0.0" (bind all interfaces)
- `allow_lan_ui`: true

## Port Scheme (original, for reference)

| Service | Port |
|---------|------|
| PitBoxController | 9630 (overridden to 5000 for Replit) |
| Sim1 Agent | 9631 |
| Sim2–8 Agents | 9632–9638 |

## Project Structure

- `controller/` -- FastAPI backend (main.py, api_routes.py, config.py, etc.)
- `controller/release_service.py` -- Release discovery/caching (single authority for GitHub release data)
- `controller/fleet_state.py` -- Persistent per-agent rollout state (JSON)
- `controller/api_update_routes.py` -- Clean update routes (`/api/update/controller/*`, `/api/update/fleet/*`)
- `controller/static/` -- Pre-built web UI (index.html, app.js, etc.)
- `agent/` -- PitBox Agent (runs on each sim PC)
- `pitbox_common/` -- Shared utilities (ports, runtime_paths, version.py)
- `installer/` -- Inno Setup scripts (agent.iss, controller.iss, pitbox.iss)
- `scripts/` -- Build, deploy, publish scripts
- `ui/` -- UI source assets (branding)
- `tools/` -- Utilities and installer scripts

## Update System (v1.6.0+ refactor)

### Unified (primary)
- `POST /api/update/run`               -- One-click: check, update controller, roll out fleet
- `GET  /api/update/summary`           -- Unified system-wide status (controller + fleet + agents)

### Granular (Advanced section)
- `GET  /api/update/controller/status` -- Controller release + updater state
- `POST /api/update/controller/check`  -- Force refresh from GitHub
- `POST /api/update/controller/apply`  -- Start controller update only
- `GET  /api/update/fleet/status`      -- All sims update status + summary
- `POST /api/update/fleet/start`       -- Begin update on selected/all sims
- `POST /api/update/fleet/cancel`      -- Cancel pending updates
- `POST /api/update/fleet/retry`       -- Retry failed updates
- `GET  /api/update/releases`          -- List available releases

### Legacy routes (kept for backward compat)
- `/update/status`, `/update/apply`, `/update/run-installer` -- shims in api_routes.py, import from release_service
- `/agents/push-update`, `/agents/update-status`, `/agents/releases`, `/agents/cancel-updates`

### Key modules
- `controller/release_service.py` -- Single authority for release discovery/caching (GitHub API, semver, asset matching)
- `controller/updater.py` -- Installer execution only (download, SHA-256 verify, silent install); imports release metadata from release_service
- `controller/fleet_state.py` -- Persists per-agent rollout state to `C:\PitBox\data\fleet_rollout_state.json`
- `tools/update_pitbox.ps1` -- CLI fallback for recovery/offline scenarios (no longer primary path)
- Agent autonomous update check disabled at startup (controller-driven updates only)

## Versioning

- `version.txt` is the single source of truth (currently 1.5.10)
- `pitbox_common/version.py` reads `version.txt` dynamically at import
- `version.ini` is synced from `version.txt` for Inno Setup
- `scripts/sync_version.py` syncs version.ini and VERSION from version.txt

## Canonical Runtime Paths (Windows)

| Component | Path |
|-----------|------|
| Agent executable | `C:\PitBox\Agent\bin\PitBoxAgent.exe` |
| Agent config | `C:\PitBox\Agent\config\agent_config.json` |
| Updater | `C:\PitBox\updater\PitBoxUpdater.exe` |
| Downloads | `C:\PitBox\downloads\` |
| Logs | `C:\PitBox\logs\` |

## Build Pipeline

Run `.\scripts\build_release.ps1 -Dev` from the repo root on Windows. Outputs:
- `dist\PitBoxAgent.exe` — Agent binary
- `dist\PitBoxController.exe` — Controller binary
- `dist\PitBoxUpdater.exe` — Installer-based updater
- `dist\PitBoxInstaller_<ver>.exe` — Unified installer (from `installer/pitbox.iss`)
- `dist\PitBoxAgentSetup_<ver>.exe` — Standalone agent installer (from `installer/agent.iss`)
- `dist\PitBoxControllerSetup_<ver>.exe` — Controller installer (from `installer/controller.iss`)

Publish: `.\scripts\publish_release.ps1 -Dev` uploads all installers to GitHub Releases.

## Agent Update Flow (v1.6.0+)

Controller is the single release authority. Operator clicks "Update PitBox" → `POST /api/update/run` → controller checks GitHub, updates itself if needed, then rolls out to all enrolled sims. Idle sims update immediately; busy sims are queued as `pending_idle` and auto-update when AC session ends. Agents no longer check GitHub autonomously.

## Sim-side Telemetry Pipeline (v1.5.10+)

Each Agent reads AC's shared-memory blocks (`acpmf_physics`, `acpmf_graphics`,
`acpmf_static`) at a configurable rate (default 15 Hz) and ships frames over a
persistent WebSocket to the Controller. The Controller merges these per-sim
frames with the existing UDP server-timing feed for a richer Live Timing UI.

- **Agent**: `agent/telemetry/sm_reader.py` (mmap parser), `agent/telemetry/sender.py`
  (persistent WS w/ auto-reconnect). Wired into `agent/main.py`; controlled by
  config keys `telemetry_enabled` (default true) and `telemetry_rate_hz`.
  No-op on non-Windows / when AC isn't running.
- **Controller**: `controller/telemetry/store.py` (per-agent latest frame +
  staleness buckets: live <3s, stale <15s, offline ≥15s),
  `controller/api_telemetry_ingest.py` (WS `/ws/agent-telemetry` + HTTP
  `/api/telemetry/*`). The WS handler **always overwrites the agent's `ts`
  with server receive time** before storing — agent wall-clock skew
  (Windows Time service unsynced after a fresh install, etc.) used to push
  every frame past the 15 s offline cutoff and hide the agent from the
  SIM AGENTS bar even at full 15 Hz. The original agent timestamp is
  preserved as `agent_ts`, and skew >5 s logs a one-time WARN per agent
  (v1.6.1 fix).
- **Snapshot merge**: `controller/timing/engine.py::snapshot()` adds
  `telemetry_agents` (top-level dict, keyed by agent_id) and `live_telemetry`
  per matched driver (best-effort name match). Existing fields untouched.
- **Live Timing UI**: `controller/static/index.html` + `live_timing.js`/`.css`
  render an agents bar, expanded leaderboard (Spd/Gear/RPM live cols), SVG
  track map (cars positioned by normalized lap distance), driver-detail panel
  with throttle/brake bars and gauges, and a recent-events panel.
- **Track-map JSON files**: `controller/static/track_maps/<key>.json` where
  `<key> = slugify(track_name) + "__" + slugify(track_config)` (or just
  `slugify(track_name)` for tracks without layouts). Contract:
  `{viewBox, svg_path, start_offset, direction, scale}`. Cars are placed
  along `svg_path` with `getPointAtLength()`.

### Automated Track-Map Generation (build-time)

`scripts/generate_track_maps.py` walks an Assetto Corsa `content/tracks/` tree,
runs `scripts/track_map_generator.py` against every `map.png`, and writes
JSON files into `controller/static/track_maps/`. Pipeline: load PNG → binary
mask → largest connected component → skeletonize → prune dead-end branches
(removes pit lane / escape roads) → walk closed cycle → Ramer–Douglas–Peucker
simplify → emit `M x y L x y ... Z`. Hand-traced JSONs (no `generated_by`
field) are never overwritten unless `--force` is passed.

This is a BUILD-TIME tool only. Its deps (`numpy`, `scikit-image`) live in
`requirements-tools.txt` and are NOT included in either PyInstaller spec.

Typical usage on the dev / main PC:
```
pip install -r requirements-tools.txt
python -m scripts.generate_track_maps --ac-root "C:\Program Files (x86)\Steam\steamapps\common\assettocorsa"
```
Tests: `python -m unittest scripts.tests.test_track_map_generator`.

PyInstaller specs include the new submodules:
- `PitBoxAgent.spec` — `agent.telemetry.*`, `websockets.asyncio.client`
- `PitBoxController.spec` — picked up by `collect_submodules('controller')`

## Debug Endpoints

- Agent: `GET /debug/environment` — returns updater status, browser, controller_url, pairing, version
- Controller: `GET /agents/debug-environment` — queries all enrolled sims for diagnostics
- UI: "Run Sim Diagnostics" button in Updates tab

## GitHub Sync & Local Rebuild Workflow

After every session where code changes are made:
1. Push changed files to GitHub (`minorthreat85/PitBox-Replit`) via the GitHub REST API using `GITHUB_PERSONAL_ACCESS_TOKEN_PITBOX_REPLIT`.
2. Skip `examples/controller_config.json` (contains secrets, flagged by GitHub's secret scanner).
3. The user then pulls on their local Windows machine (`C:\Users\info\pitbox\`) and runs:
   ```
   .\update.ps1
   ```

**This push-and-rebuild step is always required after code changes.**

## Dependencies

Installed via pip:
- fastapi, uvicorn, httpx, pydantic, pydantic-settings, python-dotenv, psutil, anyio
