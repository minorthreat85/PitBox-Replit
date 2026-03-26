# PitBox Controller v1.4.1

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
- `pitbox_common/` — Shared utilities (ports, runtime_paths, etc.)
- `ui/` — UI source assets (branding)
- `tools/` — Utilities and installer scripts

## Dependencies

Installed via pip:
- fastapi, uvicorn, httpx, pydantic, pydantic-settings, python-dotenv, psutil, anyio
