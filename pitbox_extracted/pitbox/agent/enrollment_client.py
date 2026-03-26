"""
Enrollment client: listen for controller UDP broadcast (port 9640), then POST /api/pair/enroll.
Uses sender address from recvfrom() as controller IP (IPv4); validates and never connects to 0.0.0.0/broadcast.
"""
import json
import logging
import socket
import threading
import time
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

import httpx

from pitbox_common.ports import CONTROLLER_HTTP_PORT, ENROLLMENT_UDP_PORT

logger = logging.getLogger(__name__)
RECV_TIMEOUT = 2.0
RETRY_DELAY = 5.0
INVALID_PAYLOAD_LOG_INTERVAL_SEC = 60.0

_last_invalid_log_time: float = 0.0


def _is_valid_controller_ip(ip: str) -> bool:
    """True if IP is a usable IPv4 for connecting to controller (not 0.0.0.0, not broadcast, not loopback)."""
    if not ip or not isinstance(ip, str):
        return False
    ip = ip.strip()
    if ip in ("0.0.0.0", "255.255.255.255"):
        return False
    if ip.startswith("127."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        if int(parts[3]) == 255:
            return False
        for p in parts:
            if not 0 <= int(p) <= 255:
                return False
    except ValueError:
        return False
    return True


def _recv_broadcast(sock: socket.socket) -> Optional[Tuple[dict, Tuple[str, int]]]:
    """Receive one UDP broadcast. Returns (parsed_json, (sender_ip, sender_port)) or None. IPv4 only."""
    buf = bytearray(1024)
    try:
        sock.settimeout(RECV_TIMEOUT)
        n, sender_addr = sock.recvfrom_into(buf)
        if n < 2:
            return None
        data = json.loads(buf[:n].decode("utf-8"))
        if not isinstance(data, dict):
            return None
        if not isinstance(sender_addr, (tuple, list)) or len(sender_addr) < 2:
            return (data, ("", 0))
        sender_ip = str(sender_addr[0]).strip()
        sender_port = int(sender_addr[1]) if sender_addr[1] is not None else 0
        return (data, (sender_ip, sender_port))
    except (socket.timeout, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def run_enrollment_loop(
    device_id: str,
    hostname: str,
    agent_port: int,
    agent_host: str,
    on_enrolled: Callable[[str, str], None],
    stop: threading.Event,
) -> None:
    """
    Run in a background thread. Listen for controller broadcast; when enrollment_mode true,
    use sender IP from recvfrom() as controller address, validate (no 0.0.0.0/broadcast),
    POST /api/pair/enroll. On success call on_enrolled(controller_base_url, token).
    """
    global _last_invalid_log_time
    while not stop.wait(0.5):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", ENROLLMENT_UDP_PORT))
            except OSError as e:
                logger.debug("Enrollment bind: %s", e)
                s.close()
                time.sleep(RETRY_DELAY)
                continue
            result = _recv_broadcast(s)
            s.close()
            if not result:
                continue
            payload, (sender_ip, _sender_port) = result
            if not payload.get("enrollment_mode"):
                continue
            endpoint = (payload.get("enrollment_endpoint") or "/api/pair/enroll").strip()
            secret = (payload.get("enrollment_secret") or "").strip()
            if not secret:
                continue
            url_from_payload = (payload.get("controller_http_url") or "").strip().rstrip("/")
            controller_port = CONTROLLER_HTTP_PORT
            if url_from_payload:
                try:
                    parsed = urlparse(url_from_payload if "://" in url_from_payload else f"http://{url_from_payload}")
                    controller_port = parsed.port or CONTROLLER_HTTP_PORT
                except Exception:
                    pass
            controller_ip = sender_ip if _is_valid_controller_ip(sender_ip) else None
            if not controller_ip and url_from_payload:
                try:
                    parsed = urlparse(url_from_payload if "://" in url_from_payload else f"http://{url_from_payload}")
                    controller_ip = (parsed.hostname or "").strip()
                    if not _is_valid_controller_ip(controller_ip):
                        controller_ip = None
                except Exception:
                    pass
            if not controller_ip:
                now = time.monotonic()
                if now - _last_invalid_log_time >= INVALID_PAYLOAD_LOG_INTERVAL_SEC:
                    _last_invalid_log_time = now
                    logger.warning(
                        "Enrollment: ignoring broadcast with invalid controller address (use sender or payload IP). "
                        "Payload url=%s sender_ip=%s",
                        url_from_payload or "(none)",
                        sender_ip or "(none)",
                    )
                continue
            enroll_url = f"http://{controller_ip}:{controller_port}{endpoint}" if endpoint.startswith("/") else f"http://{controller_ip}:{controller_port}/{endpoint}"
            controller_base_url = f"http://{controller_ip}:{controller_port}"
            logger.debug(
                "Enrollment: received broadcast payload url=%s endpoint=%s; using sender_ip=%s -> enroll_url=%s",
                url_from_payload, endpoint, controller_ip, enroll_url,
            )
            body = {
                "device_id": device_id,
                "hostname": hostname,
                "agent_port": agent_port,
                "enrollment_secret": secret,
            }
            if agent_host and agent_host != "0.0.0.0":
                body["host"] = agent_host
            try:
                with httpx.Client(timeout=10.0) as client:
                    r = client.post(enroll_url, json=body)
                if r.status_code != 200:
                    logger.warning("Enroll rejected: %s %s", r.status_code, (r.text or "")[:200])
                    continue
                data = r.json() or {}
                token = (data.get("token") or "").strip()
                if not token:
                    logger.warning("Enroll response missing token")
                    continue
                logger.info("Enrolled with controller at %s (sender_ip=%s)", controller_base_url, controller_ip)
                on_enrolled(controller_base_url, token)
                return
            except Exception as e:
                logger.warning(
                    "Enroll request failed: %s (url=%s). Check firewall on controller PC: allow inbound TCP %s.",
                    e, enroll_url, controller_port,
                )
        except Exception as e:
            logger.debug("Enrollment loop: %s", e)
        time.sleep(RETRY_DELAY)
