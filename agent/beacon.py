"""
Optional LAN beacon: broadcast agent_id and port so the controller can discover this agent.
Does not send token. See ENROLLMENT.md.
"""
import json
import logging
import socket
import threading
import time

from pitbox_common.ports import DISCOVERY_UDP_PORT

logger = logging.getLogger(__name__)

DISCOVERY_PORT = DISCOVERY_UDP_PORT
BEACON_INTERVAL_SEC = 15

_sock: socket.socket | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def _beacon_loop(agent_id: str, port: int) -> None:
    payload = json.dumps({"agent_id": agent_id, "port": port}).encode("utf-8")
    while not _stop.wait(BEACON_INTERVAL_SEC):
        if not _sock:
            break
        try:
            _sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
            logger.debug("Beacon sent %s:%s", agent_id, port)
        except OSError as e:
            logger.debug("Beacon send: %s", e)


def start_beacon(agent_id: str, port: int) -> None:
    """Start UDP broadcast beacon. Idempotent."""
    global _sock, _thread
    if _thread is not None and _thread.is_alive():
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _sock = s
        _stop.clear()
        _thread = threading.Thread(
            target=_beacon_loop,
            args=(agent_id, port),
            daemon=True,
            name="beacon",
        )
        _thread.start()
        logger.info("LAN beacon started (agent_id=%s, port=%s)", agent_id, port)
    except OSError as e:
        logger.warning("Could not start beacon: %s", e)


def stop_beacon() -> None:
    """Stop the beacon thread."""
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
