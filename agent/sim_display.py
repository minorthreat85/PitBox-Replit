"""
Sim Display launcher for PitBox Agent.
Launches Chrome or Edge in kiosk fullscreen mode pointing at the controller's /sim page.
"""
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_sim_display_proc: Optional[subprocess.Popen] = None

_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser() -> Optional[str]:
    """Return path to Chrome (preferred) or Edge, or None if neither found."""
    for path in _CHROME_PATHS + _EDGE_PATHS:
        if Path(path).exists():
            return path
    return None


def build_display_url(controller_url: str, agent_id: str) -> str:
    """Build the sim display URL from controller base URL and agent_id."""
    base = (controller_url or "").rstrip("/")
    aid = (agent_id or "").strip()
    return f"{base}/sim?agent_id={aid}"


def launch_display(controller_url: str, agent_id: str, browser_path: Optional[str] = None) -> dict:
    """
    Launch the sim display browser in kiosk fullscreen mode.
    Returns dict with success, message, url, browser keys.
    """
    browser = browser_path or _find_browser()
    if not browser:
        msg = "No supported browser found (Chrome or Edge required)"
        logger.error(msg)
        return {"success": False, "message": msg, "url": None, "browser": None}

    global _sim_display_proc
    url = build_display_url(controller_url, agent_id)
    try:
        proc = subprocess.Popen(
            [browser, "--kiosk", f"--app={url}"],
            close_fds=True,
        )
        _sim_display_proc = proc
        logger.info("Launched sim display: %s via %s", url, browser)
        return {"success": True, "message": "Display launched", "url": url, "browser": browser}
    except Exception as e:
        msg = f"Failed to launch browser: {e}"
        logger.error(msg)
        return {"success": False, "message": msg, "url": url, "browser": browser}


def close_display() -> dict:
    """
    Kill the browser window launched by launch_display.
    Tries the tracked PID first; falls back to taskkill by image name on Windows.
    """
    import sys
    global _sim_display_proc
    killed = False
    messages = []

    if _sim_display_proc is not None:
        pid = _sim_display_proc.pid
        _sim_display_proc = None
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                import os, signal
                os.kill(pid, signal.SIGTERM)
            killed = True
            messages.append(f"Killed display process (PID {pid})")
            logger.info("Closed sim display process PID %s", pid)
        except Exception as e:
            messages.append(f"Could not kill PID {pid}: {e}")
            logger.warning("Failed to kill sim display PID %s: %s", pid, e)

    if sys.platform == "win32" and not killed:
        for exe in ("chrome.exe", "msedge.exe"):
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", exe],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    killed = True
                    messages.append(f"Killed {exe}")
                    logger.info("Closed sim display via taskkill %s", exe)
            except Exception as e:
                logger.debug("taskkill %s failed: %s", exe, e)

    if killed:
        return {"success": True, "message": "; ".join(messages) or "Display closed"}
    return {"success": False, "message": "No display process found to close"}


def schedule_launch(controller_url: str, agent_id: str, delay_seconds: float = 5.0, browser_path: Optional[str] = None):
    """
    Schedule a sim display launch in a background thread after delay_seconds.
    Returns immediately.
    """
    def _run():
        try:
            result = launch_display(controller_url, agent_id, browser_path=browser_path)
            if not result["success"]:
                logger.warning("Sim display launch failed: %s", result.get("message"))
        except Exception as e:
            logger.warning("Sim display scheduled launch error: %s", e)

    t = threading.Timer(delay_seconds, _run)
    t.daemon = True
    t.start()
    logger.info(
        "Sim display will launch in %.0fs (controller=%s, agent_id=%s)",
        delay_seconds, controller_url, agent_id,
    )
