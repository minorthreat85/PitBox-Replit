"""
Per-agent telemetry store.

Holds the latest frame received from each PitBox Agent's shared-memory sender
along with per-agent staleness tracking. Thread/async-safe via a single asyncio
lock on writes; reads return shallow copies of the cached state so callers can
serialize without holding the lock.

Design notes:
- One frame per agent (latest wins; no history). Agents stream at ~15 Hz so
  retaining history would balloon memory with no UI need (events come from the
  separate timing engine).
- Staleness: frame older than STALE_AFTER_SEC marks the agent stale; older than
  OFFLINE_AFTER_SEC marks it offline. Both are returned in the agent record so
  the UI can color-code without doing time math.
- Driver mapping (agent_id -> driver_guid) is resolved by the consumer (the
  timing engine merger) using `enrolled_rigs` and the agent-supplied
  `static.player_*` fields. The store itself stays dumb on purpose.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("pitbox.telemetry.store")

STALE_AFTER_SEC = 3.0
OFFLINE_AFTER_SEC = 15.0

# Tolerate a tiny bit of float slop on either end; AC occasionally reports
# values like 1.0000003 right at the start/finish line.
_NORM_POS_EPS = 1e-3


def _sanitize_norm_pos(raw: Any) -> Optional[float]:
    """Return a clean 0..1 float, or None if the value is unusable.

    AC's shared memory occasionally hands us garbage for
    `normalized_car_position` — uninitialized memory before the first lap,
    NaN/inf during session transitions, or wildly out-of-range values like
    3.99e+28 when a car is in the pit before the lap counter resets. The UI
    must NEVER display those — they show up as nonsense percentages and
    place dots at random points along the track path. We log the *first*
    bad value per agent (rate-limited via _bad_norm_pos_logged) and pass
    None to the UI thereafter.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if v < -_NORM_POS_EPS or v > 1.0 + _NORM_POS_EPS:
        return None
    # Clamp tiny float slop into the canonical [0,1] range so downstream
    # `getPointAtLength(t * len)` never wraps unexpectedly.
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# Per-agent latch so we only log the first bad norm_pos we see — protects
# against ~15 Hz log spam if AC keeps sending nonsense for a whole session.
_bad_norm_pos_logged: set[str] = set()


class TelemetryStore:
    def __init__(self) -> None:
        self._frames: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def update(self, agent_id: str, frame: Dict[str, Any]) -> None:
        """Replace the latest frame for `agent_id`. `frame` should already include `ts`."""
        if not agent_id:
            return
        if "ts" not in frame:
            frame["ts"] = time.time()
        async with self._lock:
            self._frames[agent_id] = frame

    async def remove(self, agent_id: str) -> None:
        async with self._lock:
            self._frames.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Non-async snapshot read for hot paths (engine merge). Returns shallow copy."""
        f = self._frames.get(agent_id)
        return dict(f) if f else None

    def all_agents(self) -> List[Dict[str, Any]]:
        """
        List of {agent_id, ts, age_sec, status, available} for every agent we've heard
        from since process start. status in {"live","stale","offline"}.
        """
        now = time.time()
        out: List[Dict[str, Any]] = []
        for aid, f in list(self._frames.items()):
            ts = float(f.get("ts") or 0.0)
            age = max(0.0, now - ts)
            if age <= STALE_AFTER_SEC:
                status = "live"
            elif age <= OFFLINE_AFTER_SEC:
                status = "stale"
            else:
                status = "offline"
            out.append({
                "agent_id": aid,
                "ts": ts,
                "age_sec": round(age, 2),
                "status": status,
                "available": bool(f.get("available")),
                "driver_nick": (f.get("static") or {}).get("player_nick") or "",
                "car_model": (f.get("static") or {}).get("car_model") or "",
                "track": (f.get("static") or {}).get("track") or "",
            })
        return out

    def project_for_engine(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {agent_id: live_telemetry_block} for engine merge. Only fields we
        want exposed in the unified snapshot. None values omitted.
        """
        now = time.time()
        out: Dict[str, Dict[str, Any]] = {}
        for aid, f in list(self._frames.items()):
            age = now - float(f.get("ts") or 0.0)
            if age > OFFLINE_AFTER_SEC:
                continue
            phys = f.get("physics") or {}
            grx = f.get("graphics") or {}
            stat = f.get("static") or {}
            raw_np = grx.get("normalized_car_position")
            np_clean = _sanitize_norm_pos(raw_np)
            if np_clean is None and raw_np is not None and aid not in _bad_norm_pos_logged:
                LOG.warning(
                    "telemetry: dropping invalid norm_pos for agent %s (raw=%r); "
                    "subsequent bad values from this agent will be silently dropped.",
                    aid, raw_np,
                )
                _bad_norm_pos_logged.add(aid)
            out[aid] = {
                "agent_id": aid,
                "stale": age > STALE_AFTER_SEC,
                "age_sec": round(age, 2),
                "speed_kmh": phys.get("speed_kmh"),
                "rpm": phys.get("rpms"),
                "gear": phys.get("gear"),
                "throttle": phys.get("gas"),
                "brake": phys.get("brake"),
                "fuel": phys.get("fuel"),
                "in_pit": grx.get("is_in_pit"),
                "current_sector": grx.get("current_sector_index"),
                "last_sector_ms": grx.get("last_sector_time_ms"),
                "current_lap_ms": grx.get("i_current_time_ms"),
                "last_lap_ms": grx.get("i_last_time_ms"),
                "best_lap_ms": grx.get("i_best_time_ms"),
                "completed_laps": grx.get("completed_laps"),
                "norm_pos": np_clean,
                "coord": [grx.get("coord_x"), grx.get("coord_y"), grx.get("coord_z")],
                "tyre_compound": grx.get("tyre_compound"),
                "session_status": grx.get("status_name"),
                "session_name": grx.get("session_name"),
                "player_nick": stat.get("player_nick"),
                "car_model": stat.get("car_model"),
                "track": stat.get("track"),
            }
        return out


# Module-level singleton
_store: Optional[TelemetryStore] = None


def get_store() -> TelemetryStore:
    global _store
    if _store is None:
        _store = TelemetryStore()
    return _store
