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

    url = build_display_url(controller_url, agent_id)
    try:
        subprocess.Popen(
            [browser, "--kiosk", f"--app={url}"],
            close_fds=True,
        )
        logger.info("Launched sim display: %s via %s", url, browser)
        return {"success": True, "message": "Display launched", "url": url, "browser": browser}
    except Exception as e:
        msg = f"Failed to launch browser: {e}"
        logger.error(msg)
        return {"success": False, "message": msg, "url": url, "browser": browser}


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
