"""HTTP routes for PitBox-native server admin/control commands.

These endpoints sit on top of :mod:`controller.server_control` and expose
chat / kick / next-session / restart-session / generic admin / grid
reverse / grid swap / info functionality. They are mounted under
``/api/server`` from ``controller.main``.

Authentication: gated by ``require_operator_if_password_configured`` so
the LAN trust-boundary remains consistent with the rest of the
controller's admin endpoints.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from controller.operator_auth import require_operator_if_password_configured
from controller.server_control.adapter import (
    ServerControlError,
    get_adapter,
)
from controller.server_control.grid import (
    GridError,
    list_grid,
    reverse_grid,
    swap_grid,
)
from controller.server_preset_helpers import _get_server_preset_dir_safe
from controller.timing.engine import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/server", tags=["server-control"])


# ---------------------------------------------------------------------- #
# Request models
# ---------------------------------------------------------------------- #
class _BaseServerCmd(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=128)


class ChatBody(_BaseServerCmd):
    message: str = Field(..., min_length=1, max_length=255)
    car_id: Optional[int] = Field(
        default=None,
        ge=0,
        le=255,
        description="Target car_id for a private message; omit to broadcast.",
    )


class KickBody(_BaseServerCmd):
    car_id: int = Field(..., ge=0, le=255)


class AdminBody(_BaseServerCmd):
    command: str = Field(..., min_length=1, max_length=255)


class GridSwapBody(_BaseServerCmd):
    slot_a: int = Field(..., ge=0, le=255)
    slot_b: int = Field(..., ge=0, le=255)


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _entry_list_path(server_id: str):
    """Resolve the live entry_list.ini for ``server_id``.

    Prefers ``cfg/entry_list.ini`` (acServer's runtime location) over the
    preset root copy, since :func:`api_server_config_routes
    ._ensure_preset_cfg_for_ac_server` syncs the preset copy into ``cfg/``
    at start-up but operators may have edited the cfg copy directly.
    """
    preset_dir = _get_server_preset_dir_safe(server_id)
    if not preset_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Preset directory not found for server_id={server_id!r}",
        )
    cfg_el = preset_dir / "cfg" / "entry_list.ini"
    if cfg_el.exists():
        return cfg_el
    root_el = preset_dir / "entry_list.ini"
    if root_el.exists():
        return root_el
    raise HTTPException(
        status_code=404,
        detail=f"entry_list.ini not found in {cfg_el} or {root_el}",
    )


def _is_running(server_id: str) -> Optional[dict]:
    """Return a status dict if ``server_id`` is currently running, else None."""
    # Imported lazily to avoid circular import via main.py route wiring.
    from controller import api_server_config_routes as cfg_routes

    with cfg_routes._running_servers_lock:
        inst = cfg_routes._running_servers.get(server_id)
        if inst is None:
            return None
        if inst.proc.poll() is not None:
            return {
                "server_id": server_id,
                "running": False,
                "status": "crashed",
                "pid": inst.pid,
            }
        return {
            "server_id": server_id,
            "running": True,
            "status": inst.status or "running",
            "pid": inst.pid,
            "preset_path": str(inst.preset_path),
            "udp_port": inst.udp_port,
            "tcp_port": inst.tcp_port,
            "http_port": inst.http_port,
            "started_at": inst.started_at,
        }


def _require_running(server_id: str) -> dict:
    info = _is_running(server_id)
    if not info or not info.get("running"):
        raise HTTPException(
            status_code=409,
            detail=f"Server {server_id!r} is not running",
        )
    return info


# ---------------------------------------------------------------------- #
# Command endpoints
# ---------------------------------------------------------------------- #
@router.post("/chat")
async def post_chat(
    body: ChatBody,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Send a chat message (broadcast if ``car_id`` omitted)."""
    _require_running(body.server_id)
    adapter = get_adapter()
    try:
        if body.car_id is None:
            adapter.broadcast_chat(body.server_id, body.message)
            scope = "broadcast"
        else:
            adapter.send_chat_to_car(body.server_id, body.car_id, body.message)
            scope = f"car_{body.car_id}"
    except ServerControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "scope": scope, "server_id": body.server_id}


