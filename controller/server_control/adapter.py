"""PitBox native server-control adapter.

Sends admin commands (chat, kick, next-session, restart-session, generic
``/admin`` text) to a running ``acServer.exe`` instance through the
Assetto Corsa UDP plugin protocol. The adapter does **not** own the
acServer.exe lifecycle -- that responsibility stays with
``controller.api_server_config_routes`` (multi-instance) and
``controller.server_pool`` (dynamic pool). It only sends datagrams to
``UDP_PLUGIN_LOCAL_PORT`` of an already-running server.

The adapter is intentionally thin: each ``send_*`` method serialises one
UDP packet using the same little-endian / utf_32_le wire format as the
upstream ``acudpclient`` package and posts it via a process-wide UDP
socket. Failures (no server running, port resolution failure, socket
error) are logged with full context and surfaced as
``ServerControlError`` to the API layer.

Per-server-id resolution: each PitBox server preset stores its
``UDP_PLUGIN_LOCAL_PORT`` (and optional ``UDP_PLUGIN_ADDRESS`` host) in
``cfg/server_cfg.ini``. The adapter reads that file lazily and caches
the resolved ``(host, port)`` per ``server_id``. The cache is invalidated
whenever ``invalidate_target(server_id)`` is called (e.g. on server
stop/restart).
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from controller.ini_io import read_ini
from controller.server_preset_helpers import (
    _get_server_preset_dir_safe,
    _preset_ini_paths,
)
from controller.timing.constants import (
    TIMING_UDP_PLUGIN_HOST,
    TIMING_UDP_PLUGIN_LOCAL_PORT,
)
from controller.timing.vendor.acudpclient.protocol import ACUDPConst

logger = logging.getLogger(__name__)


class ServerControlError(RuntimeError):
    """Raised for adapter-level failures (resolution, socket, validation)."""


@dataclass(frozen=True)
class _Target:
    host: str
    port: int


def _utf32_payload(text: str) -> bytes:
    """Encode a UTF-32-LE string with a single-byte length prefix.

    AC's UDP plugin uses ``uint8 length`` followed by ``length * 4`` bytes
    of UTF-32-LE characters for chat/admin payloads. Maximum 255 chars.
    """
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    if len(text) > 255:
        raise ServerControlError(
            f"Text too long for AC UDP packet: {len(text)} chars (max 255)"
        )
    return struct.pack("B", len(text)) + text.encode("utf_32_le")


class ServerControlAdapter:
    """Thread-safe sender for AC UDP admin commands."""

    SOCKET_TIMEOUT_S = 1.5

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._target_cache: dict[str, _Target] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _get_sock(self) -> socket.socket:
        with self._lock:
            if self._sock is None:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setblocking(False)
                self._sock = s
            return self._sock

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._target_cache.clear()

    def invalidate_target(self, server_id: str) -> None:
        """Drop cached host/port for ``server_id`` (call on stop/restart)."""
        with self._lock:
            self._target_cache.pop(server_id, None)

    # ------------------------------------------------------------------ #
    # Target resolution
    # ------------------------------------------------------------------ #
    def resolve_target(self, server_id: str) -> _Target:
        """Resolve ``(host, port)`` for the server's UDP admin endpoint.

        Reads ``cfg/server_cfg.ini`` (preferred) or the preset root's
        ``server_cfg.ini`` to get ``[SERVER] UDP_PLUGIN_LOCAL_PORT`` and
        ``UDP_PLUGIN_ADDRESS``. Falls back to PitBox defaults (127.0.0.1
        : 9999) if either field is blank, matching the auto-fill logic in
        ``api_server_config_routes._ensure_udp_plugin_in_server_cfg``.
        """
        with self._lock:
            cached = self._target_cache.get(server_id)
            if cached is not None:
                return cached

        preset_dir = _get_server_preset_dir_safe(server_id)
        if not preset_dir.is_dir():
            raise ServerControlError(
                f"Preset directory not found for server_id={server_id!r}: {preset_dir}"
            )

        sc_path: Path
        cfg_sc = preset_dir / "cfg" / "server_cfg.ini"
        if cfg_sc.exists():
            sc_path = cfg_sc
        else:
            sc_path, _ = _preset_ini_paths(preset_dir)
        if not sc_path.exists():
            raise ServerControlError(
                f"server_cfg.ini missing for server_id={server_id!r} (looked in {cfg_sc} and {sc_path})"
            )

        data = read_ini(sc_path)
        server_section = None
        for sect, opts in data.items():
            if sect.upper() == "SERVER":
                server_section = opts
                break
        if server_section is None:
            raise ServerControlError(
                f"[SERVER] section missing in {sc_path}"
            )

        port_raw = str(server_section.get("UDP_PLUGIN_LOCAL_PORT", "")).strip()
        addr_raw = str(server_section.get("UDP_PLUGIN_ADDRESS", "")).strip()

        try:
            port = int(port_raw) if port_raw else int(TIMING_UDP_PLUGIN_LOCAL_PORT)
        except ValueError as exc:
            raise ServerControlError(
                f"UDP_PLUGIN_LOCAL_PORT in {sc_path} is not an integer: {port_raw!r}"
            ) from exc
        if port <= 0 or port > 65535:
            raise ServerControlError(
                f"UDP_PLUGIN_LOCAL_PORT out of range in {sc_path}: {port}"
            )

        host = TIMING_UDP_PLUGIN_HOST
        if addr_raw:
            # UDP_PLUGIN_ADDRESS is the *outgoing* telemetry endpoint
            # (acServer -> PitBox); we mirror its host so admin commands
            # go to the same machine. Strip an optional port suffix.
            host_part = addr_raw.split(":", 1)[0].strip()
            if host_part:
                host = host_part

        target = _Target(host=host, port=port)
        with self._lock:
            self._target_cache[server_id] = target
        return target

    # ------------------------------------------------------------------ #
    # Low-level send
    # ------------------------------------------------------------------ #
    def _send(self, server_id: str, payload: bytes, *, op: str) -> None:
        target = self.resolve_target(server_id)
        sock = self._get_sock()
        try:
            sent = sock.sendto(payload, (target.host, target.port))
        except OSError as exc:
            logger.error(
                "server_control: %s send failed for server_id=%s -> %s:%s: %s",
                op, server_id, target.host, target.port, exc,
            )
            raise ServerControlError(
                f"UDP send failed for {op} -> {target.host}:{target.port}: {exc}"
            ) from exc
        if sent != len(payload):
            logger.error(
                "server_control: %s short send for server_id=%s (%s/%s bytes)",
                op, server_id, sent, len(payload),
            )
            raise ServerControlError(
                f"UDP short send for {op}: {sent}/{len(payload)} bytes"
            )
        logger.info(
            "server_control: %s -> server_id=%s %s:%s (%s bytes)",
            op, server_id, target.host, target.port, len(payload),
        )

    # ------------------------------------------------------------------ #
    # Public commands
    # ------------------------------------------------------------------ #
    def broadcast_chat(self, server_id: str, message: str) -> None:
        """Broadcast a chat message to every connected driver."""
        payload = struct.pack("B", ACUDPConst.ACSP_BROADCAST_CHAT) + _utf32_payload(message)
        self._send(server_id, payload, op="broadcast_chat")

    def send_chat_to_car(self, server_id: str, car_id: int, message: str) -> None:
        """Send a private chat message to a single car_id."""
        if not (0 <= int(car_id) <= 255):
            raise ServerControlError(f"car_id out of range: {car_id}")
        payload = (
            struct.pack("BB", ACUDPConst.ACSP_SEND_CHAT, int(car_id))
            + _utf32_payload(message)
        )
        self._send(server_id, payload, op="send_chat")

    def kick_user(self, server_id: str, car_id: int) -> None:
        """Kick the driver in ``car_id``."""
        if not (0 <= int(car_id) <= 255):
            raise ServerControlError(f"car_id out of range: {car_id}")
        payload = struct.pack("BB", ACUDPConst.ACSP_KICK_USER, int(car_id))
        self._send(server_id, payload, op="kick_user")

    def next_session(self, server_id: str) -> None:
        """Advance the dedicated server to the next session."""
        payload = struct.pack("B", ACUDPConst.ACSP_NEXT_SESSION)
        self._send(server_id, payload, op="next_session")

    def restart_session(self, server_id: str) -> None:
        """Restart the current session."""
        payload = struct.pack("B", ACUDPConst.ACSP_RESTART_SESSION)
        self._send(server_id, payload, op="restart_session")

    def admin_command(self, server_id: str, command: str) -> None:
        """Send an arbitrary ``/admin`` text command (max 255 chars).

        Useful for AC admin commands that don't have a dedicated opcode
        (e.g. ``/ballast 3 50``, ``/restrict 1 20``, ``/help``). The
        leading ``/`` is preserved if present; AC parses the remainder.
        """
        text = (command or "").strip()
        if not text:
            raise ServerControlError("admin_command requires non-empty text")
        payload = struct.pack("B", ACUDPConst.ACSP_ADMIN_COMMAND) + _utf32_payload(text)
        self._send(server_id, payload, op="admin_command")

    def request_session_info(self, server_id: str, session_index: int = -1) -> None:
        """Ask acServer to re-emit the session info packet (telemetry side).

        The reply lands on the timing engine's UDP listener (port 9996)
        and updates ``TimingEngine.snapshot()``; this method just nudges
        the server to send it now.
        """
        payload = struct.pack("<Bh", ACUDPConst.ACSP_GET_SESSION_INFO, int(session_index))
        self._send(server_id, payload, op="get_session_info")

    def request_car_info(self, server_id: str, car_id: int) -> None:
        """Ask acServer to re-emit a car-info packet for ``car_id``."""
        if not (0 <= int(car_id) <= 255):
            raise ServerControlError(f"car_id out of range: {car_id}")
        payload = struct.pack("BB", ACUDPConst.ACSP_GET_CAR_INFO, int(car_id))
        self._send(server_id, payload, op="get_car_info")


# ---------------------------------------------------------------------- #
# Process-wide singleton
# ---------------------------------------------------------------------- #
_adapter_lock = threading.Lock()
_adapter_instance: Optional[ServerControlAdapter] = None


def get_adapter() -> ServerControlAdapter:
    """Return the process-wide :class:`ServerControlAdapter` singleton."""
    global _adapter_instance
    with _adapter_lock:
        if _adapter_instance is None:
            _adapter_instance = ServerControlAdapter()
        return _adapter_instance
