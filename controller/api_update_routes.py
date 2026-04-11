"""
PitBox Update API Routes — clean controller + fleet update management.

New route structure:
  GET  /api/update/controller/status   - controller release + install state
  POST /api/update/controller/check    - force refresh from GitHub
  POST /api/update/controller/apply    - start controller update
  GET  /api/update/fleet/status        - all sims update status + summary
  POST /api/update/fleet/start         - begin update on selected/all sims
  POST /api/update/fleet/cancel        - cancel pending updates
  POST /api/update/fleet/retry         - retry failed updates
  GET  /api/update/releases            - list available releases

Legacy routes in api_routes.py are preserved as shims.
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from controller.release_service import (
    get_controller_update_status,
    clear_cache,
    fetch_latest_release,
    list_releases,
    compare_semver,
)
from controller.fleet_state import (
    load_state as load_fleet_state,
    save_state as save_fleet_state,
    get_fleet_summary,
    get_all_agent_states,
    update_agent_state,
    update_agent_from_poll,
    set_agent_offline,
    set_approved_version,
    cancel_pending as cancel_fleet_pending,
    mark_failed_for_retry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["update"])


def _get_require_operator():
    from controller.operator_auth import require_operator
    return require_operator


def _get_require_operator_if_pw():
    from controller.operator_auth import require_operator_if_password_configured
    return require_operator_if_password_configured


def _get_status_cache():
    from controller.api_routes import get_status_cache
    return get_status_cache()


def _get_enrolled():
    from controller.api_routes import enrolled_get_all_ordered
    return enrolled_get_all_ordered()


async def _send_agent_command(agent_id, endpoint, payload, timeout=30.0, method="POST"):
    from controller.api_routes import send_agent_command
    return await send_agent_command(agent_id, endpoint, payload, timeout=timeout, method=method)


@router.get("/controller/status")
async def controller_status(refresh: bool = False, _: None = Depends(_get_require_operator_if_pw())):
    if refresh:
        clear_cache()
    release_status = get_controller_update_status(force_refresh=refresh)

    from controller.updater import get_updater_status
    updater = get_updater_status()

    out = {"state": "idle", "message": "", "percent": 0}
    out.update(release_status)
    if updater.get("state") and updater["state"] != "idle":
        out["state"] = updater["state"]
        out["message"] = updater.get("message", "")
        out["percent"] = updater.get("percent", 0)
    return out


@router.post("/controller/check")
async def controller_check(_: None = Depends(_get_require_operator())):
    clear_cache()
    release_status = get_controller_update_status(force_refresh=True)
    return release_status


@router.post("/controller/apply")
async def controller_apply(request: Request, _: None = Depends(_get_require_operator())):
    from controller.updater import run_unified_installer_update, apply_controller_update
    release = get_controller_update_status()
    has_unified = bool(
        release.get("unified_installer")
        and (release["unified_installer"].get("url") or release["unified_installer"].get("api_url"))
    )
    has_zip = bool(
        release.get("controller_zip")
        and (release["controller_zip"].get("url") or release["controller_zip"].get("api_url"))
    )
    if has_unified:
        ok, msg = run_unified_installer_update()
    elif has_zip:
        ok, msg = apply_controller_update()
    else:
        raise HTTPException(status_code=400, detail="No installer asset available in this release")
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/fleet/status")
async def fleet_status(_: None = Depends(_get_require_operator_if_pw())):
    summary = get_fleet_summary()
    agents = get_all_agent_states()
    cache = _get_status_cache()
    enrolled = _get_enrolled()

    enrolled_ids = set()
    for rig in enrolled:
        agent_id = (rig.get("agent_id") or "").strip()
        if not agent_id:
            continue
        backend = (rig.get("backend") or "agent").strip().lower()
        if backend != "agent":
            continue
        enrolled_ids.add(agent_id)

    agent_map = {a["agent_id"]: a for a in agents}

    tasks = []
    task_ids = []
    for agent_id in enrolled_ids:
        st = cache.get(agent_id)
        online = bool(st and getattr(st, "online", False))
        if agent_id not in agent_map:
            if not online:
                set_agent_offline(agent_id)
            else:
                update_agent_state(agent_id, online=True)
        elif not online:
            set_agent_offline(agent_id)

        if online:
            tasks.append(_send_agent_command(agent_id, "update/status", {}, timeout=10.0, method="GET"))
            task_ids.append(agent_id)

    if tasks:
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        for aid, raw in zip(task_ids, raw_results):
            if isinstance(raw, Exception):
                update_agent_state(aid, online=True, update_status="error",
                                   last_update_error=str(raw))
            elif isinstance(raw, dict):
                ac_status = cache.get(aid)
                ac_running = False
                if ac_status and hasattr(ac_status, "ac_running"):
                    ac_running = getattr(ac_status, "ac_running", False)
                update_agent_from_poll(aid, {
                    "online": True,
                    "current_version": raw.get("current_version"),
                    "update_status": raw.get("update_status", "idle"),
                    "target_version": raw.get("target_version"),
                    "last_update_error": raw.get("last_update_error"),
                    "ac_running": ac_running,
                })

    fresh_agents = get_all_agent_states()
    filtered = [a for a in fresh_agents if a["agent_id"] in enrolled_ids]
    fresh_summary = get_fleet_summary()

    return {
        "ok": True,
        "summary": fresh_summary,
        "agents": filtered,
    }


class FleetStartBody(BaseModel):
    agent_ids: Optional[list[str]] = None
    target_version: Optional[str] = None


@router.post("/fleet/start")
async def fleet_start(body: FleetStartBody = None, _: None = Depends(_get_require_operator())):
    if body is None:
        body = FleetStartBody()

    target_version = (body.target_version or "").strip() or None
    agent_ids_filter = body.agent_ids

    cache = _get_status_cache()
    enrolled = _get_enrolled()
    chosen_ids = []
    tasks = []
    offline = []

    for rig in enrolled:
        agent_id = (rig.get("agent_id") or "").strip()
        if not agent_id:
            continue
        backend = (rig.get("backend") or "agent").strip().lower()
        if backend != "agent":
            continue
        if agent_ids_filter and agent_id not in agent_ids_filter:
            continue
        st = cache.get(agent_id)
        online = bool(st and getattr(st, "online", False))
        if not online:
            if agent_ids_filter and agent_id in agent_ids_filter:
                offline.append({"agent_id": agent_id, "success": False,
                                "message": "Offline", "update_status": "offline"})
                set_agent_offline(agent_id)
            continue
        chosen_ids.append(agent_id)
        payload = {}
        if target_version:
            payload["target_version"] = target_version
        tasks.append(_send_agent_command(agent_id, "update", payload, timeout=45.0))
        update_agent_state(agent_id, update_status="downloading",
                           target_version=target_version)

    if not tasks:
        return {"ok": True, "results": offline or [], "message": "No online agents found"}

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results = list(offline)
    for aid, raw in zip(chosen_ids, raw_results):
        if isinstance(raw, Exception):
            update_agent_state(aid, update_status="failed", last_update_error=str(raw))
            results.append({"agent_id": aid, "success": False, "message": str(raw)})
        elif isinstance(raw, dict):
            new_status = raw.get("update_status", "installing")
            if raw.get("update_available") is False:
                new_status = "idle"
            update_agent_state(aid, update_status=new_status,
                               target_version=raw.get("latest_version") or target_version,
                               last_update_error=raw.get("message") if not raw.get("success") else None)
            results.append({"agent_id": aid, **raw})
    return {"ok": True, "results": results}


class FleetCancelBody(BaseModel):
    agent_ids: Optional[list[str]] = None


@router.post("/fleet/cancel")
async def fleet_cancel(body: FleetCancelBody = None, _: None = Depends(_get_require_operator())):
    if body is None:
        body = FleetCancelBody()

    local_cancelled = cancel_fleet_pending(body.agent_ids)

    cache = _get_status_cache()
    enrolled = _get_enrolled()
    chosen_ids = []
    tasks = []

    for rig in enrolled:
        agent_id = (rig.get("agent_id") or "").strip()
        if not agent_id:
            continue
        backend = (rig.get("backend") or "agent").strip().lower()
        if backend != "agent":
            continue
        if body.agent_ids and agent_id not in body.agent_ids:
            continue
        st = cache.get(agent_id)
        online = bool(st and getattr(st, "online", False))
        if not online:
            continue
        chosen_ids.append(agent_id)
        tasks.append(_send_agent_command(agent_id, "update/cancel", {}, timeout=10.0))

    remote_results = []
    if tasks:
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        for aid, raw in zip(chosen_ids, raw_results):
            if isinstance(raw, Exception):
                remote_results.append({"agent_id": aid, "success": False, "message": str(raw)})
            else:
                update_agent_state(aid, update_status="idle", target_version=None, last_update_error=None)
                remote_results.append({"agent_id": aid, "success": True, "message": "Cancelled"})

    return {"ok": True, "results": remote_results, "local_cancelled": local_cancelled}


@router.post("/fleet/retry")
async def fleet_retry(body: FleetCancelBody = None, _: None = Depends(_get_require_operator())):
    if body is None:
        body = FleetCancelBody()
    retried = mark_failed_for_retry(body.agent_ids)
    return {"ok": True, "retried": retried}


@router.get("/releases")
async def get_releases(_: None = Depends(_get_require_operator_if_pw())):
    loop = asyncio.get_event_loop()
    releases = await loop.run_in_executor(None, list_releases)
    return {"ok": True, "releases": releases}
