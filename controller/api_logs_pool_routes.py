"""Structured event log endpoints and dynamic server pool API (included by ``api_routes``)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from controller.common.event_log import (
    EventLogEntry as EventLogEntryModel,
    LogCategory as EventLogCategory,
    LogLevel as EventLogLevel,
)
from controller.operator_auth import require_operator
from controller.security import require_agent
from controller.server_pool import pool_manager
from controller.service.event_store import (
    append_event as event_store_append,
    query_events as event_store_query,
    query_summary_last_minutes as event_store_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/logs/event")
async def post_log_event(body: EventLogEntryModel, agent_id: str = Depends(require_agent)):
    """Accept structured event from agent. Requires X-Agent-Id + X-Agent-Token."""
    updates = {}
    if agent_id and not (body.rig_id or "").strip():
        updates["rig_id"] = agent_id
    if agent_id and body.source != "Agent":
        updates["source"] = "Agent"
    entry = body.model_copy(update=updates) if updates else body
    event_store_append(entry)
    return {}


@router.get("/logs/events")
async def get_log_events(
    rig_id: Optional[str] = None,
    category: Optional[str] = None,
    level: Optional[str] = None,
    since_minutes: int = 60,
    limit: int = 300,
    search: Optional[str] = None,
    _: None = Depends(require_operator),
):
    """Query event log with filters. Returns list of EventLogEntry newest first."""
    cat_enum = None
    if category:
        try:
            cat_enum = EventLogCategory(category)
        except ValueError:
            pass
    level_enum = None
    if level:
        try:
            level_enum = EventLogLevel(level)
        except ValueError:
            pass
    events = event_store_query(
        rig_id=rig_id,
        category=cat_enum,
        level=level_enum,
        since_minutes=since_minutes,
        limit=limit,
        search=search,
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/logs/summary")
async def get_log_summary(since_minutes: int = 60, _: None = Depends(require_operator)):
    """Rollup counts for last N minutes: errors_by_rig, errors_by_category, total_errors, total_warns."""
    return event_store_summary(since_minutes)


class CreateServerRequest(BaseModel):
    track: str
    car: str
    players: int = 6
    host_rig_id: Optional[str] = None


class ReleaseServerRequest(BaseModel):
    server_number: int


@router.post("/server/create")
async def create_pool_server(req: CreateServerRequest, _: None = Depends(require_operator)):
    """Allocate a free server slot from the pool and start it."""
    try:
        return pool_manager.create_server(
            track=req.track,
            car=req.car,
            max_players=req.players,
            host_rig_id=req.host_rig_id,
        )
    except Exception as e:
        logger.exception("create_pool_server failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/server/release")
async def release_pool_server(req: ReleaseServerRequest, _: None = Depends(require_operator)):
    """Release a server slot back to the pool."""
    try:
        pool_manager.release_server(req.server_number)
        return {"ok": True}
    except Exception as e:
        logger.exception("release_pool_server failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/server/status")
async def get_pool_server_status(_: None = Depends(require_operator)):
    """List all 15 slots and their current states."""
    try:
        return pool_manager.get_server_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
