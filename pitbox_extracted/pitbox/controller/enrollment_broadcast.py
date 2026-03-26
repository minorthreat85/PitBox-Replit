"""
UDP broadcast when enrollment mode is ON: agents discover controller and enrollment endpoint.
Port 9640 (different from agent beacon 9631). Payload always advertises a usable IPv4 (never 0.0.0.0/broadcast).
"""
import json
import logging
import socket
import threading
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse

from controller.config import _is_invalid_advertise_ip, resolve_lan_ip
from controller.enrollment import get_state
from pitbox_common.ports import CONTROLLER_HTTP_PORT, ENROLLMENT_UDP_PORT

logger = logging.getLogger(__name__)
BROADCAST_INTERVAL_SEC = 2.0

_sock: Optional[socket.socket] = None
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_get_controller_url: Optional[Callable[[], str]] = None


def set_controller_url_provider(fn: Callable[[], str]) -> None:
    """Set callback that returns controller HTTP base URL (e.g. http://192.168.1.200:9630)."""
    global _get_controller_url
    _get_controller_url = fn


def _broadcast_loop() -> None:
    global _sock
    while not _stop.wait(BROADCAST_INTERVAL_SEC):
        if not _sock:
            break
        state = get_state()
        if not state.get("enabled") or state.get("seconds_remaining", 0) <= 0:
            continue
        url = (_get_controller_url or (lambda: f"http://127.0.0.1:{CONTROLLER_HTTP_PORT}"))().rstrip("/")
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").strip()
            if not host or _is_invalid_advertise_ip(host):
                lan = resolve_lan_ip()
                if lan:
                    port = parsed.port or CONTROLLER_HTTP_PORT
                    url = urlunparse((parsed.scheme or "http", f"{lan}:{port}", parsed.path or "", "", "", ""))
        except Exception:
            pass
        payload = {
            "controller_http_url": url,
            "enrollment_mode": True,
            "enrollment_endpoint": "/api/pair/enroll",
            "enrollment_secret": state.get("secret", ""),
        }
        try:
            msg = json.dumps(payload).encode("utf-8")
            _sock.sendto(msg, ("255.255.255.255", ENROLLMENT_UDP_PORT))
            logger.debug("Enrollment broadcast sent")
        except OSError as e:
            logger.debug("Enrollment broadcast: %s", e)


def start() -> None:
    """Start broadcast thread. Idempotent."""
    global _sock, _thread
    if _thread is not None and _thread.is_alive():
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _sock = s
        _stop.clear()
        _thread = threading.Thread(target=_broadcast_loop, daemon=True, name="enrollment_broadcast")
        _thread.start()
        logger.info("Enrollment broadcast thread started (UDP %s)", ENROLLMENT_UDP_PORT)
    except OSError as e:
        logger.warning("Could not start enrollment broadcast: %s", e)


def stop() -> None:
    """Stop broadcast thread."""
    global _sock, _thread
    _stop.set()
    s = _sock
    _sock = None
    if s:
        try:
            s.close()
        except OSError:
            pass
    if _thread and _thread.is_alive():
        _thread.join(timeout=2)
    _thread = None
