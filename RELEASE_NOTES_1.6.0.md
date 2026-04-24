# PitBox v1.6.0 — Mobile UI, Sim Card Removal, Telemetry Hardening

**Status:** Source on GitHub `main` is ready (`afda7e7`). **Build must run on Windows dev box** (`C:\Users\info\pitbox\`) — PyInstaller cannot cross-compile from Linux to Windows.

---

## What's new

### Mobile UI
- **Responsive resizing across the controller UI** — the admin pages now adapt cleanly to phone and tablet viewports, not just the desktop browser on the admin PC.
- **Mobile navigation fix** — the toast container was capturing pointer events on small screens, blocking the nav bar. Toasts no longer steal taps from the underlying nav.
- **Robust mobile routing fallback** — defends against routing edge cases that previously left mobile users stuck on a blank page after deep-linking.

### Sim card management
- **Removal gated on enrollment mode** — the "Remove" button on enrolled sim cards is hidden whenever enrollment mode is off, and the backend `DELETE /api/agents/{id}` returns `403` if called outside enrollment mode. Prevents accidental rig removal during a session.

### Telemetry / Agent
- **AC crash fix on session load** — agent no longer creates Assetto Corsa shared-memory mappings; it only opens existing ones. AC's CSP `writeStatic` was crashing whenever the agent had pre-created the SM blocks.
- **Track map reliability** — improved car position tracking accuracy and map loading.
- **Canonical map key generation** — track map display now uses a deterministic map key derivation, fixing intermittent "wrong map" displays during Practice → Quali → Race transitions.
- **SM reader hardening** — `_open_existing_mapping` now safely handles the `MapViewOfFile` failure path on non-Windows test hosts (`getattr(ctypes, "get_last_error", ...)`), so the unit-test harness can exercise it with monkey-patched bindings.
- **Kernel handle leak fix** — guarantees `CloseHandle` is called exactly once when `MapViewOfFile` returns `NULL`, so we never leak a kernel handle per retry while AC is in a transient state.
- **Idempotent shared-view close** — double-closing a `_SharedView` is now a no-op; `Unmap`/`Close` each fire exactly once.
- **Two new tests** — `test_open_closes_handle_when_mapview_fails`, `test_shared_view_close_is_idempotent`. All 41 sm_reader tests pass.

---

## Build commands (on Windows dev box)

From `C:\Users\info\pitbox\` in an Admin PowerShell:

```powershell
# 1. Pull latest
git pull origin main

# 2. Verify Python 3.11 (NEVER 3.14)
& "C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe" --version

# 3. Build via the release script
.\scripts\build_release.ps1
```

This produces the `PitBoxControllerSetup_1.6.0.exe` and `PitBoxAgentSetup_1.6.0.exe` installers.

---

## Publishing the GitHub Release

After the build completes:

1. Tag the release: `git tag v1.6.0 && git push origin v1.6.0`
2. Create the GitHub Release at `https://github.com/minorthreat85/PitBox-Replit/releases/new`
   - Tag: `v1.6.0`
   - Title: `PitBox v1.6.0 — Mobile UI, Sim Card Removal, Telemetry Hardening`
   - Body: contents of this file
3. Attach the two `.exe` installers as release assets. The `asset_controller_regex` in your update channel config (`/PitBoxControllerSetup[^/]*\.exe$/i`) will match `PitBoxControllerSetup_1.6.0.exe`.

Once the Release is published, your LAN sim PCs running `update.ps1` (or the Controller's Settings → Updates panel) will detect v1.6.0 and offer to install.
