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

# ---- Phase 4: startup + stale-state resync constants ----
# How long after engine.start() before we start nudging the AC server. Gives
# the server a chance to send its initial NEW_SESSION/CAR_INFO packets on its
# own (typical: <1 s on Win, sometimes 5-10 s on cold start).
_RESYNC_STARTUP_GRACE_S = 10.0
# How long without ANY packet before we treat the feed as stale and try to
# nudge the server back to life.
_RESYNC_STALE_AFTER_S = 30.0
# Polling cadence for the resync supervisor itself.
_RESYNC_LOOP_INTERVAL_S = 5.0
# Backoff bounds: first attempt waits INITIAL, then doubles up to MAX. Reset
# to INITIAL on first healthy packet after a stale period.
_RESYNC_INITIAL_BACKOFF_S = 5.0
_RESYNC_MAX_BACKOFF_S = 120.0

# ---- Phase 7: timing health thresholds (seconds since last AC packet) ----
# <= LIVE: feed is healthy; > LIVE and <= OFFLINE: stale (display warning, do
# not yet declare offline); > OFFLINE: feed is offline (red badge, banner).
_TIMING_HEALTH_LIVE_S = 5.0
_TIMING_HEALTH_OFFLINE_S = 30.0


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
    # Internal: raw delta-to-leader (ms) populated from AC LAP_COMPLETED.
    # NOT exposed to the UI directly — it is the input from which Phase 5
    # computes the authoritative gap_to_leader_ms / interval_to_ahead_ms in
    # snapshot(). Do not read this in the frontend.
    gap_ms: int = 0
    # Phase 5: authoritative gap/interval. Backend is the only source of these.
    # ``None`` means "not authoritative yet" — UI must render '—', never compute.
    gap_to_leader_ms: Optional[int] = None
    interval_to_ahead_ms: Optional[int] = None
    cuts_last_lap: int = 0
    loaded: bool = False


