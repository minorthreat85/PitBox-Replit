# GET routes — source of truth (controller)

All **`/api/*`** paths below are served by `controller/api_routes.py` (main router) plus included routers: `controller/api_server_config_routes.py` (server INI, blacklist, acServer start/stop, etc.) and `controller/api_logs_pool_routes.py` (structured logs + dynamic pool). Shared path helpers live in `controller/ac_paths.py`. **`APIRouter(prefix="/api")`** is defined in `api_routes.py`; paths in code omit that prefix (e.g. `@router.get("/status")` → **`GET /api/status`**).

## Legend

| Label | Meaning |
|-------|---------|
| **Public** | No `Depends(...)` on the handler — any LAN client may call it (subject to path/query validation). |
| **Conditional** | `require_operator_if_password_configured` — open to all clients when `employee_password` is unset; otherwise requires employee session cookie (`401` if missing). |
| **Operator** | `require_operator` — if password unset: **localhost-only** (`403` from other hosts); if password set: requires employee cookie (`401` if missing). |

Sim/kiosk GETs are **Public** but typically require a known `agent_id` (404 if invalid).

## `GET /api/*` table

| Route | Access | Why |
|-------|--------|-----|
| `/api/agents/registry` | Operator | Agent registry / topology |
| `/api/agents/discovered` | Operator | LAN discovery snapshot |
| `/api/enrollment` | Conditional | Enrollment state + **secret** (gated when login enabled) |
| `/api/employee/session` | Public | Read-only UI flags (login configured / cookie present) |
| `/api/status` | Conditional | Dashboard summary; gated when `employee_password` set |
| `/api/assignments/{agent_id}` | Conditional | Sim → server assignment |
| `/api/kiosk/pair-info` | Public | QR pairing payload for kiosk |
| `/api/sims/{agent_id}/kiosk-display` | Public | Sim screen kiosk bundle |
| `/api/sims/{agent_id}/state` | Public | Sim display state + optional `server_display` |
| `/api/sims/{agent_id}/presets` | Public | Steering/shifting preset names for kiosk |
| `/api/catalogs/cars` | Public | Car picker from installed content |
| `/api/catalogs/tracks` | Public | Track picker from installed content |
| `/api/catalogs/assists` | Conditional | Assist presets incl. file refs / policy |
| `/api/catalogs/servers` | Conditional | Join targets + constraints from presets/INI |
| `/api/servers` | Conditional | Server list for UI |
| `/api/servers/presets-info` | Operator | Cross-preset inventory |
| `/api/servers/{server_id}` | Conditional | Server detail card data |
| `/api/servers/{server_id}/current_config` | Conditional | Live cfg path state |
| `/api/preset/{preset_id}/disk_state` | Conditional | On-disk preset inspection |
| `/api/presets/disk_state` | Conditional | Batch disk state |
| `/api/debug/presets` | Operator | Debug preset scan |
| `/api/debug/favourites` | Operator | CM favourites resolution debug |
| `/api/servers/{server_id}/summary` | Conditional | Live server summary |
| `/api/sims/{agent_id}/server-display` | Conditional | Sim ↔ server linkage detail |
| `/api/version` | Public | Build/version string |
| `/api/update/status` | Conditional | Updater pipeline status |
| `/api/config` | Operator | Full controller config snapshot |
| `/api/server-config/raw` | Conditional | Raw INI read |
| `/api/server-config/revision` | Conditional | Config file revision |
| `/api/server-config/meta` | Conditional | Preset ids / names index |
| `/api/server-config` | Conditional | Parsed server config |
| `/api/server-config/blacklist` | Conditional | Blacklist read |
| `/api/server-config/process-status` | Conditional | AC server process status |
| `/api/server-config/process-log` | Conditional | AC server process log tail |
| `/api/cars` | Public | Car list + resolved path metadata |
| `/api/cars/{car_id}/display-name` | Public | Display name helper |
| `/api/cars/{car_id}/preview` | Public | Preview image |
| `/api/cars/{car_id}/skins/{skin_name}/livery` | Public | Livery image |
| `/api/cars/{car_id}/skins/{skin_name}/preview` | Public | Skin preview image |
| `/api/tracks/{track_id}/display-name` | Public | Track name helper |
| `/api/tracks/{track_id}/layouts/{layout}/info` | Public | `ui_track.json` fields |
| `/api/tracks/{track_id}/layouts/{layout}/preview` | Public | Track preview image |
| `/api/tracks/{track_id}/layouts/{layout}/outline` | Public | Outline image |
| `/api/tracks/{track_id}/layouts/{layout}/map` | Public | Map overlay image |
| `/api/tracks/{track_id}/layouts/{layout}/base` | Public | Base image |
| `/api/logs/events` | Operator | Filtered event log |
| `/api/logs/summary` | Operator | Log rollups |
| `/api/server/status` | Operator | Dynamic pool slot status |

**Implementation note:** `GET|PATCH|PUT|POST` under **`/api/server-config/*`** are defined in `controller/api_server_config_routes.py`. `GET /api/logs/*` and pool **`/api/server/*`** live in `controller/api_logs_pool_routes.py`. All are merged into the same `/api` URL space via `router.include_router(...)` from `api_routes.py`.

## Non-API GET (same app, `controller/main.py`)

| Route | Access | Why |
|-------|--------|-----|
| `GET /` | Public | Serves main UI shell |
| `GET /status` | Public | Plain-text liveness for local probes (not under `/api`) |
| `GET /sim`, `/kiosk`, `/employee`, static assets, etc. | Public | Pages and static files |

## Keeping this file accurate

When adding or changing a `GET` on the API router, update this table in the same change.
