# PitBoxUpdater – Standalone installer-based updater

## Overview

**PitBoxUpdater.exe** is a small standalone executable used for both **Controller** and **Agent** updates. It replaces PowerShell scripts as the primary update runner: the running app or service hands off to the updater, which handles download, shutdown, install, and restart in a visible window.

## Where it lives

| Item | Location |
|------|----------|
| **Source** | `dev/pitbox/updater/pitbox_updater_installer.py` |
| **Build spec** | `dev/pitbox/PitBoxUpdater.spec` |
| **Built output** | `dev/pitbox/dist/PitBoxUpdater.exe` |
| **Installed** | `C:\PitBox\updater\PitBoxUpdater.exe` (via Inno Setup) |
| **Logs** | `C:\PitBox\logs\PitBoxUpdater.log` |
| **Downloads** | `C:\PitBox\downloads\PitBoxInstaller-<version>.exe` |

## Command-line interface

Required:

- `--target controller | agent`
- One of:
  - `--asset-url <url>` – direct URL to `PitBoxInstaller_*.exe`
  - `--release-url <url>` – GitHub release API URL (e.g. `.../releases/latest`)
  - `--repo` – use `--repo-owner` and `--repo-name` to fetch latest release

Optional:

- `--version <ver>` – version string (for filename and logging)
- `--repo-owner` (default: `minorthreat85`)
- `--repo-name` (default: `pitbox-releases`)
- `--token` – GitHub token for private repos

Required for security (controller/agent always pass this when launching):

- `--expected-sha256 <64-hex>` – SHA-256 of the installer EXE from **trusted release metadata** (see below). The updater **refuses to run the installer** if the hash is missing or does not match the downloaded file.

Examples:

```text
PitBoxUpdater.exe --target controller --asset-url "https://..." --version 1.4.3 --expected-sha256 "<64 hex chars>"
PitBoxUpdater.exe --target agent --asset-url "https://..." --version 1.4.3 --expected-sha256 "<64 hex chars>"
PitBoxUpdater.exe --target controller --repo --repo-owner minorthreat85 --repo-name pitbox-releases --expected-sha256 "<64 hex chars>"
```

### Release SHA-256 metadata (GitHub)

Publish one HTML comment per asset in the **release notes** (body). The **filename** must match the GitHub asset `name` exactly (e.g. `PitBoxController-1.5.0.zip`, `PitBoxInstaller-1.5.0.exe`):

```html
<!-- pitbox_sha256:PitBoxController-1.5.0.zip:abcdef0123456789...64hex... -->
<!-- pitbox_sha256:PitBoxInstaller-1.5.0.exe:0123456789abcdef...64hex... -->
```

The controller parses these, attaches `sha256` to release assets, and passes `--expected-sha256` to `pitbox_updater.exe` (ZIP path) and `PitBoxUpdater.exe` (installer path). **Without** these comments, **Apply ZIP update** and **Download update & restart** will fail with an operator-visible error until the release is updated.

ZIP-based `pitbox_updater.exe` also **requires** `--expected-sha256` on the command line (no install if omitted).

## Behaviour

1. Shows a visible window (title: **PitBox Updater**).
2. If not elevated, re-launches with UAC (runas).
3. Downloads the installer to `C:\PitBox\downloads\PitBoxInstaller-<version>.exe` and verifies **SHA-256** (`--expected-sha256`); on mismatch, stops with an error (installer is **not** executed).
4. Stops the target:
   - **Controller**: `PitBoxController` service (NSSM or `net stop`).
   - **Agent**: scheduled task **PitBox Agent** or `taskkill PitBoxAgent.exe`.
5. Runs the installer visibly and waits for it to finish.
6. Restarts the target (service or task / process).
7. Logs all steps and errors to `C:\PitBox\logs\PitBoxUpdater.log`.

Service/task names are defined in one place at the top of `pitbox_updater_installer.py`: `CONTROLLER_SERVICE_NAME`, `AGENT_TASK_NAME`.

## How Controller uses it

