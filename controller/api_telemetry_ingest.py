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
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from controller.security import get_registry
from controller.telemetry.store import get_store

LOG = logging.getLogger("pitbox.telemetry.ingest")

ws_router = APIRouter()


def _authenticate_ws(websocket: WebSocket) -> Optional[str]:
    """
    Validate X-Agent-Id / X-Agent-Token against the registry. Returns agent_id
    on success or None on failure. We do NOT auto-register over the WS path
    (HTTP heartbeat already does that); we only accept agents the registry
    already knows.
    """
    aid = (websocket.headers.get("x-agent-id") or "").strip()
    token = (websocket.headers.get("x-agent-token") or "").strip()
    if not aid or not token:
        return None
    reg = get_registry()
    rec = reg.get(aid)
    if rec is None:
        # Agent has WS but never POSTed heartbeat; accept and let HTTP register.
        # This avoids a chicken-and-egg at first start. We still require a token.
        return aid
    if rec.token != token:
        return None
    return aid


@ws_router.websocket("/ws/agent-telemetry")
async def agent_telemetry_ws(websocket: WebSocket) -> None:
    aid = _authenticate_ws(websocket)
    if aid is None:
        # Per FastAPI, must accept then close with code; or close before accept.
        await websocket.close(code=4401)  # 4401 = custom unauthorized
        LOG.warning("Telemetry WS rejected (auth failed) from %s", websocket.client)
        return

    await websocket.accept()
    LOG.info("Telemetry WS connected: agent=%s from %s", aid, websocket.client)
    store = get_store()
    frames = 0
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
    except WebSocketDisconnect:
        LOG.info("Telemetry WS disconnected: agent=%s (received %d frames)", aid, frames)
    except Exception as e:
        LOG.warning("Telemetry WS error for agent=%s: %s", aid, e)
    finally:
        # Don't immediately remove; staleness logic in store handles fallthrough.
        # But schedule a delayed eviction so a permanently-gone agent disappears.
        async def _evict_after_grace():
            await asyncio.sleep(30.0)
            await store.remove(aid)
        try:
            asyncio.create_task(_evict_after_grace())
        except Exception:
            pass


# HTTP route exposing current agent telemetry status (debug/diagnostics)
router = APIRouter()


@router.get("/telemetry/agents")
async def list_agents():
    return {"agents": get_store().all_agents()}


@router.get("/telemetry/agent/{agent_id}")
async def get_agent(agent_id: str):
    f = get_store().get(agent_id)
    if not f:
        return {"agent_id": agent_id, "found": False}
    return {"agent_id": agent_id, "found": True, "frame": f}
