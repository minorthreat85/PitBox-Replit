# PitBox Controller v1.5.1

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

- `controller/` — FastAPI backend (main.py, api_routes.py, config.py, etc.)
- `controller/static/` — Pre-built web UI (index.html, app.js, etc.)
- `agent/` — PitBox Agent (runs on each sim PC)
- `pitbox_common/` — Shared utilities (ports, runtime_paths, version.py)
- `installer/` — Inno Setup scripts (agent.iss, controller.iss, pitbox.iss)
- `scripts/` — Build, deploy, publish scripts
- `ui/` — UI source assets (branding)
- `tools/` — Utilities and installer scripts

## Versioning

- `version.txt` is the single source of truth (currently 1.5.1)
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

## Agent Update Flow

Controller sends `POST /update` to agents → agent queries GitHub releases → locates `PitBoxUpdater.exe` at `C:\PitBox\updater\` → launches updater with installer URL and SHA-256 → updater downloads, verifies, and runs the installer → agent service restarts.

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
