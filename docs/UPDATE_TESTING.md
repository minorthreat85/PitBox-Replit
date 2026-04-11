# Update System Testing Guide

## Local Testing Without GitHub

### 1. Fake manifest (mock API)

Create a simple mock server that returns GitHub Releases–style JSON:

```python
# mock_github_releases.py
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "releases/latest" in self.path:
            data = {
                "tag_name": "v0.2.0",
                "name": "Test Release",
                "published_at": "2025-02-24T12:00:00Z",
                "html_url": "https://github.com/owner/repo/releases/tag/v0.2.0",
                "body": "## Changes\n- Test release",
                "assets": [
                    {
                        "name": "PitBoxControllerSetup_0.2.0.exe",
                        "browser_download_url": "http://127.0.0.1:9999/fake_installer.exe",
                        "size": 5000000
                    }
                ]
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args): pass

HTTPServer(("127.0.0.1", 9999), Handler).serve_forever()
```

1. Run the mock: `python mock_github_releases.py`
2. Add a hosts override or use a local proxy to redirect `api.github.com` to `127.0.0.1:9999`
3. Or: point `update_channel.github_owner` and `github_repo` to a **test repo** you control

### 2. Test repo on GitHub

1. Create a repo (e.g. `yourorg/pitbox-test-releases`)
2. Create a release with tag `v1.4.2` (or higher than current `1.4.1`)
3. Upload `PitBoxControllerSetup_0.2.0.exe` (or a dummy .exe with MZ header)
4. In `controller_config.json`:

```json
{
  "update_channel": {
    "github_owner": "yourorg",
    "github_repo": "pitbox-test-releases"
  }
}
```

5. Restart controller, open Settings → Updates
6. You should see "Update available" and the "Update Controller" button

### 3. Test download verification

- Use a dummy installer: create a file with `MZ` as first 2 bytes and size > 1MB
- Or temporarily lower `min_installer_size_mb` to `0.001` for a tiny test file
- A file without `MZ` or below the size threshold should be rejected with a clear error

### 4. POST /api/update/apply (no body required)

- `POST /api/update/apply` accepts **no body**, empty `{}`, or `{"target": "controller"}`; all default to controller update.
- Example (PowerShell): `Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9630/api/update/apply"` (no body).
- Unit tests: `controller/tests/test_update_apply.py` (POST with no body and with `{}` return 200).

### 5. Test graceful shutdown

1. Start controller
2. Trigger update apply (Settings → Updates → Update Controller)
3. Confirm:
   - HTTP 200 with `{"ok": true, "message": "Installer launched"}`
   - Controller stops within ~2 seconds
   - If running as NSSM service, service stops cleanly (no crash)

### 6. Test PUT /config restriction

- From `127.0.0.1`: PUT /api/config should work
- From another host (e.g. LAN): should return 403 "Config updates allowed only from localhost"

### 7. Unified orchestrator (POST /api/update/run)

1. Start controller.
2. `POST /api/update/run` (no body required).
3. Response: 200 with `{"ok": true, ...}` or 409 if already running.
4. Poll `GET /api/update/summary` -- `overall` transitions through `updating` -> `up_to_date` (or `has_failures`).
5. Fleet counters in summary: `fleet.updated`, `fleet.in_progress`, `fleet.pending_idle`, `fleet.failed`, `fleet.offline`, `fleet.outdated`.

### 8. Summary endpoint (GET /api/update/summary)

1. Before any update: `overall` = `up_to_date` and `message` = `PitBox is up to date`.
2. After triggering update: `overall` = `updating` and `message` describes progress.
3. Fields: `current_version`, `target_version`, `controller.status`, `fleet.*`, `release.*`, `agents[]`.
4. 409 test: call `POST /api/update/run` twice quickly; second call should return 409.
### 9. State normalization on startup

1. Create a stale `C:\PitBox\updates\status.json` with `{"state": "downloading", "message": "test"}` (no `updated_at` field).
2. Start controller.
3. Expected: `GET /api/update/controller/status` returns `state=error` with recovery message, not `downloading`.
4. Verify logs show `Clearing stale updater status.json`.

### 10. Reset Update State (POST /api/update/reset-state)

1. Trigger an update that gets stuck (or manually set orchestrator state).
2. `POST /api/update/reset-state`.
3. Expected: 200 with `{"ok": true, "message": "Update state cleared"}`.
4. `GET /api/update/summary` should show `overall=up_to_date` (assuming no pending updates).
5. UI: Advanced > Controller > Reset Update State button should clear stuck state.
