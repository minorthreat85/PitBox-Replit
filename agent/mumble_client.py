"""
Mumble client launcher for PitBox Agent.
Launches the Mumble desktop client on Windows sim PCs.
Auto-installs Mumble 1.3.4 silently if not found.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_mumble_proc: Optional[subprocess.Popen] = None

_MUMBLE_MSI_URL = (
    "https://github.com/mumble-voip/mumble/releases/download/1.3.4/"
    "mumble-1.3.4.msi"
)

# Bundled MSI locations (PitBoxInstaller bundles this at install time)
_BUNDLED_MSI_PATHS = [
    r"C:\PitBox\tools\mumble-1.3.4.msi",
    r"C:\PitBox\bin\mumble-1.3.4.msi",
]

# Checked in order — first one that exists is used
_MUMBLE_PATHS = [
    r"C:\Program Files\Mumble\mumble.exe",
    r"C:\Program Files (x86)\Mumble\mumble.exe",
    r"C:\Program Files\Mumble\mumble-1.4\mumble.exe",
    r"C:\Program Files (x86)\Mumble\mumble-1.4\mumble.exe",
]


def _find_mumble(custom_path: Optional[str] = None) -> Optional[str]:
    """Return path to mumble.exe or None if not found."""
    if custom_path:
        p = Path(custom_path)
        if p.exists():
            return str(p)
    for path in _MUMBLE_PATHS:
        if Path(path).exists():
            return path
    return None


def _install_mumble() -> dict:
    """
    Silently install Mumble 1.3.4 MSI.
    Prefers bundled MSI (installed by PitBoxInstaller).
    Falls back to internet download only if no bundled MSI is found.
    Returns dict with success and message.
    """
    if sys.platform != "win32":
        return {"success": False, "message": "Auto-install only supported on Windows"}

    # Prefer bundled MSI (no internet required)
    msi_path: Optional[Path] = None
    for bundled in _BUNDLED_MSI_PATHS:
        p = Path(bundled)
        if p.exists() and p.stat().st_size > 0:
            msi_path = p
            logger.info("Using bundled Mumble MSI: %s", msi_path)
            break

    downloaded = False
    if msi_path is None:
        # Fallback: download from internet
        msi_path = Path(tempfile.gettempdir()) / "mumble-1.3.4.msi"
        try:
            logger.info("Bundled MSI not found — downloading Mumble 1.3.4 MSI from GitHub...")
            urllib.request.urlretrieve(_MUMBLE_MSI_URL, str(msi_path))
            logger.info("Download complete: %s", msi_path)
            downloaded = True
        except Exception as e:
            msg = f"Failed to download Mumble installer: {e}"
            logger.error(msg)
            return {"success": False, "message": msg}

    try:
        logger.info("Installing Mumble silently from: %s", msi_path)
        result = subprocess.run(
            ["msiexec", "/i", str(msi_path), "/qn", "/norestart"],
            timeout=120,
            capture_output=True,
            text=True,
        )
        if result.returncode in (0, 3010):
            logger.info("Mumble 1.3.4 installed successfully (exit code %s)", result.returncode)
            return {"success": True, "message": "Mumble 1.3.4 installed successfully"}
        else:
            msg = f"Mumble installer exited with code {result.returncode}"
            logger.error("%s — stderr: %s", msg, result.stderr)
            return {"success": False, "message": msg}
    except subprocess.TimeoutExpired:
        msg = "Mumble installer timed out after 120s"
        logger.error(msg)
        return {"success": False, "message": msg}
    except Exception as e:
        msg = f"Failed to run Mumble installer: {e}"
        logger.error(msg)
        return {"success": False, "message": msg}
    finally:
        if downloaded:
            try:
                msi_path.unlink(missing_ok=True)
            except Exception:
                pass


def _kill_mumble() -> bool:
    """
    Kill any running mumble.exe process.
    Returns True if a process was found and killed, False if nothing was running.
    Clears the internal _mumble_proc handle in all cases.
    """
    global _mumble_proc
    killed = False

    # Kill by tracked PID first (most precise)
    if _mumble_proc is not None:
        pid = _mumble_proc.pid
        _mumble_proc = None
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
            else:
                import os, signal
                os.kill(pid, signal.SIGTERM)
            killed = True
            logger.info("Killed tracked Mumble PID %s", pid)
        except Exception as e:
            logger.warning("Could not kill tracked PID %s: %s", pid, e)

    # Always run taskkill /IM as a safety net (catches externally-started mumble.exe)
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "mumble.exe"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                killed = True
                logger.info("Killed mumble.exe via taskkill /IM")
            else:
                # Exit 128 = process not found — expected when nothing is running
                logger.debug("taskkill /IM mumble.exe exit=%s (not running)", r.returncode)
        except Exception as e:
            logger.warning("taskkill /IM mumble.exe failed: %s", e)

    return killed


def launch_mumble(mumble_exe: Optional[str] = None, server_url: Optional[str] = None) -> dict:
    """
    Launch the Mumble desktop client and auto-connect to the server/channel via URL.

    Behaviour:
    - Always kills any existing mumble.exe first (even if externally started)
    - Waits 1.5 s for the process to fully exit
    - Resolves mumble.exe from standard install paths
    - Launches:  mumble.exe <server_url>
    - server_url format: mumble://<username>@<host>:<port>/<channel>

    This guarantees that every launch connects to the server and joins the
    correct channel with no manual interaction, regardless of previous state.
    """
    global _mumble_proc

    logger.info("launch_mumble called — url=%s", server_url or "(none)")

    # Step 1: Kill any existing Mumble so the new launch always uses the URL
    was_running = _kill_mumble()
    if was_running:
        logger.info("Killed existing Mumble — waiting 1.5 s before relaunch...")
        time.sleep(1.5)
    else:
        logger.info("No existing Mumble process found")

    # Step 2: Resolve mumble.exe path
    exe = _find_mumble(mumble_exe)
    if not exe:
        logger.info("Mumble not found at standard paths — attempting auto-install...")
        install_result = _install_mumble()
        if not install_result["success"]:
            return install_result
        exe = _find_mumble(mumble_exe)
        if not exe:
            msg = (
                "Mumble installed but executable still not found. "
                "Checked: " + ", ".join(_MUMBLE_PATHS)
            )
            logger.error(msg)
            return {"success": False, "message": msg}

    logger.info("Resolved mumble.exe: %s", exe)

    # Step 3: Build the command and launch
    # mumble.exe  mumble://<username>@<host>:<port>/<channel>
    cmd = [exe]
    if server_url:
        cmd.append(server_url)

    logger.info("Launching: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, close_fds=True)
        _mumble_proc = proc
        logger.info("Mumble launched (PID %s) — connecting to %s", proc.pid, server_url or "(no url)")
        return {
            "success": True,
            "message": f"Mumble launched (PID {proc.pid})",
            "exe": exe,
            "url": server_url or "",
        }
    except Exception as e:
        msg = f"Failed to launch Mumble: {e}"
        logger.error(msg)
        return {"success": False, "message": msg}


def close_mumble() -> dict:
    """
    Kill the Mumble process.
    Idempotent: if Mumble is not running, returns success without error.
    """
    logger.info("close_mumble called")
    killed = _kill_mumble()
    if killed:
        return {"success": True, "message": "Mumble closed"}
    return {"success": True, "message": "Mumble was not running", "already_closed": True}
