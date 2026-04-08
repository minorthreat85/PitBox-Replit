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

# Checked in order — first path that exists is used
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
            logger.info("Using custom mumble path: %s", p)
            return str(p)
    for path in _MUMBLE_PATHS:
        if Path(path).exists():
            logger.info("Resolved mumble.exe: %s", path)
            return path
    logger.warning("mumble.exe not found in any standard path: %s", _MUMBLE_PATHS)
    return None


def _is_mumble_running() -> bool:
    """Return True if mumble.exe is currently running (Windows tasklist check)."""
    if sys.platform != "win32":
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq mumble.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "mumble.exe" in r.stdout.lower()
    except Exception:
        return False


def _install_mumble() -> dict:
    """
    Silently install Mumble 1.3.4 MSI.
    Prefers bundled MSI (installed by PitBoxInstaller).
    Falls back to internet download only if no bundled MSI is found.
    Returns dict with success and message.
    """
    if sys.platform != "win32":
        return {"success": False, "message": "Auto-install only supported on Windows"}

    msi_path: Optional[Path] = None
    for bundled in _BUNDLED_MSI_PATHS:
        p = Path(bundled)
        if p.exists() and p.stat().st_size > 0:
            msi_path = p
            logger.info("Using bundled Mumble MSI: %s", msi_path)
            break

    downloaded = False
    if msi_path is None:
        msi_path = Path(tempfile.gettempdir()) / "mumble-1.3.4.msi"
        try:
            logger.info("Bundled MSI not found — downloading Mumble 1.3.4 from GitHub...")
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
            timeout=120, capture_output=True, text=True,
        )
        if result.returncode in (0, 3010):
            logger.info("Mumble 1.3.4 installed (exit %s)", result.returncode)
            return {"success": True, "message": "Mumble 1.3.4 installed successfully"}
        msg = f"Mumble installer exited with code {result.returncode}"
        logger.error("%s — stderr: %s", msg, result.stderr)
        return {"success": False, "message": msg}
    except subprocess.TimeoutExpired:
        msg = "Mumble installer timed out after 120 s"
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
    Kill any running mumble.exe.
    Tries tracked PID first, then taskkill /IM as a safety net.
    Returns True if a process was found and killed.
    """
    global _mumble_proc
    killed = False

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
                logger.debug("taskkill /IM mumble.exe exit=%s (not running)", r.returncode)
        except Exception as e:
            logger.warning("taskkill /IM mumble.exe failed: %s", e)

    return killed


def launch_mumble(mumble_exe: Optional[str] = None, server_url: Optional[str] = None) -> dict:
    """
    Launch the Mumble desktop client and auto-connect via URL.

    Steps:
      1. Kill any existing mumble.exe (tracked PID + taskkill /IM)
      2. Wait 1.5 s for the process to fully exit
      3. Resolve mumble.exe from standard paths (auto-install if missing)
      4. Launch:  mumble.exe  <server_url>
      5. Verify mumble.exe is running after 1 s

    server_url format:
      mumble://<username>:<password>@<host>:<port>/<channel>
    Example:
      mumble://Sim3:fastestlap@192.168.1.200:64738/Race%20Control
    """
    global _mumble_proc

    logger.info("[launch-mumble] called — url=%s", server_url or "(none)")

    # Step 1 — always kill first so the URL-based launch always takes effect
    was_running = _kill_mumble()
    if was_running:
        logger.info("[launch-mumble] Killed existing Mumble — waiting 1.5 s...")
        time.sleep(1.5)
    else:
        logger.info("[launch-mumble] No existing Mumble process")

    # Step 2 — resolve exe
    exe = _find_mumble(mumble_exe)
    if not exe:
        logger.info("[launch-mumble] mumble.exe not found — attempting auto-install...")
        install_result = _install_mumble()
        if not install_result["success"]:
            return {**install_result, "exe": None, "url": server_url or ""}
        exe = _find_mumble(mumble_exe)
        if not exe:
            msg = "Mumble installed but exe still not found. Checked: " + ", ".join(_MUMBLE_PATHS)
            logger.error("[launch-mumble] %s", msg)
            return {"success": False, "message": msg, "exe": None, "url": server_url or ""}

    logger.info("[launch-mumble] exe=%s", exe)
    logger.info("[launch-mumble] url=%s", server_url or "(none)")

    # Step 3 — launch
    cmd = [exe]
    if server_url:
        cmd.append(server_url)

    logger.info("[launch-mumble] Spawning: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, close_fds=True)
        _mumble_proc = proc
        logger.info("[launch-mumble] Spawned PID %s — verifying after 1 s...", proc.pid)
    except Exception as e:
        msg = f"Failed to spawn Mumble: {e}"
        logger.error("[launch-mumble] %s", msg)
        return {"success": False, "message": msg, "exe": exe, "url": server_url or "", "verified": False}

    # Step 4 — verify
    time.sleep(1.0)
    running = _is_mumble_running()
    if running:
        logger.info("[launch-mumble] Verified mumble.exe is running (PID %s)", proc.pid)
    else:
        logger.warning("[launch-mumble] mumble.exe not found in tasklist after launch — may have crashed")

    return {
        "success": running,
        "message": f"Mumble launched (PID {proc.pid})" if running else "Mumble launched but not detected in tasklist",
        "exe": exe,
        "url": server_url or "",
        "pid": proc.pid,
        "verified": running,
    }


def close_mumble() -> dict:
    """
    Kill the Mumble process and verify it is gone.
    Idempotent: succeeds even if Mumble was not running.
    Returns: process_found, terminated, verified.
    """
    logger.info("[close-mumble] called")

    process_found = _is_mumble_running()
    logger.info("[close-mumble] mumble.exe running before kill: %s", process_found)

    killed = _kill_mumble()

    # Verify it is gone
    time.sleep(0.5)
    still_running = _is_mumble_running()
    verified_closed = not still_running

    if still_running:
        logger.warning("[close-mumble] mumble.exe still detected after taskkill")
    else:
        logger.info("[close-mumble] mumble.exe no longer running — confirmed closed")

    return {
        "success": verified_closed,
        "message": "Mumble closed" if verified_closed else "Mumble still running after kill attempt",
        "process_found": process_found,
        "terminated": killed,
        "verified": verified_closed,
    }
