"""
Graceful shutdown for PitBox Controller. Used by update apply and other triggers.
Ensures HTTP response is sent before shutdown begins; stops pollers/threads cleanly.
Windows/NSSM robust: SIGTERM first, timeout fallback to sys.exit, last resort os._exit.
"""
import logging
import os
import signal
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_shutdown_reason: Optional[str] = None
_shutdown_lock = threading.Lock()
_shutdown_scheduled = False
_server_shutdown_cb: Optional[Callable[[], None]] = None
_SHUTDOWN_TIMEOUT_SEC = 10


def set_server_shutdown_callback(cb: Callable[[], None]) -> None:
    """Set callback to trigger graceful server stop (e.g. uvicorn server.should_exit)."""
    global _server_shutdown_cb
    _server_shutdown_cb = cb


def request_shutdown(reason: str = "unknown", delay_sec: float = 1.5) -> None:
    """
    Request graceful shutdown. After delay (so HTTP response is sent):
    1. Try server shutdown callback (graceful) or SIGTERM
    2. Fallback: after 10s, sys.exit(0)
    3. Last resort: os._exit(0)
    """
    global _shutdown_reason, _shutdown_scheduled
    with _shutdown_lock:
        if _shutdown_scheduled:
            logger.warning("Shutdown already scheduled, ignoring request_shutdown(%s)", reason)
            return
        _shutdown_reason = reason
        _shutdown_scheduled = True

    def _do_shutdown():
        time.sleep(delay_sec)
        logger.info("Graceful shutdown: reason=%s", reason)
        if _server_shutdown_cb:
            try:
                _server_shutdown_cb()
                return
            except Exception as e:
                logger.warning("Server shutdown callback failed: %s", e)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def _fallback_exit():
        time.sleep(delay_sec + _SHUTDOWN_TIMEOUT_SEC)
        logger.warning("Shutdown timeout: forcing exit")
        try:
            sys.exit(0)
        except SystemExit:
            pass
        os._exit(0)

    threading.Thread(target=_do_shutdown, daemon=True, name="shutdown-trigger").start()
    threading.Thread(target=_fallback_exit, daemon=True, name="shutdown-fallback").start()


def get_shutdown_reason() -> Optional[str]:
    """Return the shutdown reason if shutdown was requested."""
    return _shutdown_reason
