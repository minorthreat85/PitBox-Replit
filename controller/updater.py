"""
PitBox Controller updater: installer execution and install state management.

Release discovery/caching lives in controller/release_service.py (single authority).
This module handles: download+install threads, external updater spawn, dev-pull.
Controller never updates itself; POST /api/update/apply spawns pitbox_updater.exe (detached).
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from controller.config import get_config
from controller.release_service import (
    get_channel_config as get_update_channel_config,
    get_controller_update_status as get_update_status,
    clear_cache as clear_update_cache,
    fetch_latest_release as get_latest_release_info,
)

logger = logging.getLogger(__name__)

# Default paths for external updater (outside controller install)
DEFAULT_UPDATER_EXE = Path(r"C:\PitBox\updater\pitbox_updater.exe")
# Installer-based updater (PitBoxUpdater.exe) - primary path for "Download update & restart"
DEFAULT_PITBOX_UPDATER_EXE = Path(r"C:\PitBox\updater\PitBoxUpdater.exe")
DEFAULT_INSTALL_DIR = Path(r"C:\PitBox\Controller")
DEFAULT_WORK_DIR = Path(r"C:\PitBox\updates")

# ---------------------------------------------------------------------------
# In-process install state (for silent Inno installer download + run)
# ---------------------------------------------------------------------------
_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: dict[str, Any] = {"state": "idle", "message": "", "percent": 0}


def _set_install_state(state: str, message: str, percent: int = 0) -> None:
    with _INSTALL_LOCK:
        _INSTALL_STATE["state"] = state
        _INSTALL_STATE["message"] = message
        _INSTALL_STATE["percent"] = percent
    logger.info("Install state: %s — %s (%d%%)", state, message, percent)


def _run_installer_windows(installer_path: Path, cwd: Path) -> None:
    """
    Run the Inno Setup installer on Windows without any visible window.

    Strategy:
    1. Write a detached PowerShell launcher script to the temp dir.
       The script stops the PitBox service first (releasing file locks),
       then runs the installer with /VERYSILENT /SUPPRESSMSGBOXES.
    2. Fire the launcher as a fully DETACHED_PROCESS so it survives even if
       our controller process is killed by the service stop.
    3. Return immediately — the frontend will detect the controller going
       offline and auto-reload when the new version comes back up.

    This avoids:
    - Inno exit code 5 (CloseApplications fails on locked service files)
    - ShellExecuteEx runas blocking forever in session-0 / SYSTEM context
    - Any visible console or PowerShell window
    """
    installer_str = str(installer_path).replace("'", "''")  # escape for PS single-quote
    cwd_str = str(cwd).replace("'", "''")

    # PowerShell script that runs detached from our process tree
    launcher_ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
# Give the controller a moment to respond to the browser's next poll
Start-Sleep -Seconds 3
# Stop the service so Inno can replace locked files
Stop-Service -Name 'PitBoxController' -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
# Run the Inno installer silently
& '{installer_str}' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
"""
    launcher_path = cwd / "pitbox_launcher.ps1"
    launcher_path.write_text(launcher_ps, encoding="utf-8")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    subprocess.Popen(
        [
            "powershell.exe",
            "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass",
            "-NonInteractive",
            "-File", str(launcher_path),
        ],
        cwd=cwd_str,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    # We return here — the launcher will stop the service (killing us) and
    # run the installer.  The frontend will detect the offline/online
    # transition and reload automatically.
    _set_install_state("installing", "Installer launched — PitBox will restart in a few seconds…", 98)


def _download_and_install(asset_url: str, expected_sha256: str, installer_filename: str) -> None:
    """
    Background thread: download Inno installer EXE, verify SHA-256, run silently.
    Writes progress to _INSTALL_STATE so the /api/update/status polling reflects reality.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="pitbox_upd_"))
    installer_path = tmp_dir / installer_filename
    try:
        channel = get_update_channel_config()
        token = (channel.get("github_token") or "").strip() or None

        headers: dict[str, str] = {"Accept": "application/octet-stream", "User-Agent": "PitBox-Controller"}
        if token:
            headers["Authorization"] = f"token {token}"

        _set_install_state("downloading", "Downloading update… 0%", 0)
        req = Request(asset_url, headers=headers)
        with urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            sha = hashlib.sha256()
            with open(installer_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded * 90 / total)
                        _set_install_state("downloading", f"Downloading update… {pct}%", pct)

        _set_install_state("verifying", "Verifying download…", 92)
        actual = sha.hexdigest().lower()
        if actual != expected_sha256.lower():
            _set_install_state(
                "error",
                f"SHA-256 mismatch — download may be corrupt. Expected …{expected_sha256[-8:]}, got …{actual[-8:]}",
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        _set_install_state("installing", "Installing silently… PitBox will restart automatically.", 95)
        try:
            installer_path.chmod(0o755)
        except Exception:
            pass
        try:
            if os.name == "nt":
                _run_installer_windows(installer_path, tmp_dir)
            else:
                proc = subprocess.Popen(
                    [str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    cwd=str(tmp_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=300)
                if proc.returncode == 0:
                    _set_install_state("done", "Install complete — PitBox is restarting…", 100)
                else:
                    _set_install_state("error", f"Installer exited with code {proc.returncode}")
        except subprocess.TimeoutExpired:
            try:
                proc.kill()  # type: ignore[name-defined]
            except Exception:
                pass
            _set_install_state("error", "Installer timed out after 5 minutes")
        except Exception as exc:
            _set_install_state("error", f"Failed to run installer: {exc}")

    except Exception as exc:
        logger.exception("Download/install thread failed: %s", exc)
        _set_install_state("error", f"Update failed: {exc}")
    finally:
        # On Windows the detached PowerShell launcher still needs the installer
        # EXE and .ps1 from tmp_dir — leave them in place.  The OS purges temp
        # files on the next reboot.  On Linux/Mac clean up immediately.
        with _INSTALL_LOCK:
            cur_state = _INSTALL_STATE.get("state", "idle")
        if os.name == "nt" and cur_state in ("installing", "done"):
            logger.info("Leaving tmp dir for detached installer: %s", tmp_dir)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


_STALE_THRESHOLDS = {
    "starting": 120,
    "downloading": 1800,
    "verifying": 600,
    "installing": 900,
}


def is_updater_process_running() -> bool:
    """Check if an external updater process is actually running."""
    try:
        import psutil
        names = {"pitbox_updater.exe", "pitboxupdater.exe"}
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() in names:
                return True
    except Exception:
        pass
    return False


def _is_install_thread_active() -> bool:
    """Check if the in-memory background install thread is running."""
    with _INSTALL_LOCK:
        st = _INSTALL_STATE.get("state", "idle")
    return st not in ("idle", "done", "error")


def _normalize_disk_status(data: dict) -> dict:
    """Normalize a status.json entry — return corrected state dict."""
    state = data.get("state", "idle")
    if state in ("idle", "done", "error"):
        return {
            "state": state,
            "message": data.get("message", ""),
            "percent": data.get("percent", 0),
        }
    if is_updater_process_running():
        return {
            "state": state,
            "message": data.get("message", ""),
            "percent": data.get("percent", 0),
        }
    threshold = _STALE_THRESHOLDS.get(state, 600)
    updated_at = data.get("updated_at") or data.get("started_at")
    if updated_at:
        try:
            age = time.time() - float(updated_at)
            if age < threshold:
                return {
                    "state": state,
                    "message": data.get("message", ""),
                    "percent": data.get("percent", 0),
                }
        except (ValueError, TypeError):
            pass
    logger.warning("Stale updater state '%s' with no active process — normalizing to error", state)
    return {
        "state": "error",
        "message": "Recovered from stale update state left over from a previous session.",
        "percent": 0,
    }


def normalize_updater_state_on_startup(work_dir: Path | None = None) -> None:
    """Called once at controller startup to clean up stale updater state."""
    with _INSTALL_LOCK:
        _INSTALL_STATE["state"] = "idle"
        _INSTALL_STATE["message"] = ""
        _INSTALL_STATE["percent"] = 0

    wd = work_dir or DEFAULT_WORK_DIR
    path = wd / "status.json"
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        state = data.get("state", "idle")
        if state in ("idle", "done", "error"):
            return
        if is_updater_process_running():
            logger.info("Updater process is running — keeping status.json state '%s'", state)
            return
        logger.warning("Clearing stale updater status.json (state='%s') — no updater process running", state)
        path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read/clean status.json on startup: %s", e)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def get_updater_status(work_dir: Path | None = None) -> dict[str, Any]:
    """
    Return current update state.
    Checks in-memory _INSTALL_STATE first (set by the background download/install thread),
    then falls back to status.json written by the external pitbox_updater.exe.
    Normalizes stale transitional states.
    """
    with _INSTALL_LOCK:
        mem = _INSTALL_STATE.copy()
    if mem.get("state") and mem["state"] != "idle":
        return mem

    wd = work_dir or DEFAULT_WORK_DIR
    path = wd / "status.json"
    if not path.exists():
        return {"state": "idle"}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_disk_status(data)
    except (OSError, json.JSONDecodeError):
        return {"state": "idle"}


def apply_controller_update() -> tuple[bool, str]:
    """
    Spawn external updater (pitbox_updater.exe) with ZIP URL. Return immediately.
    Controller never updates itself; updater stops service, replaces install, starts service.
    """
    status = get_update_status()
    if status.get("error"):
        return False, status.get("error", "Update check failed")
    ctrl_zip = status.get("controller_zip")
    if not ctrl_zip:
        return False, "No controller update ZIP available for this release"
    zip_url = ctrl_zip.get("api_url") or ctrl_zip.get("url")
    if not zip_url:
        return False, "No controller ZIP URL available"

    expected_sha = (ctrl_zip.get("sha256") or "").strip()
    if not expected_sha:
        return False, (
            "Cannot apply ZIP update: release has no SHA-256 for this asset. "
            "Publish a GitHub release note line: "
            "<!-- pitbox_sha256:EXACT_ZIP_FILENAME.zip:64_hex_digits --> "
            "(filename must match the ZIP asset name exactly)."
        )

    channel = get_update_channel_config()
    updater_exe = Path(os.environ.get("PITBOX_UPDATER_EXE", str(DEFAULT_UPDATER_EXE)))
    install_dir = Path(os.environ.get("PITBOX_CONTROLLER_INSTALL", str(DEFAULT_INSTALL_DIR)))
    work_dir = Path(os.environ.get("PITBOX_UPDATES_DIR", str(DEFAULT_WORK_DIR)))
    token = (channel.get("github_token") or "").strip() or None

    if not updater_exe.exists():
        return False, f"Updater not found: {updater_exe}. Deploy pitbox_updater.exe to C:\\PitBox\\updater\\."

    args = [
        str(updater_exe),
        "--service", "PitBoxController",
        "--zip-url", zip_url,
        "--install-dir", str(install_dir),
        "--work-dir", str(work_dir),
    ]
    if token:
        args.extend(["--token", token])
    args.extend(["--expected-sha256", expected_sha])

    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP so controller does not wait
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            cwd=str(work_dir),
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.exception("Failed to spawn updater: %s", e)
        return False, f"Failed to start updater: {e}"
    return True, "Updater started"


# Primary path used by the Inno installer and Start Menu shortcut
DEFAULT_UPDATE_SCRIPT = Path(r"C:\PitBox\tools\update_pitbox.ps1")


def _resolve_update_script_path() -> Optional[Path]:
    """
    Resolve update_pitbox.ps1: try installed path first, then exe-relative, then dev repo.
    Log every candidate checked and which one was selected.
    """
    # 1) Installed live path (Inno installs controller to C:\PitBox and puts script in tools\)
    primary = Path(r"C:\PitBox\tools\update_pitbox.ps1")
    # 2) Next to the running executable (e.g. C:\PitBox\PitBoxController.exe -> C:\PitBox\tools\)
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "executable", None) else None
    exe_tools = (exe_dir / "tools" / "update_pitbox.ps1") if exe_dir else None
    # 3) Dev repo (when running as python -m controller, __file__ is in controller/)
    try:
        _file = __file__
    except NameError:
        _file = None
    dev_tools = (Path(_file).resolve().parent.parent / "tools" / "update_pitbox.ps1") if _file else None

    candidates = [c for c in [primary, exe_tools, dev_tools] if c is not None]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        r = c.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(c)

    for p in unique:
        exists = p.exists()
        logger.info("Update script candidate: %s exists=%s", p, exists)
        if exists:
            logger.info("Using update script: %s", p)
            return p

    logger.warning("No update script found. Checked: %s", [str(p) for p in unique])
    return None


def _launch_pitbox_updater_installer(
    asset_url: str,
    version: str,
    *,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    """
    Launch PitBoxUpdater.exe (installer-based) in the logged-in user session via scheduled task.
    So the updater window and Inno installer are visible. Controller can then exit safely.
    """
    import base64
    updater_exe = Path(os.environ.get("PITBOX_UPDATER_INSTALLER_EXE", str(DEFAULT_PITBOX_UPDATER_EXE)))
    if not updater_exe.exists():
        return False, f"PitBoxUpdater not found: {updater_exe}. Install PitBox to get C:\\PitBox\\updater\\PitBoxUpdater.exe."

    try:
        import psutil
        logged_in_user = None
        for u in psutil.users():
            if u.name and u.name.lower() != "system":
                logged_in_user = u.name
                break
        if not logged_in_user:
            return False, "Could not determine logged-in user to show updater window."

        # Encode argument string so URL and special chars are passed safely to the task
        arg_string = f'--target controller --asset-url "{asset_url}" --version "{version or "unknown"}"'
        exp = (expected_sha256 or "").strip()
        if exp:
            arg_string += f' --expected-sha256 "{exp}"'
        arg_b64 = base64.b64encode(arg_string.encode("utf-8")).decode("ascii")
        exe_path = str(updater_exe).replace("'", "''")  # escape for PowerShell single-quoted string
        task_name = "PitBox Interactive Updater"
        ps_script = f'''
$ErrorActionPreference = 'Stop'
$arg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{arg_b64}'))
Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue
$Action = New-ScheduledTaskAction -Execute '{exe_path}' -Argument $arg -WorkingDirectory "C:\\PitBox"
$Principal = New-ScheduledTaskPrincipal -UserId "{logged_in_user}" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $Action -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName '{task_name}'
'''
        create_res = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
        )
        if create_res.returncode != 0:
            logger.error("Failed to start PitBoxUpdater task. stdout: %s stderr: %s", create_res.stdout, create_res.stderr)
            return False, create_res.stderr.strip() or create_res.stdout.strip() or "Failed to start updater task."
        logger.info("Started PitBoxUpdater via scheduled task (user=%s)", logged_in_user)
        return True, "PitBox Updater started. A window will open to download and install the update."
    except Exception as e:
        logger.exception("Failed to launch PitBoxUpdater: %s", e)
        return False, str(e)


def run_unified_installer_update() -> tuple[bool, str]:
    """
    Download the Inno Setup installer directly, verify SHA-256, and run it with /VERYSILENT.
    No PowerShell window, no scheduled tasks — completely silent like an auto-update.
    Runs the download+install in a background daemon thread and returns immediately.
    Progress is tracked in _INSTALL_STATE and exposed via /api/update/status.
    """
    with _INSTALL_LOCK:
        current_state = _INSTALL_STATE.get("state", "idle")
    if current_state not in ("idle", "error", "done"):
        return False, "An update is already in progress."

    status = get_update_status()
    if status.get("error"):
        return False, status.get("error", "Update check failed")
    if not status.get("update_available"):
        return False, "No update available"

    unified = status.get("unified_installer")
    if not unified:
        return False, "No unified installer (PitBoxInstaller*.exe) found in this release."

    # Prefer browser_download_url (no auth needed for public repos); fall back to API URL
    asset_url = unified.get("url") or unified.get("api_url")
    if not asset_url:
        return False, "Unified installer download URL not available."

    installer_sha = (unified.get("sha256") or "").strip()
    if not installer_sha:
        return False, (
            "Release is missing a SHA-256 annotation for the installer. "
            "Add this to the GitHub release notes: "
            "<!-- pitbox_sha256:PitBoxInstaller-x.x.x.exe:64_hex_digits -->"
        )

    installer_filename = unified.get("name") or "PitBoxInstaller.exe"

    _set_install_state("starting", "Starting download…", 0)
    t = threading.Thread(
        target=_download_and_install,
        args=(asset_url, installer_sha, installer_filename),
        daemon=True,
        name="pitbox-installer",
    )
    t.start()
    return True, "Download started — progress visible in the Updates panel."


def apply_dev_pull_update(repo_path: str) -> tuple[bool, str]:
    """
    Dev-mode in-app update: spawns a detached PowerShell script that stops the
    PitBoxController service, runs git pull + build_release.ps1 -Dev, copies the
    built exe to the install dir, and restarts the service.

    Equivalent to running update.ps1 manually, but triggered from the PitBox UI.
    Only meaningful on the dev machine where the source repo lives.
    """
    if os.name != "nt":
        return False, "Dev pull update is only supported on Windows."

    rp = Path(repo_path)
    if not rp.exists():
        return False, f"Dev repo path not found: {repo_path}"

    update_ps1 = rp / "update.ps1"
    build_ps1 = rp / "scripts" / "build_release.ps1"

    if not update_ps1.exists() and not build_ps1.exists():
        return False, (
            f"Neither update.ps1 nor scripts/build_release.ps1 found in {repo_path}. "
            "Check your Dev Repo Path in Settings."
        )

    repo_str = str(rp).replace("'", "''")

    # If update.ps1 exists, run it directly (it handles stop → pull → build → copy → start)
    if update_ps1.exists():
        update_ps1_str = str(update_ps1).replace("'", "''")
        launcher_ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 2
& powershell.exe -ExecutionPolicy Bypass -NonInteractive -File '{update_ps1_str}'
"""
    else:
        # Fallback: inline the logic
        launcher_ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 2
Stop-Service -Name 'PitBoxController' -Force
Start-Sleep -Seconds 3
Set-Location '{repo_str}'
git pull
& '{repo_str}\\scripts\\build_release.ps1' -Dev
$src = '{repo_str}\\dist\\PitBoxController.exe'
$dst = 'C:\\PitBox\\installed\\bin\\PitBoxController.exe'
if (Test-Path $src) {{ Copy-Item $src $dst -Force }}
Start-Service -Name 'PitBoxController'
"""

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="pitbox_devupd_"))
        launcher_path = tmp_dir / "dev_pull.ps1"
        launcher_path.write_text(launcher_ps, encoding="utf-8")

        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200

        subprocess.Popen(
            [
                "powershell.exe",
                "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass",
                "-NonInteractive",
                "-File", str(launcher_path),
            ],
            cwd=str(rp),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        _set_install_state("installing", "Pulling & rebuilding — PitBox will restart in ~30 seconds…", 10)
        return True, "Dev pull started — PitBox will restart automatically."
    except Exception as exc:
        return False, f"Failed to launch dev pull: {exc}"
