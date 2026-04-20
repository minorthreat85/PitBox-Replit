# PitBox v1.5.12 — Release & Build Playbook

**Status:** Source freeze ready on Replit. **Build must run on Windows dev box** (`C:\Users\info\pitbox\`) — PyInstaller cannot cross-compile from Linux to Windows.

---

## What changed since v1.5.11

This release ships the full 12-phase live-timing remediation in one EXE bump.

| Phase | Change |
|---|---|
| 2 | Canonical event schema `{seq, ts, type, car_id, driver, track, lap_ms, payload}` (no aliases) |
| 3 | Configurable UDP listener + persistent config |
| 4 | Cold-start + stale-feed resync supervisor |
| 5 | Backend-authoritative gap/interval (never negative) |
| 6 | Monotonic snapshot ordering (`snapshot_seq` + `generated_unix`) |
| 7 | Timing-feed health badge (live/stale/offline thresholds) |
| 8 | No ghost driver-detail state (cleared when selected car leaves) |
| 9 | WS primary, HTTP polling fallback only; shared `consumeEvents` cursor |
| 10 | `/ws/timing` auth parity with HTTP timing endpoints |
| 11 | Cleanup, naming, `docs/TIMING_API.md` canonical schema |
| 12 | 29 automated tests + `docs/TIMING_VALIDATION.md` |

**Plus packaging fix this release:**

- `PitBoxAgent.spec` switched from a hand-maintained `hiddenimports` list to
  `collect_submodules('agent')` + `collect_submodules('pitbox_common')`.
  This closes a silent-failure class where lazily-imported agent modules
  (`pairing`, `enrollment_client`, `sim_display`, `update_state`,
  `update_check`, `hotkey`, `race_out`, `server_cfg_sync`,
  `common.event_log`, `service.event_emitter`) were missing from the EXE
  and silently no-op'd at runtime. Critical for telemetry: the startup
  path goes `pairing → controller_heartbeat → start_telemetry`, so a
  missing `agent.pairing` would have left telemetry permanently disabled
  with only a warning in the log.

`PitBoxController.spec` already uses `collect_submodules('controller')`
and needs no change.

---

## Build commands (on Windows dev box)

From `C:\Users\info\pitbox\` in an Admin PowerShell:

```powershell
# 1. Pull
git pull origin main

# 2. Verify Python 3.11 (NEVER 3.14)
& "C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe" --version

# 3. Build via existing release script (Dev mode = no signing)
.\scripts\Build-PitBox.ps1
```

Or build EXEs individually:

```powershell
& "C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe" -m PyInstaller PitBoxController.spec --clean --noconfirm
& "C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe" -m PyInstaller PitBoxAgent.spec --clean --noconfirm
```

Outputs:
- `dist\PitBoxController.exe`
- `dist\PitBoxAgent.exe`

---

## Build-time sanity checks

`PitBoxAgent.exe --help` does NOT prove the lazy-import fix because argparse
short-circuits before the `pairing/enrollment_client/sim_display/...`
imports inside `try/except` run. To actually exercise the lazy paths, do
a real one-shot run against a test config and check the agent log:

```powershell
# Run the new agent EXE for ~5s with a paired test config; then kill it.
$env:PITBOX_TEST = "1"
Start-Process -FilePath .\dist\PitBoxAgent.exe -ArgumentList "--config","C:\PitBox\Agent\config\agent_config.json","--debug" -PassThru | Tee-Object -Variable p
Start-Sleep -Seconds 5
Stop-Process -Id $p.Id -Force

# The startup log MUST contain a 'STARTUP[telemetry]' line and MUST NOT
# contain 'IMPORT FAILED' or any 'ModuleNotFoundError' for agent.* modules.
Select-String -Path C:\PitBox\Agent\logs\agent.log -Pattern "STARTUP\[telemetry\]|IMPORT FAILED|ModuleNotFoundError" | Select-Object -Last 20
```

If you see `STARTUP[telemetry] decision(...)` and no `IMPORT FAILED`, the
spec change worked.

---

## Deploy

**Controller (admin PC):**

```powershell
C:\PitBox\installed\tools\nssm.exe stop PitBoxController
Copy-Item .\dist\PitBoxController.exe C:\PitBox\installed\PitBoxController.exe -Force
C:\PitBox\installed\tools\nssm.exe start PitBoxController
```

**Agent (push to all sims via existing script):**

```powershell
# -Dev flag is required by the script as a safety confirmation.
.\scripts\push_agent_to_sims.ps1 -Dev
```

This copies `dist\PitBoxAgent.exe` to each `\\Sim1..Sim8\C$\PitBox\PitBoxAgent.exe`.

---

## Post-deploy verification

Run on the admin PC against the live controller:

```powershell
# 1. Controller alive + version reported (expect 1.5.12)
curl http://localhost:9630/api/version

# 2. Timing routes present and shape-correct
curl http://localhost:9630/api/timing/health
curl http://localhost:9630/api/timing/snapshot | ConvertFrom-Json | Select-Object snapshot_seq,generated_unix,@{n='health';e={$_.health.timing.state}}

# 3. WS auth parity (open LAN mode — no operator password set)
# Expect 101 (switch protocols) success
# (Use a small wscat or browser DevTools console rather than curl)

# 4. Per-sim agent registry (paired rigs + last-seen)
curl http://localhost:9630/api/agents/registry

# 5. Fleet status (includes per-rig connection + telemetry presence)
curl http://localhost:9630/api/status
```

Expected snapshot top-level keys (per `docs/TIMING_API.md`):
`snapshot_seq, generated_unix, health, session, drivers, telemetry_agents, stats`.

---

## Manual smoke checklist (post-deploy)

| # | Scenario | Pass criteria |
|---|---|---|
| 1 | Open `/live-timing` in browser | Header badge shows `Live` (or `Offline` if AC not started) |
| 2 | Start AC server with 1 car | Driver row appears within 2s; status pill `Live` |
| 3 | Drive 1 lap | `lap_completed` event appears in events panel; gap/interval populated for non-leader |
| 4 | Disconnect a driver | Pill flips to `Disconnected`; if selected, detail panel clears (no ghost) |
| 5 | Stop AC server (timing feed dies) | Header badge → `Stale` after 5s, `Offline` after 30s |
| 6 | Restart controller mid-session | Snapshot resyncs; `snapshot_seq` resets but `generated_unix` advances; UI does not regress |
| 7 | Open WS without operator cookie (when password set) | Browser shows 403 on WS handshake; HTTP `/api/timing/snapshot` also 403 |

---

## Rollback

If anything breaks:

```powershell
C:\PitBox\installed\tools\nssm.exe stop PitBoxController
Copy-Item C:\PitBox\installed\PitBoxController.exe.bak C:\PitBox\installed\PitBoxController.exe -Force
C:\PitBox\installed\tools\nssm.exe start PitBoxController
```

Always keep the previous EXE as `.bak` before overwriting.
