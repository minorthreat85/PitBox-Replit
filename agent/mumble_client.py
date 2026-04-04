"""
Mumble client launcher for PitBox Agent.
Launches the Mumble desktop client on Windows sim PCs.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_mumble_proc: Optional[subprocess.Popen] = None

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


def launch_mumble(mumble_exe: Optional[str] = None, server_url: Optional[str] = None) -> dict:
    """
    Launch the Mumble desktop client.
    Optional server_url: mumble://host[:port][/channel] — auto-connects on open.
    Returns dict with success, message.
    """
    global _mumble_proc

    exe = _find_mumble(mumble_exe)
    if not exe:
        msg = (
            "Mumble not found. Install Mumble on this sim PC. "
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
