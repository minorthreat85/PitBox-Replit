"""
Optional heartbeat to PitBox Controller: POST /api/heartbeat with X-Agent-Id and X-Agent-Token.
Runs in a daemon thread when controller_url is set in config.
"""
import logging
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_heartbeat_thread: threading.Thread | None = None
_stop_event: threading.Event = threading.Event()


def _heartbeat_loop(controller_url: str, agent_id: str, token: str, interval_sec: float = 60.0) -> None:
    """Loop: POST heartbeat every interval_sec until _stop_event is set."""
    base = controller_url.rstrip("/") + "/api/heartbeat"

    while not _stop_event.wait(interval_sec):
        req = urllib.request.Request(base, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Agent-Id", agent_id)
        req.add_header("X-Agent-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    logger.debug("Heartbeat OK: %s", base)
                else:
                    logger.warning("Heartbeat %s returned %s", base, resp.status)
        except urllib.error.HTTPError as e:
            logger.warning("Heartbeat failed: %s %s", base, e.code)
        except Exception as e:
            logger.debug("Heartbeat error: %s", e)


def start_heartbeat(controller_url: str, agent_id: str, token: str, interval_sec: float = 60.0) -> None:
    """Start daemon thread that POSTs heartbeat to controller. No-op if already running."""
    global _heartbeat_thread
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    _stop_event.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(controller_url, agent_id, token),
        kwargs={"interval_sec": interval_sec},
        daemon=True,
        name="controller-heartbeat",
    )
    _heartbeat_thread.start()
    logger.info("Controller heartbeat started: %s (every %.0fs)", controller_url, interval_sec)


def stop_heartbeat() -> None:
    """Signal heartbeat thread to stop (next wake)."""
    _stop_event.set()
