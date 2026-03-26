# Controller API — security notes

## Source of truth for GET auth

**[`API_GET_ROUTES.md`](API_GET_ROUTES.md)** lists every **`GET /api/*`** route, whether it is **Public**, **Conditional** (`require_operator_if_password_configured`), or **Operator** (`require_operator`), and a one-line rationale. That table is maintained to match the code.

## Auth primitives

- **`require_operator`**: employee cookie when `employee_password` is set; otherwise **localhost-only** for remote clients (`403` off-box).
- **`require_operator_if_password_configured`**: if `employee_password` is set → same cookie checks as `require_operator`; if unset → **no gate** (legacy LAN UI without operator login).

## Mutating / non-GET (summary)

Dangerous and state-changing routes use **`require_operator`** (or agent/kiosk/enrollment-specific checks). Examples: `POST /start`, `/stop`, steering/shifting, `POST /enrollment`, `PUT /config`, **`/api/server-config`** writes and acServer lifecycle, `POST /server/create`, `POST /logs/event` (**`require_agent`**), kiosk **`POST`** with session validation, etc. See `controller/api_routes.py`, `controller/api_server_config_routes.py`, and `controller/api_logs_pool_routes.py` for the full set.

## Kiosk behaviour

Kiosk and sim **`GET`** routes stay **Public** (see `API_GET_ROUTES.md`). The kiosk client tolerates **`401`/`403`** on optional calls (e.g. some catalogs, **`GET /api/status`**) when operator login is enabled, so car/track pickers can still load.

## Release / ops

See **[`RELEASE.md`](RELEASE.md)** for version and checklists.
