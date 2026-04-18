"""
WebSocket ingest for per-agent AC shared-memory telemetry (Phase B).

Endpoint: WS /ws/agent-telemetry
Auth   : X-Agent-Id + X-Agent-Token request headers (validated against
         AgentRegistry the same way HTTP routes do via security.require_agent).

Each agent maintains exactly one connection. Frames are JSON dicts shaped by
agent/telemetry/sender.py:
    {
      "agent_id": "<id>",
      "ts": 1712345678.9,
      "available": bool,
      "physics": {...},   # optional
      "graphics": {...},  # optional
      "static": {...},    # optional
    }

We tolerate missing blocks, ignore frames without agent_id, and store the
latest frame in the per-agent store. Connection close removes the live frame
after a short delay so a flicker reconnect doesn't blank the UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from controller.security import get_registry
from controller.telemetry.store import get_store

LOG = logging.getLogger("pitbox.telemetry.ingest")

ws_router = APIRouter()

# Rolling buffer of recent WS auth rejects so /api/telemetry/debug can show
# token-mismatch / missing-header failures without operators tailing the log.
# Bounded size to keep memory trivial.
_RECENT_REJECTS_MAX = 32
_recent_rejects: list = []


def _record_reject(agent_id: Optional[str], client: str, reason: str) -> None:
    _recent_rejects.append({
        "ts": time.time(),
        "agent_id": agent_id or "",
        "client": client,
        "reason": reason,
    })
    if len(_recent_rejects) > _RECENT_REJECTS_MAX:
        del _recent_rejects[: len(_recent_rejects) - _RECENT_REJECTS_MAX]


# Monotonic generation counter for connections. Used to make the delayed
# eviction safe when an agent reconnects quickly: only the *latest* connection
# for a given agent is allowed to evict.
_conn_gen: dict = {}


def _authenticate_ws(websocket: WebSocket) -> Tuple[Optional[str], str]:
    """
    Validate X-Agent-Id / X-Agent-Token against the registry. Returns
    (agent_id, reason). agent_id is None on failure; reason is a short
    human string suitable for logging at WARNING level on rejects.
    """
    aid = (websocket.headers.get("x-agent-id") or "").strip()
    token = (websocket.headers.get("x-agent-token") or "").strip()
    if not aid:
        return None, "missing X-Agent-Id header"
    if not token:
        return None, "missing X-Agent-Token header"
    reg = get_registry()
    rec = reg.get(aid)
    if rec is None:
        # Agent has WS but never POSTed heartbeat; accept and let HTTP register.
        # This avoids a chicken-and-egg at first start. We still require a token.
        return aid, "accepted (registry unknown — first contact)"
    if rec.token != token:
        return None, "token mismatch (re-enroll the agent)"
    return aid, "accepted"


@ws_router.websocket("/ws/agent-telemetry")
async def agent_telemetry_ws(websocket: WebSocket) -> None:
    aid, reason = _authenticate_ws(websocket)
    client_str = f"{websocket.client}"
    if aid is None:
        # Per FastAPI, must accept then close with code; or close before accept.
        # Capture the rejected agent_id (if any) so the operator can see it
        # in /api/telemetry/debug.
        rejected_id = (websocket.headers.get("x-agent-id") or "").strip() or None
        _record_reject(rejected_id, client_str, reason)
        await websocket.close(code=4401)  # 4401 = custom unauthorized
        LOG.warning("Telemetry WS REJECTED from %s id=%r: %s",
                    client_str, rejected_id or "", reason)
        return

    await websocket.accept()
    # Bump generation so any pending eviction from a previous connection
    # for this agent_id becomes a no-op.
    gen = _conn_gen.get(aid, 0) + 1
    _conn_gen[aid] = gen
    LOG.info("Telemetry WS ACCEPTED: agent=%s gen=%d from %s (%s)",
             aid, gen, client_str, reason)
    store = get_store()
    frames = 0
    last_summary = time.monotonic()
    summary_frames = 0
    summary_avail = 0
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                LOG.debug("Bad JSON frame from %s", aid)
                continue
            if not isinstance(frame, dict):
                continue
            # Trust the header agent_id over the body to prevent spoofing
            frame["agent_id"] = aid
            await store.update(aid, frame)
            frames += 1
            summary_frames += 1
            if frame.get("available"):
                summary_avail += 1
            # Periodic INFO so operators can see the pipe is alive without DEBUG
            now = time.monotonic()
            if now - last_summary >= 10.0:
                LOG.info(
                    "Telemetry rx: agent=%s frames=%d (avail=%d) in last %.1fs (total=%d)",
                    aid, summary_frames, summary_avail, now - last_summary, frames,
                )
                last_summary = now
                summary_frames = 0
                summary_avail = 0
    except WebSocketDisconnect:
        LOG.info("Telemetry WS DISCONNECTED: agent=%s (received %d frames total)", aid, frames)
    except Exception as e:
        LOG.warning("Telemetry WS error for agent=%s: %s: %s", aid, type(e).__name__, e)
    finally:
        # Don't immediately remove; staleness logic in store handles fallthrough.
        # Schedule a delayed eviction so a permanently-gone agent disappears,
        # but bind it to *this* connection generation: if the agent reconnects
        # within the grace window, the newer connection bumps the gen counter
        # and this eviction becomes a no-op (avoids the reconnect-eviction race).
        my_gen = gen

        async def _evict_after_grace():
            try:
                await asyncio.sleep(30.0)
                if _conn_gen.get(aid) == my_gen:
                    await store.remove(aid)
                    LOG.info("Telemetry: evicted agent=%s after 30s grace (gen=%d)", aid, my_gen)
                else:
                    LOG.debug("Telemetry: skipping eviction for %s (newer gen %s active)",
                              aid, _conn_gen.get(aid))
            except Exception:
                pass

        try:
            asyncio.create_task(_evict_after_grace())
        except Exception:
            pass


# HTTP routes for diagnostics
router = APIRouter()


@router.get("/telemetry/agents")
async def list_agents():
    """List every agent the telemetry store has heard from since startup."""
    return {"agents": get_store().all_agents()}


@router.get("/telemetry/agent/{agent_id}")
async def get_agent(agent_id: str):
    """Return the most recent raw frame for a single agent (full payload)."""
    f = get_store().get(agent_id)
    if not f:
        return {"agent_id": agent_id, "found": False}
    return {"agent_id": agent_id, "found": True, "frame": f}


@router.get("/telemetry/debug")
async def telemetry_debug():
    """One-stop diagnostic for the telemetry pipeline.

    Returns:
      - registry: all enrolled rigs (agent_id, display_name, host, last_seen)
      - store:    all agents the store has ever heard from (status, ages,
                  player_nick, car_model, track)
      - merge:    for each store agent, which leaderboard driver (if any)
                  it merged into the latest snapshot, plus the join key
      - snapshot: current `drivers` (id/name/connected) and `telemetry_agents`
                  keys from the engine snapshot
      - hints:    short text suggestions for the operator based on observed state
    """
    out: dict = {"now": time.time()}
    # Registry side
    try:
        from controller.enrolled_rigs import get_all_ordered as _rigs
        rigs = []
        for r in (_rigs() or []):
            rigs.append({
                "agent_id": r.get("agent_id"),
                "display_name": r.get("display_name"),
                "host": r.get("host"),
                "port": r.get("port"),
                "hostname": r.get("hostname"),
            })
        out["registry"] = rigs
    except Exception as e:
        out["registry"] = {"error": f"{type(e).__name__}: {e}"}

    # Store side
    store = get_store()
    out["store"] = store.all_agents()

    # Recent WS auth rejects (token mismatch, missing headers, etc.) — exposed
    # so operators can spot a misconfigured agent without tailing controller logs.
    now = time.time()
    out["recent_rejects"] = [
        {**r, "age_sec": round(now - r["ts"], 1)} for r in _recent_rejects[-20:]
    ]

    # Snapshot side (drivers + telemetry_agents that engine.snapshot() exposes)
    snap_drivers: list = []
    snap_tel_keys: list = []
    try:
        from controller.timing.engine import get_engine
        snap = get_engine().snapshot()
        if snap:
            for d in (snap.get("drivers") or []):
                snap_drivers.append({
                    "car_id": d.get("car_id"),
                    "driver_name": d.get("driver_name"),
                    "connected": d.get("connected"),
                    "has_live_telemetry": bool(d.get("live_telemetry")),
                })
            snap_tel_keys = sorted(list((snap.get("telemetry_agents") or {}).keys()))
    except Exception as e:
        out["snapshot_error"] = f"{type(e).__name__}: {e}"
    out["snapshot"] = {"drivers": snap_drivers, "telemetry_agents": snap_tel_keys}

    # Merge picture: for each agent in the store, who did it match (if anyone)?
    merge: list = []
    name_to_driver = {}
    for d in snap_drivers:
        nm = (d.get("driver_name") or "").strip().lower()
        if nm:
            name_to_driver.setdefault(nm, d)
    for a in out["store"]:
        nick = (a.get("driver_nick") or "").strip().lower()
        matched = name_to_driver.get(nick) if nick else None
        if not matched and nick and " " in nick:
            for tok in (nick.split()[-1], nick.split()[0]):
                matched = name_to_driver.get(tok)
                if matched:
                    break
        merge.append({
            "agent_id": a.get("agent_id"),
            "status": a.get("status"),
            "age_sec": a.get("age_sec"),
            "available": a.get("available"),
            "player_nick": a.get("driver_nick"),
            "matched_car_id": matched.get("car_id") if matched else None,
            "matched_driver_name": matched.get("driver_name") if matched else None,
        })
    out["merge"] = merge

    # Operator hints
    hints: list = []
    if out["recent_rejects"]:
        last = out["recent_rejects"][-1]
        hints.append(
            f"Recent WS auth REJECT: agent={last['agent_id']!r} "
            f"reason={last['reason']!r} ({last['age_sec']}s ago). "
            "Token mismatch usually means the rig was re-enrolled — re-pair the agent.")
    if not out["store"]:
        hints.append("No agents have ever connected to /ws/agent-telemetry. "
                     "Check on each rig: agent service running with v1.5.10+ binary, "
                     "and `telemetry_enabled: true` in agent_config.json.")
    else:
        live = [a for a in out["store"] if a.get("status") == "live"]
        offline = [a for a in out["store"] if a.get("status") == "offline"]
        if not live:
            hints.append("Agents are known but none are LIVE — they may have "
                         "disconnected. Check the rig agent log for 'Telemetry WS' lines.")
        for a in out["store"]:
            if a.get("status") == "live" and not a.get("available"):
                hints.append(
                    f"Agent {a['agent_id']} is connected but AC shared memory is "
                    f"UNAVAILABLE — AC isn't on track on that rig (or mmap names differ).")
        if offline:
            hints.append(f"{len(offline)} agent(s) marked offline (>15s since last frame).")
    if out["store"] and snap_drivers:
        unmatched = [m for m in merge if m.get("available") and not m.get("matched_car_id")]
        if unmatched:
            details = ", ".join(
                f"{m['agent_id']}(nick={m['player_nick']!r})" for m in unmatched
            )
            hints.append(
                "Live telemetry is arriving but no leaderboard row matches the AC nickname. "
                "Either rename the driver in the AC server entry list to match the AC profile "
                "nickname, or set the AC profile nickname to match the entry-list name. "
                f"Unmatched: {details}")
    out["hints"] = hints
    return out
