"""PitBox native timing engine.

Listens on the AC dedicated server's UDP plugin port (default 9996), parses
each datagram with the vendored ``acudpclient`` packet definitions and
maintains an in-memory snapshot of the current session, drivers and recent
events. Phase 3 adds HTTP/WebSocket routes on top of ``snapshot()`` and
``events_since()``.

The engine is asyncio-native: each datagram is one AC packet, so we wrap the
payload in a ``BytesIO`` and call ``ACUDPPacket.factory`` directly. No threads,
no blocking sockets.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Deque, Dict, Optional

from controller.timing.constants import (
    TIMING_UDP_PLUGIN_HOST,
    TIMING_UDP_PLUGIN_PORT,
)
from controller.timing.vendor.acudpclient.exceptions import NotEnoughBytes
from controller.timing.vendor.acudpclient.packet_base import ACUDPPacket
from controller.timing.vendor.acudpclient.protocol import ACUDPConst
# Import packets module to register all ACUDPPacket subclasses with the factory.
from controller.timing.vendor.acudpclient import packets  # noqa: F401


LOG = logging.getLogger("pitbox.timing")

_SESSION_TYPE_NAMES = {
    0: "Booking",
    1: "Practice",
    2: "Qualify",
    3: "Race",
    4: "Hotlap",
    5: "Time Attack",
    6: "Drift",
    7: "Drag",
}

_CLIENT_EVENT_NAMES = {
    ACUDPConst.ACSP_CE_COLLISION_WITH_CAR: "collision_with_car",
    ACUDPConst.ACSP_CE_COLLISION_WITH_ENV: "collision_with_env",
}

_MAX_EVENTS = 200


@dataclass
class SessionState:
    server_name: str = ""
    track_name: str = ""
    track_config: str = ""
    session_name: str = ""
    session_type: int = 0
    session_type_name: str = ""
    session_index: int = 0
    current_session_index: int = 0
    session_count: int = 0
    proto_version: int = 0
    time_minutes: int = 0
    laps: int = 0
    wait_time: int = 0
    ambient_temp: int = 0
    track_temp: int = 0
    weather_graph: str = ""
    elapsed_ms: int = 0
    started_at_unix: float = 0.0


@dataclass
class DriverState:
    car_id: int
    connected: bool = False
    driver_name: str = ""
    driver_guid: str = ""
    driver_team: str = ""
    car_model: str = ""
    car_skin: str = ""
    last_lap_ms: int = 0
    best_lap_ms: int = 0
    total_laps: int = 0
    position: int = 0
    gap_ms: int = 0
    cuts_last_lap: int = 0
    loaded: bool = False


class TimingEngine:
    """In-memory model of a single AC server's timing state."""

    def __init__(self) -> None:
        self.session = SessionState()
        self.drivers: Dict[int, DriverState] = {}
        self.events: Deque[Dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
        self._event_seq = 0
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional["_TimingProtocol"] = None
        self._lock = asyncio.Lock()
        self.host = TIMING_UDP_PLUGIN_HOST
        self.port = TIMING_UDP_PLUGIN_PORT
        self.last_packet_unix: float = 0.0
        self.packets_received: int = 0
        self.unknown_packets: int = 0

    # ---- lifecycle ----
    async def start(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        if self._transport is not None:
            return
        if host is not None:
            self.host = host
        if port is not None:
            self.port = port
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _TimingProtocol(self),
                local_addr=(self.host, self.port),
            )
        except OSError as exc:
            LOG.warning(
                "Timing engine could not bind %s:%s (%s); will run in cold-start mode.",
                self.host, self.port, exc,
            )
            return
        self._transport = transport
        self._protocol = protocol
        LOG.info("Timing engine listening on udp://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # pragma: no cover
                pass
        self._transport = None
        self._protocol = None

    def is_running(self) -> bool:
        return self._transport is not None

    # ---- public read API ----
    def snapshot(self) -> Dict[str, Any]:
        drivers_sorted = sorted(
            self.drivers.values(),
            key=lambda d: (d.position if d.position > 0 else 999, d.car_id),
        )
        driver_dicts = [asdict(d) for d in drivers_sorted]

        # ---- Phase C: merge per-agent live telemetry from sim PCs ----
        # Best-effort: keyed by case-insensitive driver name. Telemetry blocks
        # are also returned at top level under `telemetry_agents` so the UI can
        # render unmatched agents (e.g. spectator sims, mismatched names).
        telemetry_agents: Dict[str, Any] = {}
        try:
            from controller.telemetry.store import get_store as _get_tel_store
            tel = _get_tel_store().project_for_engine()
            telemetry_agents = tel
            if tel:
                # Build lookup: nick -> agent_id (lower)
                nick_to_aid = {}
                for aid, block in tel.items():
                    nick = (block.get("player_nick") or "").strip().lower()
                    if nick:
                        nick_to_aid.setdefault(nick, aid)
                for d in driver_dicts:
                    name = (d.get("driver_name") or "").strip().lower()
                    if not name:
                        continue
                    aid = nick_to_aid.get(name)
                    # Try simple variants: split on whitespace, or last token
                    if not aid and " " in name:
                        for tok in (name.split()[-1], name.split()[0]):
                            aid = nick_to_aid.get(tok)
                            if aid:
                                break
                    if aid:
                        d["live_telemetry"] = tel[aid]
        except Exception:
            LOG.debug("telemetry merge skipped", exc_info=True)

        return {
            "session": asdict(self.session),
            "drivers": driver_dicts,
            "telemetry_agents": telemetry_agents,
            "stats": {
                "running": self.is_running(),
                "host": self.host,
                "port": self.port,
                "packets_received": self.packets_received,
                "unknown_packets": self.unknown_packets,
                "last_packet_unix": self.last_packet_unix,
                "event_seq": self._event_seq,
            },
        }

    def events_since(self, seq: int = 0, limit: int = 100) -> Dict[str, Any]:
        items = [e for e in self.events if e["seq"] > seq]
        items = items[-limit:]
        return {
            "events": items,
            "next_seq": self._event_seq,
        }

    # ---- packet ingestion ----
    def handle_datagram(self, data: bytes) -> None:
        self.packets_received += 1
        self.last_packet_unix = time.time()
        buf = io.BytesIO(data)
        try:
            packet = ACUDPPacket.factory(buf)
        except NotEnoughBytes:
            LOG.debug("Truncated AC packet ignored (%d bytes)", len(data))
            return
        except NotImplementedError as exc:
            self.unknown_packets += 1
            LOG.debug("Unknown AC packet ignored: %s", exc)
            return
        except Exception:
            LOG.exception("Failed to decode AC packet (%d bytes)", len(data))
            return
        try:
            self._dispatch(packet)
        except Exception:
            LOG.exception("Error handling packet %s", packet)

    def _dispatch(self, packet: ACUDPPacket) -> None:
        name = packet.packet_name() or ""
        handler = getattr(self, "_on_" + name, None)
        if handler is None:
            return
        handler(packet)

    # ---- packet handlers ----
    def _on_ACSP_VERSION(self, p):
        self.session.proto_version = int(p.proto_version)
        self._record_event("server_version", proto_version=int(p.proto_version))


    def _on_ACSP_NEW_SESSION(self, p):
        self._apply_session_info(p)
        self.session.started_at_unix = time.time()
        # Reset per-session driver lap totals; keep identities.
        for d in self.drivers.values():
            d.last_lap_ms = 0
            d.best_lap_ms = 0
            d.total_laps = 0
            d.position = 0
            d.gap_ms = 0
            d.cuts_last_lap = 0
        self._record_event(
            "new_session",
            session_name=self.session.session_name,
            session_type=self.session.session_type_name,
            layout=self.session.track_config,
        )

    def _on_ACSP_SESSION_INFO(self, p):
        self._apply_session_info(p)

    def _apply_session_info(self, p):
        s = self.session
        s.proto_version = int(p.proto_version)
        s.session_index = int(p.session_index)
        s.current_session_index = int(p.current_sess_index)
        s.session_count = int(p.session_count)
        s.server_name = str(p.server_name)
        s.track_name = str(p.track_name)
        s.track_config = str(p.track_config)
        s.session_name = str(p.name)
        s.session_type = int(p.session_type)
        s.session_type_name = _SESSION_TYPE_NAMES.get(s.session_type, "Unknown")
        s.time_minutes = int(p.time)
        s.laps = int(p.laps)
        s.wait_time = int(p.wait_time)
        s.ambient_temp = int(p.ambient_temp)
        s.track_temp = int(p.track_temp)
        s.weather_graph = str(p.weather_graph)
        s.elapsed_ms = int(p.elapsed_ms)

    def _on_ACSP_END_SESSION(self, p):
        self._record_event("end_session", filename=str(p.filename))


    def _on_ACSP_NEW_CONNECTION(self, p):
        car_id = int(p.car_id)
        new_guid = str(p.driver_guid)
        existing = self.drivers.get(car_id)
        d = self._driver(car_id)
        # If this slot is being taken by a different human (different
        # GUID), wipe the previous driver's per-session stats so they
        # don't leak. Same GUID = reconnect, keep stats.
        if existing is not None and existing.driver_guid and existing.driver_guid != new_guid:
            d.last_lap_ms = 0
            d.best_lap_ms = 0
            d.total_laps = 0
            d.position = 0
            d.gap_ms = 0
            d.cuts_last_lap = 0
        d.connected = True
        d.driver_name = str(p.driver_name)
        d.driver_guid = new_guid
        d.car_model = str(p.car_model)
        d.car_skin = str(p.car_skin)
        d.loaded = False
        self._record_event(
            "driver_connected",
            car_id=d.car_id,
            driver=d.driver_name,
            car_model=d.car_model,
        )

    def _on_ACSP_CONNECTION_CLOSED(self, p):
        car_id = int(p.car_id)
        d = self._driver(car_id)
        d.connected = False
        d.loaded = False
        # Drop position so the leaderboard doesn't keep showing this
        # driver mid-pack after they leave; lap stats are preserved so
        # the operator can still see what they did before disconnect.
        d.position = 0
        d.gap_ms = 0
        self._record_event(
            "driver_disconnected",
            car_id=car_id,
            driver=str(p.driver_name),
        )

    def _on_ACSP_CLIENT_LOADED(self, p):
        d = self._driver(int(p.car_id))
        d.loaded = True
        self._record_event("client_loaded", car_id=d.car_id, driver=d.driver_name)

    def _on_ACSP_CAR_INFO(self, p):
        d = self._driver(int(p.car_id))
        d.connected = bool(p.is_connected)
        d.car_model = str(p.car_model)
        d.car_skin = str(p.car_skin)
        d.driver_name = str(p.driver_name)
        d.driver_team = str(p.driver_team)
        d.driver_guid = str(p.driver_guid)

    def _on_ACSP_CHAT(self, p):
        car_id = int(p.car_id)
        d = self.drivers.get(car_id)
        self._record_event(
            "chat",
            car_id=car_id,
            driver=d.driver_name if d else "",
            message=str(p.message),
        )

    def _on_ACSP_LAP_COMPLETED(self, p):
        car_id = int(p.car_id)
        d = self._driver(car_id)
        lap_ms = int(p.lap_time)
        d.last_lap_ms = lap_ms
        d.cuts_last_lap = int(p.cuts)
        d.total_laps += 1
        if lap_ms > 0 and (d.best_lap_ms == 0 or lap_ms < d.best_lap_ms):
            d.best_lap_ms = lap_ms

        # Apply leaderboard ordering / gap-to-leader from the embedded
        # array. AC sets ``has_completed_flag=False`` for cars that have
        # never crossed the line in this session; their ``rtime`` is
        # meaningless so we still record the position but leave gap=0
        # rather than charting an absurd value. The leader baseline is
        # the first car in the list with a real completed lap (so a
        # mixed list where idx 1 hasn't yet completed still produces
        # correct gaps for later completed entries).
        leader_time: Optional[int] = None
        for idx, entry in enumerate(getattr(p, "cars", []) or [], start=1):
            rcar_id = int(entry.rcar_id)
            rtime = int(entry.rtime)
            has_completed = bool(getattr(entry, "has_completed_flag", True))
            rd = self._driver(rcar_id)
            rd.position = idx
            rd.total_laps = max(rd.total_laps, int(entry.rlaps))
            if not has_completed:
                rd.gap_ms = 0
                continue
            if leader_time is None:
                leader_time = rtime
                rd.gap_ms = 0
            else:
                rd.gap_ms = max(0, rtime - leader_time)

        self._record_event(
            "lap_completed",
            car_id=car_id,
            driver=d.driver_name,
            lap_ms=lap_ms,
            cuts=d.cuts_last_lap,
            total_laps=d.total_laps,
            position=d.position,
            grip_level=float(getattr(p, "grip_level", 0.0)),
        )

    def _on_ACSP_CLIENT_EVENT(self, p):
        ev_type = int(p.ev_type)
        car_id = int(p.car_id)
        d = self.drivers.get(car_id)
        payload: dict[str, Any] = {
            "subtype": _CLIENT_EVENT_NAMES.get(ev_type, "client_event"),
            "impact_speed": float(p.impact_speed),
        }
        if ev_type == ACUDPConst.ACSP_CE_COLLISION_WITH_CAR:
            other_id = int(p.other_car_id)
            other = self.drivers.get(other_id)
            payload["other_car_id"] = other_id
            payload["other_driver"] = other.driver_name if other else ""
        self._record_event(
            "client_event",
            car_id=car_id,
            driver=d.driver_name if d else "",
            **payload,
        )

    def _on_ACSP_ERROR(self, p):
        self._record_event("ac_error", message=str(p.message))


    def _on_ACSP_CAR_UPDATE(self, p):  # realtime telemetry; ignored by default
        return

    # ---- helpers ----
    def _driver(self, car_id: int) -> DriverState:
        d = self.drivers.get(car_id)
        if d is None:
            d = DriverState(car_id=car_id)
            self.drivers[car_id] = d
        return d

    def _record_event(
        self,
        type: str,
        *,
        car_id: Optional[int] = None,
        driver: Optional[str] = None,
        lap_ms: Optional[int] = None,
        **payload: Any,
    ) -> None:
        """Append an event in the canonical timing-event schema.

        Schema (one contract, used by HTTP /api/timing/events, WS /ws/timing,
        and the Live Timing UI event renderer):

            {seq, ts, type, car_id, driver, track, lap_ms, payload}

        ``car_id`` / ``driver`` / ``lap_ms`` are first-class top-level fields so
        the UI can render leaderboards and lap rows without digging into
        ``payload``. Everything else (sub-types, AC-specific fields, etc.)
        lives under ``payload``.
        """
        self._event_seq += 1
        evt = {
            "seq": self._event_seq,
            "ts": time.time(),
            "type": type,
            "car_id": car_id,
            "driver": driver if driver is not None else "",
            "track": self.session.track_name or None,
            "lap_ms": lap_ms,
            "payload": payload,
        }
        self.events.append(evt)


class _TimingProtocol(asyncio.DatagramProtocol):
    def __init__(self, engine: TimingEngine) -> None:
        self.engine = engine

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: D401
        self.engine.handle_datagram(data)

    def error_received(self, exc: Exception) -> None:
        LOG.warning("Timing engine UDP error: %s", exc)


# ---- module singleton ----
_ENGINE: Optional[TimingEngine] = None


def get_engine() -> TimingEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TimingEngine()
    return _ENGINE


async def start() -> None:
    await get_engine().start()


async def stop() -> None:
    if _ENGINE is not None:
        await _ENGINE.stop()
