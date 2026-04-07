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


def launch_mumble(mumble_exe: Optional[str] = None, server_url: Optional[str] = None) -> dict:
    """
    Launch the Mumble desktop client.
    If Mumble is not found, automatically downloads and installs it first.
    Optional server_url: mumble://host[:port][/channel] — auto-connects on open.
    Returns dict with success, message.
    """
    global _mumble_proc

    exe = _find_mumble(mumble_exe)

    if not exe:
        logger.info("Mumble not found — attempting auto-install...")
        install_result = _install_mumble()
        if not install_result["success"]:
            return install_result
        exe = _find_mumble(mumble_exe)
        if not exe:
            msg = (
                "Mumble installed but still not found at expected paths. "
                "Checked: " + ", ".join(_MUMBLE_PATHS)
            )
            logger.error(msg)
            return {"success": False, "message": msg}

    cmd = [exe]
    if server_url:
        cmd.append(server_url)

    try:
        proc = subprocess.Popen(cmd, close_fds=True)
        _mumble_proc = proc
        logger.info("Launched Mumble: %s (PID %s)", exe, proc.pid)
        return {"success": True, "message": f"Mumble launched (PID {proc.pid})", "exe": exe}
    except Exception as e:
        msg = f"Failed to launch Mumble: {e}"
        logger.error(msg)
        return {"success": False, "message": msg}


def close_mumble() -> dict:
    """
    Kill the Mumble process launched by launch_mumble.
    Falls back to taskkill by image name on Windows.
    """
    global _mumble_proc
    killed = False
    messages: list[str] = []

    if _mumble_proc is not None:
        pid = _mumble_proc.pid
        _mumble_proc = None
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                import os, signal
                os.kill(pid, signal.SIGTERM)
            killed = True
            messages.append(f"Killed Mumble process (PID {pid})")
            logger.info("Closed Mumble PID %s", pid)
        except Exception as e:
            messages.append(f"Could not kill PID {pid}: {e}")
            logger.warning("Failed to kill Mumble PID %s: %s", pid, e)

    if sys.platform == "win32" and not killed:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "mumble.exe"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                killed = True
                messages.append("Killed mumble.exe via taskkill")
                logger.info("Closed Mumble via taskkill")
        except Exception as e:
            logger.debug("taskkill mumble.exe failed: %s", e)

    if killed:
        return {"success": True, "message": "; ".join(messages) or "Mumble closed"}
    return {"success": False, "message": "No Mumble process found to close"}