class TimingEngine:
    """In-memory model of a single AC server's timing state."""

    def __init__(self) -> None:
        self.session = SessionState()
        self.drivers: Dict[int, DriverState] = {}
        self.events: Deque[Dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
        self._event_seq = 0
        # Phase 6: monotonic snapshot ordering. Every call to snapshot()
        # increments this; clients drop frames with seq <= last seen so
        # an out-of-order WS tick / HTTP poll cannot rewind the UI.
        self._snapshot_seq = 0
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional["_TimingProtocol"] = None
        # NOTE: no async lock is needed. The engine runs entirely on a single
        # asyncio loop; UDP datagram_received and snapshot()/events_since() all
        # execute on that one thread, so mutations are inherently serialised.
        self.host = TIMING_UDP_PLUGIN_HOST
        self.port = TIMING_UDP_PLUGIN_PORT
        self.last_packet_unix: float = 0.0
        self.packets_received: int = 0
        self.unknown_packets: int = 0
        # Phase 4: resync supervisor state
        self._started_at_unix: float = 0.0
        self._resync_task: Optional[asyncio.Task] = None
        self._resync_next_attempt_unix: float = 0.0
        self._resync_backoff_s: float = _RESYNC_INITIAL_BACKOFF_S
        self._resync_attempts: int = 0
        self._resync_successes: int = 0
        self._last_resync_reason: str = ""
        self._last_seen_healthy: bool = False

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
        self._started_at_unix = time.time()
        LOG.info("Timing engine listening on udp://%s:%s", self.host, self.port)
        # Kick off the resync supervisor (Phase 4). Best-effort: any failure
        # in the supervisor is logged but never tears down the listener.
        try:
            self._resync_task = asyncio.create_task(
                self._resync_loop(), name="pitbox-timing-resync"
            )
        except Exception:
            LOG.exception("Failed to start timing resync supervisor")

    async def stop(self) -> None:
        if self._resync_task is not None:
            self._resync_task.cancel()
            try:
                await self._resync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._resync_task = None
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

        # ---- Phase 5: backend is the authoritative source of gap/interval ----
        # Rules (frontend MUST display these as-is, never derive):
        #   * Driver has no completed laps -> gap_to_leader_ms = interval_to_ahead_ms = None
        #   * Leader (first driver with completed laps): gap=0, interval=None
        #   * Subsequent drivers with laps: gap = max(0, their_gap_ms);
        #     interval = max(0, my_gap - prev_gap) (clamped non-negative).
        #   * Drivers with no completed laps appear at the bottom with both = None.
        _prev_gap: Optional[int] = None
        _leader_seen = False
        for d in driver_dicts:
            laps = int(d.get("total_laps") or 0)
            raw_gap = int(d.get("gap_ms") or 0)
            if laps <= 0:
                d["gap_to_leader_ms"] = None
                d["interval_to_ahead_ms"] = None
                continue
            if not _leader_seen:
                d["gap_to_leader_ms"] = 0
                d["interval_to_ahead_ms"] = None
                _leader_seen = True
                _prev_gap = 0
            else:
                gap = max(0, raw_gap)
                interval = max(0, gap - (_prev_gap if _prev_gap is not None else 0))
                d["gap_to_leader_ms"] = gap
                d["interval_to_ahead_ms"] = interval
                _prev_gap = gap

        # ---- Phase C: merge per-agent live telemetry from sim PCs ----
        # Best-effort: keyed by case-insensitive driver name. Telemetry blocks
        # are also returned at top level under `telemetry_agents` so the UI can
        # render unmatched agents (e.g. spectator sims, mismatched names).
        telemetry_agents: Dict[str, Any] = {}
        try:
            from controller.telemetry.store import get_store as _get_tel_store
            agents_by_id = _get_tel_store().project_for_engine()
            telemetry_agents = agents_by_id
            if agents_by_id:
                # Build lookup: nick -> agent_id (lower)
                nick_to_aid: Dict[str, str] = {}
                for aid, block in agents_by_id.items():
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
                        d["live_telemetry"] = agents_by_id[aid]
        except Exception:
            LOG.debug("telemetry merge skipped", exc_info=True)

        # ---- Phase 7: explicit health model ----
        # Backend is the only source of truth for "is the timing feed live?".
        # The frontend must NOT do its own age math here — it just reads
        # health.timing.state and renders a badge.
        now = time.time()
        last_pkt = self.last_packet_unix
        if last_pkt <= 0.0:
            timing_state = "offline"
            timing_age: Optional[float] = None
        else:
            timing_age = max(0.0, now - last_pkt)
            if timing_age <= _TIMING_HEALTH_LIVE_S:
                timing_state = "live"
            elif timing_age <= _TIMING_HEALTH_OFFLINE_S:
                timing_state = "stale"
            else:
                timing_state = "offline"
        health = {
            "timing": {
                "state": timing_state,
                "last_packet_unix": last_pkt,
                "last_packet_age_s": round(timing_age, 2) if timing_age is not None else None,
                "stale_after_s": _TIMING_HEALTH_LIVE_S,
                "offline_after_s": _TIMING_HEALTH_OFFLINE_S,
            },
            "transport": {
                "ws_supported": True,
            },
        }

        # Per-driver freshness derived from connected flag + global timing state
        # + per-agent telemetry status. Operators see a single coherent picture
        # instead of having to reconcile transport-vs-feed-vs-agent themselves.
        for d in driver_dicts:
            connected = bool(d.get("connected"))
            if not connected:
                drv_timing = "offline"
            else:
                drv_timing = timing_state  # follows global feed health
            drv_tel = d.get("live_telemetry") or None
            if drv_tel is None:
                tel_state = "missing"
            elif drv_tel.get("stale"):
                tel_state = "stale"
            else:
                tel_state = "live"
            d["freshness"] = {
                "timing_state": drv_timing,
                "telemetry_state": tel_state,
            }

        self._snapshot_seq += 1
        return {
            "snapshot_seq": self._snapshot_seq,
            "generated_unix": now,
            "health": health,
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
                "resync": {
                    "attempts": self._resync_attempts,
                    "successes": self._resync_successes,
                    "next_attempt_unix": self._resync_next_attempt_unix,
                    "backoff_s": self._resync_backoff_s,
                    "last_reason": self._last_resync_reason,
                },
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

    # ---- Phase 4: startup + stale-state resync ----
    def _resync_diagnose(self, now: float) -> Optional[str]:
        """Decide whether the engine needs to nudge AC server for state.

        Returns a short human-readable reason string when a resync is
        warranted, or None when the feed is healthy enough to leave alone.

        Trigger conditions (in priority order):
          1. ``cold_start``  — never received a packet AND startup grace
             elapsed (engine just came up, server may have started before us
             so we missed NEW_SESSION / NEW_CONNECTION packets).
          2. ``stale_feed``  — received packets at some point but nothing in
             the last ``_RESYNC_STALE_AFTER_S`` seconds (transient AC server
             stall, network blip, etc.).
        """
        if self._started_at_unix == 0.0:
            return None  # not started yet
        if self.last_packet_unix == 0.0:
            if (now - self._started_at_unix) >= _RESYNC_STARTUP_GRACE_S:
                return "cold_start"
            return None
        # We've seen packets at some point.
        if (now - self.last_packet_unix) >= _RESYNC_STALE_AFTER_S:
            return "stale_feed"
        return None

    async def _resync_loop(self) -> None:
        """Bounded-backoff supervisor that nudges AC for SESSION_INFO/CAR_INFO.

        Runs as a background task while the listener is up. On each tick:
          * Healthy feed -> reset backoff to initial, no action.
          * Cold start / stale feed -> if backoff timer elapsed, send
            ``request_session_info`` and (for each known car) ``request_car_info``
            to every running AC server, then double the backoff up to
            ``_RESYNC_MAX_BACKOFF_S``.

        Each iteration is wrapped in its own try/except so an unexpected
        failure (one bad probe, a transient import error) cannot permanently
        disable the supervisor — only an explicit cancellation stops it.
        """
        while True:
            try:
                await asyncio.sleep(_RESYNC_LOOP_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            try:
                now = time.time()
                reason = self._resync_diagnose(now)

                if reason is None:
                    if not self._last_seen_healthy:
                        LOG.info("Timing feed healthy; resync backoff reset.")
                    self._last_seen_healthy = True
                    self._resync_backoff_s = _RESYNC_INITIAL_BACKOFF_S
                    self._resync_next_attempt_unix = 0.0
                    self._last_resync_reason = ""
                    continue

                self._last_seen_healthy = False
                self._last_resync_reason = reason

                if now < self._resync_next_attempt_unix:
                    continue  # waiting out the current backoff window

                ok = await self._fire_resync_probes(reason)
                self._resync_attempts += 1
                if ok:
                    self._resync_successes += 1
                self._resync_next_attempt_unix = now + self._resync_backoff_s
                self._resync_backoff_s = min(
                    self._resync_backoff_s * 2.0, _RESYNC_MAX_BACKOFF_S
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Per-iteration backstop: log and keep the supervisor alive.
                LOG.exception(
                    "Timing resync iteration failed; supervisor continues running"
                )

    async def _fire_resync_probes(self, reason: str) -> bool:
        """Send SESSION_INFO + per-car CAR_INFO to every running AC server.

        Returns True if at least one probe was dispatched. Lazy-imports to
        avoid a circular dependency between timing.engine and the API layer.
        """
        # Lazy imports to avoid circular dependency at module load time.
        try:
            from controller.api_server_config_routes import _get_running_servers_list
            from controller.server_control import get_adapter
        except Exception:
            LOG.debug("resync probe skipped: server-control adapter unavailable", exc_info=True)
            return False

        try:
            running = _get_running_servers_list() or []
        except Exception:
            LOG.exception("resync probe: could not list running AC servers")
            return False
        if not running:
            LOG.info(
                "Timing resync (%s): no running AC servers; will retry after %.0fs backoff",
                reason, self._resync_backoff_s,
            )
            return False

        adapter = get_adapter()
        any_ok = False
        for entry in running:
            sid = entry.get("server_id") if isinstance(entry, dict) else None
            if not sid:
                continue
            try:
                adapter.request_session_info(sid)
                any_ok = True
                LOG.info(
                    "Timing resync (%s): sent GET_SESSION_INFO to server '%s' (backoff=%.0fs)",
                    reason, sid, self._resync_backoff_s,
                )
            except Exception as exc:
                # On send failure, force a re-read of server_cfg.ini next time
                # in case the cached UDP_PLUGIN_LOCAL_PORT is now wrong.
                try:
                    adapter.invalidate_target(sid)
                except Exception:
                    pass
                LOG.warning(
                    "Timing resync (%s): GET_SESSION_INFO to '%s' failed: %s",
                    reason, sid, exc,
                )
                continue
            # Also re-request known cars so we don't have ghost driver rows
            # after a reconnect storm or mid-session start.
            for car_id in list(self.drivers.keys()):
                try:
                    adapter.request_car_info(sid, int(car_id))
                except Exception as exc:
                    LOG.debug(
                        "Timing resync: GET_CAR_INFO car_id=%s on '%s' failed: %s",
                        car_id, sid, exc,
                    )
        return any_ok

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


async def start(host: Optional[str] = None, port: Optional[int] = None) -> None:
    await get_engine().start(host=host, port=port)


async def stop() -> None:
    if _ENGINE is not None:
        await _ENGINE.stop()
