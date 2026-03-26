"""
LAN discovery: UDP listener for agent beacons. Agents send (agent_id, port); we collect by sender IP.
"""
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from pitbox_common.ports import DISCOVERY_UDP_PORT

logger = logging.getLogger(__name__)

DISCOVERY_PORT = DISCOVERY_UDP_PORT
BEACON_INTERVAL_SEC = 15
# Consider agent stale after this many seconds
STALE_SEC = 45


@dataclass
class DiscoveredAgent:
    agent_id: str
    host: str
    port: int
    last_seen: float


_discovered: dict[str, DiscoveredAgent] = {}
_lock: threading.Lock = threading.Lock()
_sock: socket.socket | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def _recv_loop() -> None:
    global _sock
    buf = bytearray(512)
    while not _stop.is_set() and _sock:
        try:
            _sock.settimeout(1.0)
            n, addr = _sock.recvfrom_into(buf)
            if n < 2:
                continue
            try:
                data = json.loads(buf[:n].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            agent_id = (data.get("agent_id") or data.get("id") or "").strip()
            port = data.get("port")
            if not agent_id or not isinstance(port, int) or port < 1 or port > 65535:
                continue
            host = addr[0]
            with _lock:
                _discovered[agent_id] = DiscoveredAgent(
                    agent_id=agent_id,
                    host=host,
                    port=port,
                    last_seen=time.time(),
                )
            logger.debug("Discovered agent %s at %s:%s", agent_id, host, port)
        except socket.timeout:
            continue
        except OSError as e:
            if not _stop.is_set():
                logger.debug("Discovery recv: %s", e)
            break
        except Exception as e:
            logger.warning("Discovery recv error: %s", e)


def start_discovery() -> None:
    """Start UDP listener on DISCOVERY_PORT. Idempotent."""
    global _sock, _thread
    with _lock:
        if _sock is not None:
            return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", DISCOVERY_PORT))
        _sock = s
        _stop.clear()
        _thread = threading.Thread(target=_recv_loop, daemon=True, name="discovery")
        _thread.start()
        logger.info("LAN discovery listening on UDP port %s", DISCOVERY_PORT)
    except OSError as e:
        logger.warning("Could not start discovery listener: %s", e)


def stop_discovery() -> None:
    """Stop the listener."""
    global _sock, _thread
    _stop.set()
    with _lock:
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


def get_discovered(stale_sec: float = STALE_SEC) -> list[dict]:
    """Return list of discovered agents (agent_id, host, port), dropping stale entries."""
    now = time.time()
    with _lock:
        to_drop = [aid for aid, a in _discovered.items() if (now - a.last_seen) > stale_sec]
        for aid in to_drop:
            del _discovered[aid]
        return [
            {"agent_id": a.agent_id, "host": a.host, "port": a.port}
            for a in _discovered.values()
        ]