@router.post("/kick")
async def post_kick(
    body: KickBody,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Kick the driver currently in ``car_id``."""
    _require_running(body.server_id)
    try:
        get_adapter().kick_user(body.server_id, body.car_id)
    except ServerControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "server_id": body.server_id, "car_id": body.car_id}


@router.post("/next-session")
async def post_next_session(
    body: _BaseServerCmd,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Advance to the next session (Practice -> Qual -> Race)."""
    _require_running(body.server_id)
    try:
        get_adapter().next_session(body.server_id)
    except ServerControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "server_id": body.server_id, "action": "next_session"}


@router.post("/restart-session")
async def post_restart_session(
    body: _BaseServerCmd,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Restart the current session in place."""
    _require_running(body.server_id)
    try:
        get_adapter().restart_session(body.server_id)
    except ServerControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "server_id": body.server_id, "action": "restart_session"}


@router.post("/admin")
async def post_admin(
    body: AdminBody,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Send a generic ``/admin`` text command (e.g. ``/ballast 3 50``)."""
    _require_running(body.server_id)
    try:
        get_adapter().admin_command(body.server_id, body.command)
    except ServerControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "server_id": body.server_id, "command": body.command}


# ---------------------------------------------------------------------- #
# Grid endpoints
# ---------------------------------------------------------------------- #
@router.get("/grid")
async def get_grid(
    server_id: str,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Return the current entry_list.ini grid for the given server."""
    path = _entry_list_path(server_id)
    try:
        entries = list_grid(path)
    except GridError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"server_id": server_id, "path": str(path), "entries": entries}


@router.post("/grid/reverse")
async def post_grid_reverse(
    body: _BaseServerCmd,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Reverse the grid order in entry_list.ini.

    Requires a server restart (or the next session start) to take effect
    on acServer.exe -- the dedicated server only reads entry_list.ini at
    start-up. A warning is included in the response when the server is
    currently running.
    """
    path = _entry_list_path(body.server_id)
    try:
        result = reverse_grid(path)
    except GridError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    running = _is_running(body.server_id)
    if running and running.get("running"):
        result["warning"] = (
            "entry_list.ini updated, but acServer.exe is currently running. "
            "Restart the server (or wait for it to be stopped) for the new "
            "grid to take effect."
        )
    return {"success": True, "server_id": body.server_id, **result}


@router.post("/grid/swap")
async def post_grid_swap(
    body: GridSwapBody,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Swap two grid slots (0-indexed) in entry_list.ini."""
    path = _entry_list_path(body.server_id)
    try:
        result = swap_grid(path, body.slot_a, body.slot_b)
    except GridError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    running = _is_running(body.server_id)
    if running and running.get("running"):
        result["warning"] = (
            "entry_list.ini updated, but acServer.exe is currently running. "
            "Restart the server for the new grid to take effect."
        )
    return {"success": True, "server_id": body.server_id, **result}


# ---------------------------------------------------------------------- #
# Info endpoint
# ---------------------------------------------------------------------- #
@router.get("/info")
async def get_info(
    server_id: str,
    _: None = Depends(require_operator_if_password_configured),
) -> dict:
    """Aggregate live info for a server: process state + telemetry snapshot.

    Combines:
      * acServer.exe process status (from ``api_server_config_routes``)
      * resolved UDP admin endpoint (from the adapter's preset reader)
      * live telemetry from the timing engine (session, drivers, stats)

    Returns ``running=False`` and ``telemetry=None`` if the server is not
    currently running; the call still succeeds so the UI can render a
    consistent panel either way.
    """
    process_info = _is_running(server_id) or {
        "server_id": server_id,
        "running": False,
        "status": "stopped",
    }

    target_info: Optional[dict] = None
    target_error: Optional[str] = None
    try:
        target = get_adapter().resolve_target(server_id)
        target_info = {"host": target.host, "port": target.port}
    except ServerControlError as exc:
        target_error = str(exc)

    telemetry = None
    if process_info.get("running"):
        try:
            telemetry = get_engine().snapshot()
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("server_control.info: engine snapshot failed: %s", exc)
            telemetry = None

    return {
        "server_id": server_id,
        "process": process_info,
        "udp_admin_target": target_info,
        "udp_admin_target_error": target_error,
        "telemetry": telemetry,
    }
