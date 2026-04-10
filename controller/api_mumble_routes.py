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


# ── Config ─────────────────────────────────────────────────────────────────────

class MumbleConfigBody(BaseModel):
    mumble_host: Optional[str] = None
    mumble_protocol: Optional[str] = None
    mumble_ice_port: Optional[int] = None
    mumble_secret: Optional[str] = None
    mumble_grpc_port: Optional[int] = None
    mumble_token: Optional[str] = None


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
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def put_mumble_config(body: MumbleConfigBody, _: None = Depends(require_operator)):
    try:
        from controller.config import get_config, get_config_path, save_config, set_config
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
        new_cfg = cfg.__class__(**new_data)
        config_path = get_config_path()
        if config_path:
            save_config(Path(config_path), new_cfg)
        else:
            set_config(new_cfg)
        reset_mumble_client()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
