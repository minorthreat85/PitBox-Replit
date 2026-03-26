"""
In-memory store for agent telemetry_tick and optional agent_status.
Enforces ordering (seq, ts_ms); builds timing_snapshot for GET /api/timing/snapshot.
"""
import logging
import threading
import time
from typing import Any, Optional

from controller.telemetry_models import (
    TelemetryTickBody,
    AgentStatusBody,
    TimingSnapshotBody,
    ServerInfo,
    TrackSnapshot,
    CarSnapshot,
    LiveInfo,
)

logger = logging.getLogger(__name__)

# Last telemetry_tick per agent_id (canonical from X-Agent-Id)
_last_telemetry: dict[str, TelemetryTickBody] = {}
_last_status: dict[str, AgentStatusBody] = {}
_store_lock = threading.Lock()


def ingest_telemetry(agent_id: str, msg: TelemetryTickBody) -> bool:
    """
    Store telemetry_tick if not stale/out-of-order.
    Returns True if stored, False if ignored (seq <= last.seq and ts_ms <= last.ts_ms).
    """
    with _store_lock:
        prev = _last_telemetry.get(agent_id)
        if prev is not None:
            if msg.seq <= prev.seq and msg.ts_ms <= prev.ts_ms:
                return False
        _last_telemetry[agent_id] = msg
        return True


def ingest_status(agent_id: str, msg: AgentStatusBody) -> None:
    """Store last agent_status per agent (for server/track context)."""
    with _store_lock:
        _last_status[agent_id] = msg


def get_last_telemetry(agent_id: str) -> Optional[TelemetryTickBody]:
    """Return last telemetry_tick for agent or None."""
    with _store_lock:
        return _last_telemetry.get(agent_id)


def get_all_telemetry() -> dict[str, TelemetryTickBody]:
    """Return copy of last telemetry per agent."""
    with _store_lock:
        return dict(_last_telemetry)


def get_last_status(agent_id: str) -> Optional[AgentStatusBody]:
    """Return last agent_status for agent or None."""
    with _store_lock:
        return _last_status.get(agent_id)


def clear_all() -> None:
    """Clear telemetry and status store (for tests)."""
    with _store_lock:
        _last_telemetry.clear()
        _last_status.clear()


def build_timing_snapshot() -> TimingSnapshotBody:
    """
    Build fused timing_snapshot from last telemetry per agent.
    Order cars by: 1) lap desc, 2) normalized_pos desc.
    gap_ms: 0 for leader; others null or rough from normalized_pos * avg_lap_ms.
    live.stale_ms = now - ts_ms for each car.
    """
    now_ms = int(time.time() * 1000)
    with _store_lock:
        agents = list(_last_telemetry.keys())
        ticks = [_last_telemetry[a] for a in agents]
        statuses = {a: _last_status.get(a) for a in agents}

    if not ticks:
        return TimingSnapshotBody(
            type="timing_snapshot",
            v=1,
            ts_ms=now_ms,
            server=ServerInfo(),
            track=TrackSnapshot(),
            cars=[],
        )

    # Order: lap desc, then normalized_pos desc
    def order_key(t: TelemetryTickBody) -> tuple:
        return (-(t.timing.lap or 0), -(t.track.normalized_pos or 0.0))

    sorted_ticks = sorted(ticks, key=order_key)
    leader_last_lap: Optional[int] = None
    cars_out: list[CarSnapshot] = []
    for pos, tick in enumerate(sorted_ticks, start=1):
        agent_id = tick.agent_id
        stale_ms = now_ms - tick.ts_ms if tick.ts_ms else None
        status = statuses.get(agent_id)
        server_name = ""
        server_addr = ""
        if status and status.session:
            server_name = status.session.server_name or ""
            server_addr = status.session.server_addr or ""
        gap_ms: Optional[int] = None
        if pos == 1:
            gap_ms = 0
            leader_last_lap = tick.timing.last_lap_ms
        elif leader_last_lap is not None and leader_last_lap > 0 and tick.track.normalized_pos is not None:
            # Rough gap: (1 - normalized_pos) * leader_last_lap for same lap
            gap_ms = int((1.0 - tick.track.normalized_pos) * leader_last_lap) if tick.track.normalized_pos <= 1.0 else None

        cars_out.append(
            CarSnapshot(
                pos=pos,
                driver=tick.car.driver_name or "—",
                car_model=tick.car.car_model or "—",
                best_lap_ms=tick.timing.best_lap_ms,
                last_lap_ms=tick.timing.last_lap_ms,
                lap=tick.timing.lap or 0,
                sector=tick.timing.sector or 0,
                sector_time_ms=tick.timing.sector_time_ms,
                gap_ms=gap_ms,
                pit=tick.car_state.in_pit,
                live=LiveInfo(
                    normalized_pos=tick.track.normalized_pos or 0.0,
                    speed_kmh=tick.track.speed_kmh or 0.0,
                    source=f"agent:{agent_id}",
                    stale_ms=stale_ms,
                ),
            )
        )

    # Server/track from first tick (or first status)
    first_tick = sorted_ticks[0]
    first_agent = first_tick.agent_id
    first_status = statuses.get(first_agent)
    server = ServerInfo(
        name=first_status.session.server_name if first_status and first_status.session else "",
        addr=first_status.session.server_addr if first_status and first_status.session else "",
        phase="QUALIFY",
        time_left_ms=None,
    )
    track = TrackSnapshot(
        track_id=first_tick.track.track_id or "",
        layout=first_tick.track.layout or "",
    )

    return TimingSnapshotBody(
        type="timing_snapshot",
        v=1,
        ts_ms=now_ms,
        server=server,
        track=track,
        cars=cars_out,
    )
