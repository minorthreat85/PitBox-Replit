"""HTTP + WebSocket routes for the native PitBox timing engine.

Two routers are exported:

- ``router`` is mounted under ``/api`` (matching the rest of the controller's
  HTTP API) and exposes ``/timing/health``, ``/timing/snapshot``,
  ``/timing/events`` and ``/timing/session``.
- ``ws_router`` is mounted at the application root and exposes
  ``WS /ws/timing`` for push updates. Operators can choose to consume the WS
  feed or fall back to plain polling against the HTTP routes.

All HTTP endpoints are gated by ``require_operator_if_password_configured`` so
behaviour matches the rest of the API: open on the LAN by default, gated when
``employee_password`` is configured.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from controller.operator_auth import (
    is_ws_authorized_for_operator,
    require_operator_if_password_configured,
)
from controller.timing.engine import get_engine


LOG = logging.getLogger("pitbox.timing.routes")

router = APIRouter(prefix="/api")
ws_router = APIRouter()


def _health_payload() -> Dict[str, Any]:
    eng = get_engine()
    snap_stats = eng.snapshot()["stats"]
    last = snap_stats.get("last_packet_unix") or 0.0
    age = (time.time() - last) if last else None
    fresh = bool(last) and age is not None and age < 10.0
    return {
        "running": snap_stats["running"],
        "host": snap_stats["host"],
        "port": snap_stats["port"],
        "packets_received": snap_stats["packets_received"],
        "unknown_packets": snap_stats["unknown_packets"],
        "last_packet_unix": last,
        "last_packet_age_seconds": age,
        "fresh": fresh,
        "event_seq": snap_stats["event_seq"],
        "engine": "native-acudpclient",
    }


@router.get("/timing/health")
async def timing_health(_: None = Depends(require_operator_if_password_configured)):
    """Engine status: bind state, last-packet age, freshness, counters."""
    return _health_payload()


@router.get("/timing/snapshot")
async def timing_snapshot(_: None = Depends(require_operator_if_password_configured)):
    """Full leaderboard snapshot: session header + sorted drivers + stats."""
    return get_engine().snapshot()


@router.get("/timing/session")
async def timing_session(_: None = Depends(require_operator_if_password_configured)):
    """Current session header only (track, layout, type, time, temps, …)."""
    return get_engine().snapshot()["session"]


@router.get("/timing/events")
async def timing_events(
    since: int = Query(0, ge=0, description="Return events with seq > since"),
    limit: int = Query(100, ge=1, le=200),
    _: None = Depends(require_operator_if_password_configured),
):
    """Recent timing events newer than ``since`` (sequence number).

    Each event uses the canonical timing-event schema:
    ``{seq, ts, type, car_id, driver, track, lap_ms, payload}``.
    Use ``next_seq`` from the response as the next ``since`` value.
    """
    return get_engine().events_since(since, limit=limit)


# --- WebSocket ---------------------------------------------------------------

# WebSockets are pushed at this rate (snapshot + new events). 2 Hz keeps the
# wire light while still feeling live for a leaderboard.
_WS_PUSH_INTERVAL_S = 0.5


@ws_router.websocket("/ws/timing")
async def ws_timing(ws: WebSocket) -> None:
    """Push snapshots + incremental events while the connection is open.

    Phase 10: the WS handshake is gated by the SAME effective policy as the
    timing HTTP routes (``require_operator_if_password_configured``):

    - if ``employee_password`` is unset  -> open to all LAN clients
    - if ``employee_password`` is set    -> requires ``pitbox_employee=1`` cookie

    Decision is made via the shared helper ``is_ws_authorized_for_operator``
    so HTTP and WS can never drift out of policy.
    """
    if not is_ws_authorized_for_operator(ws):
        # Reject BEFORE accept so Starlette returns HTTP 403 during the
        # handshake — the client never enters the WebSocket protocol state.
        client = ws.client.host if ws.client else "?"
        LOG.warning("Unauthorized timing WS attempt from %s: missing/invalid operator cookie", client)
        await ws.close(code=1008)  # 1008 = policy violation
        return
    await ws.accept()
    eng = get_engine()
    last_seq = 0
    try:
        # Initial frame: full snapshot so the client can paint immediately.
        snap = eng.snapshot()
        last_seq = snap["stats"].get("event_seq", 0)
        await ws.send_json({"type": "snapshot", "data": snap})

        while True:
            await asyncio.sleep(_WS_PUSH_INTERVAL_S)
            snap = eng.snapshot()
            ev = eng.events_since(last_seq, limit=200)
            last_seq = ev.get("next_seq", last_seq)
            await ws.send_json({
                "type": "tick",
                "snapshot": snap,
                "events": ev["events"],
                "next_seq": last_seq,
            })
    except WebSocketDisconnect:
        return
    except Exception:
        LOG.exception("Timing WebSocket failed")
        try:
            await ws.close()
        except Exception:
            pass