- In **Settings → Updates**, when the user clicks **Download update & restart** and the release has a **unified installer** (`PitBoxInstaller_*.exe`), the controller:
  1. Resolves `PitBoxUpdater.exe` at `C:\PitBox\updater\PitBoxUpdater.exe` (or `PITBOX_UPDATER_INSTALLER_EXE`).
  2. Creates a one-off scheduled task **PitBox Interactive Updater** that runs `PitBoxUpdater.exe --target controller --asset-url "<url>" --version "<ver>" --expected-sha256 "<hex>"` as the **logged-in user** (interactive), so the updater and Inno UI are visible.
  3. Starts the task and returns success; the controller does not run the update itself.

- If `PitBoxUpdater.exe` is missing, the controller falls back to `update_pitbox.ps1` (PowerShell).

## How Agent uses it

- On startup, the agent runs an update check in the background. If an update is available, an installer URL is found, **and** the release notes include a matching `pitbox_sha256` comment for that installer asset:
  1. A MessageBox is shown: “Click OK to start the updater now.”
  2. When the user clicks OK, the agent runs `PitBoxUpdater.exe` with `--asset-url`, `--version`, and `--expected-sha256` via `subprocess.Popen`.
  3. The updater window appears and performs download, verify, stop, install, restart.

If the release has no SHA-256 metadata for the installer, the agent shows the manual “download from releases” message instead of one-click update.

## Packaging and build

- **Build**: from `dev/pitbox`, run the normal release build script. It builds `PitBoxUpdater.exe` via `PitBoxUpdater.spec` and places it in `dist/PitBoxUpdater.exe`.
- **Installer**: `installer/pitbox.iss` includes `PitBoxUpdater.exe` and installs it to `{app}\updater` (i.e. `C:\PitBox\updater\`). The `[Dirs]` section creates `{app}\updater` and `{app}\downloads`.
- **Deploy**: After building, `dist/` contains `PitBoxUpdater.exe`; the Inno installer copies it to `C:\PitBox\updater\` so it is present for both controller and agent update flows.

---

## Manual test procedure

### Controller update (Download update & restart)

1. Install PitBox with **Controller** (or both) so that `C:\PitBox\updater\PitBoxUpdater.exe` exists.
2. Ensure a release with a **unified installer** (`PitBoxInstaller_*.exe`) is available (e.g. on GitHub).
3. Open the controller Web UI → **Settings → Updates**.
4. If an update is available, click **Download update & restart**.
5. **Expected**: A UAC prompt may appear; then a **PitBox Updater** window opens, shows “Updating: Controller”, downloads the installer, stops the controller service, runs the Inno installer, then restarts the controller. Logs in `C:\PitBox\logs\PitBoxUpdater.log`.

### Agent update (MessageBox → OK)

1. Install PitBox with **Agent** so that `C:\PitBox\updater\PitBoxUpdater.exe` exists.
2. Run the agent (e.g. from the **PitBox Agent** scheduled task or manually).
3. Ensure a newer release with `PitBoxInstaller_*.exe` exists so the startup update check sees an update.
4. **Expected**: A MessageBox “PitBox Agent — Update available” with “Click OK to start the updater now.”
5. Click **OK**.
6. **Expected**: PitBox Updater window opens, shows “Updating: Agent”, downloads the installer, stops the agent (task/process), runs the installer, restarts the agent. Check `C:\PitBox\logs\PitBoxUpdater.log`.

### Standalone CLI (optional)

1. Open a command prompt (or PowerShell) as Administrator.
2. Run:
   ```cmd
   "C:\PitBox\updater\PitBoxUpdater.exe" --target controller --asset-url "https://github.com/.../releases/download/.../PitBoxInstaller_1.4.3.exe" --version 1.4.3
   ```
3. **Expected**: Updater window appears and runs the full flow; installer runs visibly.

### Fallback (Controller only)

1. Temporarily rename or remove `C:\PitBox\updater\PitBoxUpdater.exe`.
2. In the controller Web UI, click **Download update & restart** (with a release that has a unified installer).
3. **Expected**: Controller falls back to `update_pitbox.ps1` (PowerShell); behaviour as before (script runs via scheduled task).
