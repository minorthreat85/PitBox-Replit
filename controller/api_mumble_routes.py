"""
Mumble admin API routes for PitBox controller.

Endpoints:
  GET  /api/mumble/status          — ping server + get channels & users
  GET  /api/mumble/channels        — list channels
  GET  /api/mumble/users           — list connected users
  POST /api/mumble/users/{session}/mute    — mute/unmute user
  POST /api/mumble/users/{session}/move    — move user to channel
  POST /api/mumble/users/{session}/kick    — kick user
  POST /api/mumble/channels/{channel_id}/mute — mute/unmute all in channel
  POST /api/mumble/message         — send text message to channel
  POST /api/mumble/agents/push-launch  — launch Mumble on all sim PCs
  POST /api/mumble/agents/push-close   — close Mumble on all sim PCs
  GET  /api/mumble/config          — get current Mumble connection config
  PUT  /api/mumble/config          — update Mumble connection config
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from controller.operator_auth import require_operator
from controller.mumble_client import MumbleClientError, get_mumble_client, reset_mumble_client
from controller.enrolled_rigs import get_all_ordered as enrolled_get_all_ordered
from controller.agent_poller import get_status_cache
from controller.api_routes import send_agent_command

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mumble", tags=["mumble"])


def _client():
    return get_mumble_client()


def _mumble_error(e: MumbleClientError, detail: str = "") -> HTTPException:
    msg = f"{detail}: {e}" if detail else str(e)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg)


# ── Status ─────────────────────────────────────────────────────────────────────

@router.get("/status")
async def mumble_status(_: None = Depends(require_operator)):
    """Ping Mumble server and return channels + users."""
    try:
        loop = asyncio.get_event_loop()
        client = _client()
        channels, users = await asyncio.gather(
            loop.run_in_executor(None, lambda: client.get_channels()),
            loop.run_in_executor(None, lambda: client.get_users()),
        )
        return {"connected": True, "channels": channels, "users": users}
    except MumbleClientError as e:
        return {"connected": False, "error": str(e), "channels": [], "users": []}
    except Exception as e:
        return {"connected": False, "error": str(e), "channels": [], "users": []}


# ── Channels ───────────────────────────────────────────────────────────────────

@router.get("/channels")
async def get_channels(_: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        channels = await loop.run_in_executor(None, lambda: _client().get_channels())
        return {"channels": channels}
    except MumbleClientError as e:
        raise _mumble_error(e, "GetChannels")


@router.get("/users")
async def get_users(_: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        users = await loop.run_in_executor(None, lambda: _client().get_users())
        return {"users": users}
    except MumbleClientError as e:
        raise _mumble_error(e, "GetUsers")


# ── User actions ───────────────────────────────────────────────────────────────

class MuteBody(BaseModel):
    mute: bool
    server_id: int = 1


class MoveBody(BaseModel):
    channel_id: int
    server_id: int = 1


class KickBody(BaseModel):
    reason: str = ""
    server_id: int = 1


@router.post("/users/{session}/mute")
async def mute_user(session: int, body: MuteBody, _: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _client().mute_user(session, body.mute, body.server_id)
        )
        return result
    except MumbleClientError as e:
        raise _mumble_error(e, "MuteUser")


@router.post("/users/{session}/move")
async def move_user(session: int, body: MoveBody, _: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _client().move_user(session, body.channel_id, body.server_id)
        )
        return result
    except MumbleClientError as e:
        raise _mumble_error(e, "MoveUser")


@router.post("/users/{session}/kick")
async def kick_user(session: int, body: KickBody, _: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _client().kick_user(session, body.reason, body.server_id)
        )
        return result
    except MumbleClientError as e:
        raise _mumble_error(e, "KickUser")


# ── Channel actions ────────────────────────────────────────────────────────────

@router.post("/channels/{channel_id}/mute")
async def mute_channel(channel_id: int, body: MuteBody, _: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None, lambda: _client().mute_channel(channel_id, body.mute, body.server_id)
        )
        return {"results": results}
    except MumbleClientError as e:
        raise _mumble_error(e, "MuteChannel")


# ── Text message ───────────────────────────────────────────────────────────────

class MessageBody(BaseModel):
    text: str
    channel_id: Optional[int] = None
    server_id: int = 1


@router.post("/message")
async def send_message(body: MessageBody, _: None = Depends(require_operator)):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _client().send_text_message(body.text, body.channel_id, body.server_id)
        )
        return result
    except MumbleClientError as e:
        raise _mumble_error(e, "SendMessage")


# ── Agent push: launch / close Mumble client on sim PCs ────────────────────────

async def _push_to_all_agents(command: str) -> dict:
    cache = get_status_cache()
    enrolled = enrolled_get_all_ordered()
    agent_ids: list[str] = []
    tasks = []
    for rig in enrolled:
        agent_id = (rig.get("agent_id") or "").strip()
        if not agent_id:
            continue
        if (rig.get("backend") or "agent").strip().lower() != "agent":
            continue
        s = cache.get(agent_id)
        if not (s and getattr(s, "online", False)):
            continue
        agent_ids.append(agent_id)
        tasks.append(send_agent_command(agent_id, command, {}, timeout=15.0))
    if not tasks:
        return {"ok": True, "results": [], "message": "No online agents found"}
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for aid, raw in zip(agent_ids, raw_results):
        if isinstance(raw, Exception):
            results.append({"agent_id": aid, "success": False, "message": str(raw)})
        else:
            results.append({"agent_id": aid, **raw})
    return {"ok": True, "results": results}


@router.post("/agents/push-launch")
async def push_launch_mumble(_: None = Depends(require_operator)):
    """Send launch-mumble command to every online enrolled agent."""
    return await _push_to_all_agents("launch-mumble")


@router.post("/agents/push-close")
async def push_close_mumble(_: None = Depends(require_operator)):
    """Send close-mumble command to every online enrolled agent."""
    return await _push_to_all_agents("close-mumble")


# ── Config ─────────────────────────────────────────────────────────────────────

class MumbleConfigBody(BaseModel):
    mumble_host: Optional[str] = None
    mumble_protocol: Optional[str] = None
    mumble_ice_port: Optional[int] = None
    mumble_secret: Optional[str] = None
    mumble_grpc_port: Optional[int] = None
    mumble_token: Optional[str] = None
    mumble_exe_path: Optional[str] = None


@router.get("/config")
async def get_mumble_config(_: None = Depends(require_operator)):
    try:
        from controller.config import get_config
        cfg = get_config()
        return {
            "mumble_host": getattr(cfg, "mumble_host", None) or "127.0.0.1",
            "mumble_protocol": getattr(cfg, "mumble_protocol", None) or "ice",
            "mumble_ice_port": getattr(cfg, "mumble_ice_port", None) or 6502,
            "mumble_secret": "" if not getattr(cfg, "mumble_secret", None) else "***",
            "mumble_grpc_port": getattr(cfg, "mumble_grpc_port", None) or 50051,
            "mumble_token": "" if not getattr(cfg, "mumble_token", None) else "***",
            "mumble_exe_path": getattr(cfg, "mumble_exe_path", None) or "",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def put_mumble_config(body: MumbleConfigBody, _: None = Depends(require_operator)):
    try:
        from controller.config import get_config, get_config_path
        import json
        from pathlib import Path
        cfg = get_config()
        new_data = cfg.model_dump()
        if body.mumble_host is not None:
            new_data["mumble_host"] = body.mumble_host.strip() or "127.0.0.1"
        if body.mumble_protocol is not None:
            proto = body.mumble_protocol.strip().lower()
            if proto in ("ice", "grpc"):
                new_data["mumble_protocol"] = proto
        if body.mumble_ice_port is not None:
            new_data["mumble_ice_port"] = body.mumble_ice_port
        if body.mumble_secret is not None and body.mumble_secret != "***":
            new_data["mumble_secret"] = body.mumble_secret
        if body.mumble_grpc_port is not None:
            new_data["mumble_grpc_port"] = body.mumble_grpc_port
        if body.mumble_token is not None and body.mumble_token != "***":
            new_data["mumble_token"] = body.mumble_token
        if body.mumble_exe_path is not None:
            new_data["mumble_exe_path"] = body.mumble_exe_path.strip()
        new_cfg = cfg.__class__(**new_data)
        config_path = get_config_path()
        if config_path:
            path_obj = Path(config_path)
            tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(new_cfg.model_dump(), f, indent=2, ensure_ascii=False)
            tmp.replace(path_obj)
        reset_mumble_client()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
