"""
PitBox Update API Routes — unified update orchestration + granular management.

Primary (unified):
  POST /api/update/run                 - one-click: check, update controller, roll out fleet
  GET  /api/update/summary             - unified system-wide update status

Granular (still available):
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


_orchestrator_state = {
    "phase": "idle",
    "message": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "controller_needed": False,
    "controller_done": False,
    "fleet_dispatched": False,
    "fleet_results": [],
}

def _reset_orchestrator():
    _orchestrator_state.update({
        "phase": "idle",
        "message": "",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "controller_needed": False,
        "controller_done": False,
        "fleet_dispatched": False,
        "fleet_results": [],
    })


@router.post("/run")
async def unified_run(_: None = Depends(_get_require_operator())):
    import time as _time

    if _orchestrator_state["phase"] not in ("idle", "done", "error"):
        raise HTTPException(status_code=409, detail="Update already in progress")

    _reset_orchestrator()
    _orchestrator_state["phase"] = "checking"
    _orchestrator_state["message"] = "Checking for updates..."
    _orchestrator_state["started_at"] = _time.time()

    clear_cache()
    loop = asyncio.get_event_loop()
    release_status = await loop.run_in_executor(None, lambda: get_controller_update_status(force_refresh=True))

    if release_status.get("error"):
        _orchestrator_state["phase"] = "error"
        _orchestrator_state["error"] = release_status["error"]
        _orchestrator_state["message"] = release_status["error"]
        _orchestrator_state["finished_at"] = _time.time()
        return {"ok": False, "phase": "error", "message": release_status["error"]}

    latest = release_status.get("latest_version")
    controller_needs_update = release_status.get("update_available", False)
    _orchestrator_state["controller_needed"] = controller_needs_update

    if controller_needs_update:
        _orchestrator_state["phase"] = "updating_controller"
        _orchestrator_state["message"] = "Updating controller..."
        from controller.updater import run_unified_installer_update, apply_controller_update
        has_unified = bool(
            release_status.get("unified_installer")
            and (release_status["unified_installer"].get("url") or release_status["unified_installer"].get("api_url"))
        )
        has_zip = bool(
            release_status.get("controller_zip")
            and (release_status["controller_zip"].get("url") or release_status["controller_zip"].get("api_url"))
        )
        if has_unified:
            ok, msg = run_unified_installer_update()
        elif has_zip:
            ok, msg = apply_controller_update()
        else:
            _orchestrator_state["phase"] = "error"
            _orchestrator_state["error"] = "No installer asset in this release"
            _orchestrator_state["message"] = "No installer asset in this release"
            _orchestrator_state["finished_at"] = _time.time()
            return {"ok": False, "phase": "error", "message": "No installer asset in this release"}

        if not ok:
            _orchestrator_state["phase"] = "error"
            _orchestrator_state["error"] = msg
            _orchestrator_state["message"] = msg
            _orchestrator_state["finished_at"] = _time.time()
            return {"ok": False, "phase": "error", "message": msg}

        _orchestrator_state["controller_done"] = True
        _orchestrator_state["message"] = "Controller update started, rolling out to fleet..."
    else:
        _orchestrator_state["controller_done"] = True

    _orchestrator_state["phase"] = "updating_fleet"
    _orchestrator_state["message"] = "Rolling out to sims..."

    if latest:
        set_approved_version(latest)

    cache = _get_status_cache()
    enrolled = _get_enrolled()
    chosen_ids = []
    tasks = []
    fleet_results = []

    for rig in enrolled:
        agent_id = (rig.get("agent_id") or "").strip()
        if not agent_id:
            continue
        backend = (rig.get("backend") or "agent").strip().lower()
        if backend != "agent":
            continue
        st = cache.get(agent_id)
        online = bool(st and getattr(st, "online", False))
        ac_running = bool(st and getattr(st, "ac_running", False))

        if not online:
            set_agent_offline(agent_id)
            fleet_results.append({"agent_id": agent_id, "result": "offline", "message": "Offline"})
            continue

        if ac_running:
            update_agent_state(agent_id, update_status="pending_idle",
                               target_version=latest, online=True, ac_running=True)
            fleet_results.append({"agent_id": agent_id, "result": "pending_idle",
                                  "message": "Busy - will update when idle"})
            continue

        chosen_ids.append(agent_id)
        payload = {}
        if latest:
            payload["target_version"] = latest
        tasks.append(_send_agent_command(agent_id, "update", payload, timeout=45.0))
        update_agent_state(agent_id, update_status="downloading", target_version=latest)

    if tasks:
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        for aid, raw in zip(chosen_ids, raw_results):
            if isinstance(raw, Exception):
                update_agent_state(aid, update_status="failed", last_update_error=str(raw))
                fleet_results.append({"agent_id": aid, "result": "failed", "message": str(raw)})
            elif isinstance(raw, dict):
                new_status = raw.get("update_status", "installing")
                if raw.get("update_available") is False:
                    new_status = "idle"
                update_agent_state(aid, update_status=new_status,
                                   target_version=raw.get("latest_version") or latest,
                                   last_update_error=raw.get("message") if not raw.get("success") else None)
                fleet_results.append({"agent_id": aid, "result": new_status,
                                      "message": raw.get("message", "OK")})

    _orchestrator_state["fleet_dispatched"] = True
    _orchestrator_state["fleet_results"] = fleet_results
    _orchestrator_state["phase"] = "done"
    _orchestrator_state["finished_at"] = _time.time()
    _orchestrator_state["message"] = "Update complete"

    return {
        "ok": True,
        "phase": "done",
        "controller_updated": controller_needs_update,
        "fleet_results": fleet_results,
        "message": _orchestrator_state["message"],
    }


@router.get("/summary")
async def unified_summary(_: None = Depends(_get_require_operator_if_pw())):
    import time as _time
    release_status = get_controller_update_status()
    agents = get_all_agent_states()

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

    filtered_agents = [a for a in agents if a["agent_id"] in enrolled_ids]

    from controller.updater import get_updater_status
    updater = get_updater_status()
    updater_state = updater.get("state", "idle") if updater else "idle"

    controller_updating = updater_state not in ("idle", "done", "error")

    approved = release_status.get("latest_version")
    total_sims = len(filtered_agents)
    online_sims = sum(1 for a in filtered_agents if a.get("online"))
    pending_idle = sum(1 for a in filtered_agents if a.get("update_status") == "pending_idle")
    in_progress = sum(1 for a in filtered_agents if a.get("update_status") in ("downloading", "installing", "restarting"))
    failed_count = sum(1 for a in filtered_agents if a.get("update_status") == "failed")
    updated_count = 0
    for a in filtered_agents:
        st = a.get("update_status", "unknown")
        if st in ("idle", "updated"):
            if approved and a.get("installed_version") == approved:
                updated_count += 1
            elif not approved:
                updated_count += 1

    overall = "up_to_date"
    if _orchestrator_state["phase"] not in ("idle", "done", "error"):
        overall = "updating"
    elif controller_updating:
        overall = "updating"
    elif in_progress > 0:
        overall = "updating"
    elif failed_count > 0:
        overall = "has_failures"
    elif pending_idle > 0:
        overall = "pending"
    elif release_status.get("update_available"):
        overall = "available"

    return {
        "ok": True,
        "overall": overall,
        "orchestrator_phase": _orchestrator_state["phase"],
        "controller": {
            "current_version": release_status.get("current_version"),
            "latest_version": release_status.get("latest_version"),
            "update_available": release_status.get("update_available", False),
            "state": updater_state,
            "message": updater.get("message", "") if updater else "",
            "percent": updater.get("percent", 0) if updater else 0,
        },
        "fleet": {
            "total": total_sims,
            "online": online_sims,
            "updated": updated_count,
            "pending_idle": pending_idle,
            "in_progress": in_progress,
            "failed": failed_count,
        },
        "agents": filtered_agents,
        "release": {
            "name": release_status.get("release_name"),
            "published_at": release_status.get("published_at"),
            "html_url": release_status.get("html_url"),
            "notes_markdown": release_status.get("notes_markdown"),
        },
        "last_checked_at": release_status.get("last_checked_at"),
        "error": release_status.get("error"),
    }
