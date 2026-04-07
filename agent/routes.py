"""
FastAPI routes for PitBox Agent.
"""
import asyncio
import json
import re
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor


def _load_json_with_fallback(path: Path) -> Optional[dict]:
    """Load JSON with encoding fallbacks (utf-8-sig, utf-8, cp1252, latin-1). Returns None if missing or invalid."""
    if not path.is_file():
        return None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
    return None


def _get_track_display_name(tracks_dir: Path, track_id: str, layout: str) -> Optional[str]:
    """Return display name from content/tracks/<track_id>/ui/<layout>/ui_track.json (name field)."""
    try:
        if not track_id or ".." in track_id or "/" in track_id or "\\" in track_id:
            return None
        track_id = track_id.strip()
        layout = (layout or "default").strip()
        track_ui = tracks_dir / track_id / "ui"
        if not track_ui.is_dir():
            return None
        for layout_name in (layout, "default", track_id, ""):
            if not layout_name:
                continue
            ui_track = track_ui / layout_name / "ui_track.json"
            if not ui_track.is_file():
                continue
            data = _load_json_with_fallback(ui_track)
            if data is None:
                continue
            name = (data.get("name") or data.get("screenName") or data.get("uiName") or "").strip()
            if name:
                return name
        return None
    except Exception:
        return None

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from agent.auth import verify_token
from pitbox_common.safe_inputs import validate_steering_shifting_preset_basename
from agent.kiosk_apply import apply_unified
from agent.process_manager import start_process, stop_process, get_process_status
from agent.utils.files import ensure_extension
from agent.utils.cmpreset import (
    cmpreset_to_assists_ini,
    _extract_assists_data,
    validate_assists_ini_content,
    verify_assists_ini_after_write,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _validate_steering_shifting_agent(name: str) -> str:
    try:
        return validate_steering_shifting_preset_basename(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _natural_sort_key(name: str):
    """Sort key for preset names so e.g. '1 Race' < '2 Kids' < '10 Expert'."""
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", name)]


# Request/Response models
class StartRequest(BaseModel):
    """Request to start Assetto Corsa."""
    steering_preset: Optional[str] = None


class StopRequest(BaseModel):
    """Request to stop Assetto Corsa."""
    pass


class ApplySteeringRequest(BaseModel):
    """Request to apply a steering preset."""
    name: str


class ApplyShiftingRequest(BaseModel):
    """Request to apply a shifting preset ([SHIFTER] section merged into controls.ini)."""
    name: str


class SetDriverNameRequest(BaseModel):
    """Request to set driver name in race.ini."""
    driver_name: str


class StatusResponse(BaseModel):
    """Agent status response (backward compat + extended server panel fields)."""
    ac_running: bool
    pid: int | None = None
    uptime_sec: float | None = None
    display_name: Optional[str] = None
    # Extended for Server Panel
    agent_id: Optional[str] = None
    ts: Optional[str] = None
    online: Optional[bool] = None
    error: Optional[str] = None
    heartbeat: Optional[dict] = None
    ac: Optional[dict] = None
    server: Optional[dict] = None


class ResetRigRequest(BaseModel):
    """Request to reset rig to defaults (steering, shifting, display name)."""
    steering_preset: Optional[str] = "Race"
    shifting_preset: Optional[str] = "H-Pattern"
    display_name: Optional[str] = None


class LaunchOnlineRequest(BaseModel):
    """Request to launch and join online server (server + car + presets)."""
    server_ip: Optional[str] = None
    server_port: Optional[int] = None
    car_id: Optional[str] = None
    preset_id: Optional[str] = None
    shifter_mode: Optional[str] = None
    sim_display: Optional[str] = None
    server_cfg_snapshot: Optional[dict] = None  # Full parsed server_cfg.ini from preset
    preset_name: Optional[str] = None
    global_server_password: Optional[str] = None  # Venue-wide join password (overrides preset PASSWORD)
    max_running_time_minutes: Optional[int] = None  # Session limit in minutes (0 = no limit); writes time_limited_test.ini


class UpdateRaceSelectionRequest(BaseModel):
    """Request to write server and/or car selection to race.ini (no launch)."""
    join_addr: Optional[str] = None  # "ip:port"
    server_ip: Optional[str] = None
    server_port: Optional[int] = None
    server_name: Optional[str] = None
    password: Optional[str] = None
    car_id: Optional[str] = None
    skin_id: Optional[str] = None
    server_cfg_snapshot: Optional[dict] = None  # Full parsed server_cfg.ini from preset
    preset_name: Optional[str] = None
    global_server_password: Optional[str] = None  # Venue-wide join password (overrides preset PASSWORD)


def _read_display_name() -> Optional[str]:
    """Read display_name from persistent state file. Returns None if not set or file missing."""
    from agent.config import get_config, get_agent_state_path
    try:
        config = get_config()
        path = get_agent_state_path(config)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("display_name") or "").strip() or None
    except Exception:
        return None


def _default_display_name() -> str:
    """Default display name from agent_id (e.g. '5' -> 'Sim 5')."""
    from agent.config import get_config
    config = get_config()
    aid = (config.agent_id or "").strip()
    if aid.isdigit():
        return f"Sim {aid}"
    return aid or "Sim 1"


def _write_display_name(display_name: str) -> None:
    """Persist display_name to state file."""
    from agent.config import get_config, get_agent_state_path
    config = get_config()
    path = get_agent_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"display_name": display_name}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _build_server_status(ac_running: bool, pid: int | None) -> dict[str, Any]:
    """Build server object for status. When AC not running -> UNAVAILABLE; when running but no detection -> DISCONNECTED."""
    if not ac_running:
        return {
            "state": "UNAVAILABLE",
            "source": "UNKNOWN",
        }
    # AC running but we don't have live server detection yet
    return {
        "state": "DISCONNECTED",
        "source": "UNKNOWN",
        "last": None,  # Optional: could be set if we track last-connected server
    }


# Endpoints
@router.get("/ping", dependencies=[Depends(verify_token)])
async def ping():
    """Health check - requires Bearer token."""
    return {"status": "ok"}


@router.get("/status", dependencies=[Depends(verify_token)])
async def get_status():
    """Get current agent status (heartbeat, ac, server, last_session from race.ini)."""
    from agent.config import get_config, get_controls_ini_dir
    from agent.race_ini import parse_last_session
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    config = get_config()
    status = get_process_status()
    display = _read_display_name() or _default_display_name()
    ac_running = status.get("ac_running", False)
    pid = status.get("pid")
    # Last session from race.ini (both modes; do not assume one schema)
    last_session = None
    try:
        cfg_dir = get_controls_ini_dir(config)
        if cfg_dir:
            race_ini = Path(cfg_dir) / "race.ini"
            last_session = parse_last_session(race_ini)
            if last_session:
                last_session = dict(last_session)
                track = (last_session.get("track") or "").strip()
                if track and track != "—":
                    try:
                        layout = (last_session.get("layout") or "default").strip() or "default"
                        tracks_dir = Path(config.paths.acs_exe).resolve().parent / "content" / "tracks"
                        display_name = _get_track_display_name(tracks_dir, track, layout)
                        last_session["track_name"] = display_name or track
                    except Exception:
                        last_session["track_name"] = track
                else:
                    last_session["track_name"] = last_session.get("track") or "—"
    except Exception:
        pass
    # Extended fields for Server Panel (use device_id when paired so controller cache key matches)
    try:
        from agent.pairing import is_paired
        from agent.identity import get_device_id
        status["agent_id"] = get_device_id() if is_paired() else config.agent_id
    except Exception:
        status["agent_id"] = config.agent_id
    status["acs_running"] = ac_running
    status["ts"] = ts
    status["online"] = True
    status["error"] = None
    status["heartbeat"] = {"last_seen_ts": ts, "age_ms": 0}
    status["ac"] = {
        "running": ac_running,
        "pid": pid,
        "focused": None,
        "mode": "UNKNOWN",
    }
    status["server"] = _build_server_status(ac_running, pid)
    status["display_name"] = display
    try:
        from agent.hotkey import get_control_mode
        status["control_mode"] = get_control_mode()
    except Exception:
        status["control_mode"] = "AUTO"
    # Normalized last_session for sim display (mode_kind, car_id, skin_id, track_id, layout_id, server_*)
    if last_session:
        last_session["mode_kind"] = last_session.get("mode") or "singleplayer"
        last_session["car_id"] = last_session.get("car") or "—"
        last_session["skin_id"] = last_session.get("skin") or "—"
        last_session["track_id"] = last_session.get("track") or "—"
        last_session["layout_id"] = last_session.get("layout") or "—"
        srv = last_session.get("server")
        if srv:
            last_session["server_name"] = srv.get("name") or "—"
            last_session["server_ip"] = srv.get("ip") or "—"
            last_session["server_port"] = srv.get("port") or "—"
        else:
            last_session["server_name"] = last_session["server_ip"] = last_session["server_port"] = "—"
    status["last_session"] = last_session
    # Race results from AC race_out.json (Documents\Assetto Corsa\out\race_out.json)
    try:
        from agent.race_out import get_race_out_path, parse_race_out
        race_out_path = get_race_out_path(config)
        parsed = parse_race_out(race_out_path)
        if parsed and parsed.get("results"):
            status["race_results"] = parsed["results"]
            status["race_track_name"] = (parsed.get("track_name") or "").strip() or "—"
            status["race_session_type"] = (parsed.get("session_type") or "").strip() or ""
            status["race_total_laps"] = parsed.get("total_laps")
        else:
            status["race_results"] = None
            status["race_track_name"] = None
            status["race_session_type"] = None
            status["race_total_laps"] = None
    except Exception:
        status["race_results"] = None
        status["race_track_name"] = None
        status["race_session_type"] = None
        status["race_total_laps"] = None
    return status


def _run_start_background(steering_preset: Optional[str]):
    """Run start_process in background (blocking). Used by /start for fire-and-forget."""
    try:
        start_process(steering_preset=steering_preset)
    except Exception as e:
        logger.exception("Background start failed: %s", e)


@router.post("/start", dependencies=[Depends(verify_token)])
async def start_ac(request: StartRequest):
    """Start Assetto Corsa by launching paths.acs.exe directly. Returns immediately; actual launch runs in background."""
    raw_preset = (request.steering_preset or "").strip() or None
    preset = _validate_steering_shifting_agent(raw_preset) if raw_preset else None
    logger.info("Launch requested (direct acs.exe), dispatching to background")
    thread = threading.Thread(target=_run_start_background, args=(preset,), daemon=True, name="start-ac")
    thread.start()
    return {
        "ok": True,
        "success": True,
        "message": "launch started",
    }


def _write_time_limited_test_ini(max_running_time_minutes: int) -> None:
    """Write time_limited_test.ini with [SETTINGS] MAX_RUNNING_TIME=<minutes>.
    Skips the write entirely if the file already contains the same value.
    Uses atomic write when a change is needed.
    """
    import tempfile
    import os
    from agent.config import get_config, get_time_limited_test_ini_path
    config = get_config()
    path = get_time_limited_test_ini_path(config)
    value = max(0, int(max_running_time_minutes))
    path = path.resolve()
    # Read current value; skip write if unchanged
    try:
        if path.exists():
            with open(str(path), "r", encoding="utf-8", errors="replace") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line.upper().startswith("MAX_RUNNING_TIME"):
                        _parts = _line.split("=", 1)
                        if len(_parts) == 2 and int(_parts[1].strip()) == value:
                            logger.debug("time_limited_test.ini already has MAX_RUNNING_TIME=%s, skipping write", value)
                            return
                        break
    except Exception:
        pass  # Unreadable; proceed with write
    content = "[SETTINGS]\nMAX_RUNNING_TIME=" + str(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="time_limited_test.", suffix=".ini")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
        logger.info("time_limited_test.ini written: %s MAX_RUNNING_TIME=%s (minutes)", path, value)
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        logger.warning("Failed to write time_limited_test.ini %s: %s", path, e)


@router.post("/launch_online", dependencies=[Depends(verify_token)])
async def launch_online(request: LaunchOnlineRequest):
    """Launch AC for online join. REQUIRES server_cfg_snapshot; syncs race.ini from preset, then starts acs.exe."""
    from agent.config import get_config, get_controls_ini_dir
    from agent.kiosk_apply import _verify_and_log_remote
    from agent.server_cfg_sync import sync_race_ini_from_server_cfg
    try:
        raw_pid = (request.preset_id or "").strip() or None
        preset = _validate_steering_shifting_agent(raw_pid) if raw_pid else None
        server_ip = (request.server_ip or "").strip() or None
        server_port = request.server_port
        cfg_dir = get_controls_ini_dir(get_config())
        if cfg_dir and server_ip and server_port is not None:
            race_ini = Path(cfg_dir) / "race.ini"
            server_cfg = request.server_cfg_snapshot
            preset_name = request.preset_name
            car_id = (request.car_id or "").strip() or None
            if not server_cfg or not isinstance(server_cfg, dict):
                logger.error("SKIPPING FULL SYNC: snapshot missing (server_cfg_snapshot required for online join)")
                raise HTTPException(
                    status_code=400,
                    detail="Server preset config (server_cfg_snapshot) required for online join. Controller must send preset server_cfg.ini.",
                )
            global_pwd = (request.global_server_password or "").strip() or None
            sync_race_ini_from_server_cfg(
                server_cfg, server_ip, int(server_port), car_id, race_ini,
                preset_name=preset_name, global_password=global_pwd,
            )
            _verify_and_log_remote(race_ini)
        max_min = request.max_running_time_minutes
        if max_min is not None and max_min >= 0:
            _write_time_limited_test_ini(max_min)
        success, pid, message = start_process(steering_preset=preset)
        if success:
            return {"success": True, "message": message or "Launch sent", "pid": pid}
        raise HTTPException(status_code=500, detail=message or "Launch failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("launch_online failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_race_selection", dependencies=[Depends(verify_token)])
async def update_race_selection(request: UpdateRaceSelectionRequest):
    """Write server and/or car selection to race.ini (no launch). REQUIRES server_cfg_snapshot for server selection."""
    from agent.config import get_config, get_controls_ini_dir
    from agent.kiosk_apply import parse_join_addr, _update_race_ini_car, _verify_and_log_remote
    from agent.server_cfg_sync import sync_race_ini_from_server_cfg
    cfg_dir = get_controls_ini_dir(get_config())
    if not cfg_dir:
        raise HTTPException(status_code=503, detail="cfg path not configured")
    race_ini = Path(cfg_dir) / "race.ini"

    server_ip = (request.server_ip or "").strip() or None
    server_port = request.server_port
    if (server_ip is None or server_port is None) and (request.join_addr or "").strip():
        server_ip, server_port = parse_join_addr(request.join_addr)
    car_id = (request.car_id or "").strip() or None
    server_cfg = request.server_cfg_snapshot
    preset_name = request.preset_name
    if server_ip and server_port is not None:
        if not server_cfg or not isinstance(server_cfg, dict):
            logger.error("SKIPPING FULL SYNC: snapshot missing (server_cfg_snapshot required for online selection)")
            raise HTTPException(
                status_code=400,
                detail="Server preset config (server_cfg_snapshot) required. Controller must send preset server_cfg.ini.",
            )
        global_pwd = (request.global_server_password or "").strip() or None
        sync_race_ini_from_server_cfg(
            server_cfg, server_ip, int(server_port), car_id, race_ini,
            preset_name=preset_name, global_password=global_pwd,
        )
        _verify_and_log_remote(race_ini)
    elif car_id:
        skin_id = (request.skin_id or "").strip() or "default"
        _update_race_ini_car(race_ini, car_id=car_id, skin_id=skin_id)

    return {"success": True, "message": "race.ini updated"}


def _run_stop_background():
    """Run stop_process in background (blocking). Used by /stop for fire-and-forget."""
    try:
        stop_process()
    except Exception as e:
        logger.exception("Background stop failed: %s", e)


@router.post("/stop", dependencies=[Depends(verify_token)])
async def stop_ac(request: StopRequest):
    """Stop Assetto Corsa. Returns immediately; actual stop runs in background."""
    logger.info("Stop requested, dispatching to background")
    thread = threading.Thread(target=_run_stop_background, daemon=True, name="stop-ac")
    thread.start()
    return {
        "ok": True,
        "success": True,
        "message": "exit started",
    }


@router.get("/presets/steering", dependencies=[Depends(verify_token)])
async def list_steering_presets():
    """List available steering preset names (from savedsetups or managed_steering_templates)."""
    from agent.config import get_config, get_preset_dir
    config = get_config()
    managed = get_preset_dir(config)
    if not managed:
        return {"items": []}
    src_dir = Path(managed)
    if not src_dir.is_dir():
        return {"items": []}
    items = []
    for f in src_dir.iterdir():
        if f.suffix.lower() == ".ini" and f.is_file():
            items.append(f.stem)
    items.sort(key=_natural_sort_key)
    return {"items": items}


@router.post("/apply_steering_preset", dependencies=[Depends(verify_token)])
async def apply_steering_preset(request: ApplySteeringRequest):
    """Apply steering: copy preset .ini to cfg/controls.ini (overwrite). 503 if config missing, 404 if file missing, 500 on I/O."""
    from agent.config import get_config, get_preset_dir, get_controls_ini_dir
    from agent.process_manager import get_process_status
    import shutil
    config = get_config()
    preset_name = (request.name or "").strip()
    if not preset_name:
        raise HTTPException(status_code=400, detail="Preset name is required")
    preset_name = _validate_steering_shifting_agent(preset_name)
    managed_raw = get_preset_dir(config)
    ac_cfg_raw = get_controls_ini_dir(config)
    if not managed_raw or not ac_cfg_raw:
        raise HTTPException(
            status_code=503,
            detail="Steering presets not configured (set savedsetups/savedsetups_dir and ac_cfg/ac_cfg_dir)",
        )
    src_dir = Path(managed_raw)
    ac_cfg = Path(ac_cfg_raw)
    ini_filename = ensure_extension(preset_name, ".ini")
    src_file = src_dir / ini_filename
    if not src_file.is_file():
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            from agent.config import get_config as get_agent_config
            cfg = get_agent_config()
            emit(LogLevel.ERROR, LogCategory.PRESET, f"Steering preset not found: {ini_filename}", rig_id=cfg.agent_id, event_code="PRESET_STEERING_MISSING", details={"path": str(src_file), "preset": preset_name})
        except Exception:
            pass
        raise HTTPException(status_code=404, detail=f"Preset not found: {ini_filename} (path: {src_file})")
    dest_file = ac_cfg / "controls.ini"
    try:
        ac_cfg.mkdir(parents=True, exist_ok=True)
        if dest_file.exists():
            shutil.copy2(dest_file, dest_file.with_suffix(".ini.bak"))
        shutil.copy2(src_file, dest_file)
    except OSError as e:
        logger.exception("Steering apply failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Cannot copy preset: {e}")
    status = get_process_status()
    requires_restart = status.get("ac_running", False)
    return {
        "success": True,
        "message": f"Applied {preset_name}",
        "requires_restart": requires_restart,
    }


@router.get("/presets/shifting", dependencies=[Depends(verify_token)])
async def list_shifting_presets():
    """List shifting preset names (Content Manager .cmpreset in cm_assists_presets, e.g. %LOCALAPPDATA%\\AcTools Content Manager\\Presets\\Assists)."""
    from agent.config import get_config, get_assists_presets_dir
    config = get_config()
    presets_dir = get_assists_presets_dir(config)
    if not presets_dir:
        return {"items": []}
    src_dir = Path(presets_dir)
    if not src_dir.is_dir():
        return {"items": []}
    items = []
    for f in src_dir.iterdir():
        if f.suffix.lower() == ".cmpreset" and f.is_file():
            items.append(f.stem)
    items.sort(key=_natural_sort_key)
    return {"items": items}


@router.post("/apply_shifting_preset", dependencies=[Depends(verify_token)])
async def apply_shifting_preset(request: ApplyShiftingRequest):
    """Apply shifting: read .cmpreset from cm_dir, overwrite assists.ini (no merge). 503 if config missing, 404 if file missing, 500 on I/O."""
    from agent.config import get_config, get_assists_presets_dir, get_controls_ini_dir
    from agent.process_manager import get_process_status
    config = get_config()
    preset_name = (request.name or "").strip()
    if not preset_name:
        raise HTTPException(status_code=400, detail="Preset name is required")
    preset_name = _validate_steering_shifting_agent(preset_name)
    cm_dir_raw = get_assists_presets_dir(config)
    ac_cfg_raw = get_controls_ini_dir(config)
    if not cm_dir_raw or not ac_cfg_raw:
        raise HTTPException(
            status_code=503,
            detail="Assists presets not configured (set cm_assists_presets_dir or cm_assists_presets, and ac_cfg_dir or ac_cfg)",
        )
    cm_dir = Path(cm_dir_raw)
    ac_cfg = Path(ac_cfg_raw)
    assists_ini = ac_cfg / "assists.ini"
    cmpreset_filename = ensure_extension(preset_name, ".cmpreset")
    src_file = cm_dir / cmpreset_filename
    if not src_file.is_file():
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            from agent.config import get_config as get_agent_config
            cfg = get_agent_config()
            emit(LogLevel.ERROR, LogCategory.PRESET, f"Shifting preset not found: {cmpreset_filename}", rig_id=cfg.agent_id, event_code="PRESET_SHIFTING_MISSING", details={"path": str(src_file), "preset": preset_name})
        except Exception:
            pass
        raise HTTPException(status_code=404, detail=f"Preset not found: {cmpreset_filename} (path: {src_file})")
    try:
        data = json.loads(src_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.exception("Read .cmpreset failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Cannot read preset: {e}")
    logger.debug("assists preset=%s raw_json=%s", preset_name, data)
    flat = _extract_assists_data(data)
    content = cmpreset_to_assists_ini(data, preset_name=preset_name)
    try:
        ac_cfg.mkdir(parents=True, exist_ok=True)
        assists_ini.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.exception("Write assists.ini failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Cannot write assists.ini: {e}")
    ok, validation_errors = verify_assists_ini_after_write(assists_ini, flat)
    if not ok and validation_errors:
        logger.error(
            "assists.ini verification failed for preset=%s: %s",
            preset_name,
            validation_errors,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "assists_ini_verification_failed", "errors": validation_errors},
        )
    logger.info("assists.ini verification passed for preset=%s", preset_name)
    status = get_process_status()
    requires_restart = status.get("ac_running", False)
    return {
        "success": True,
        "message": f"Applied assists preset {preset_name}",
        "requires_restart": requires_restart,
    }


def _update_race_ini_driver_name(race_ini_path: Path, driver_name: str) -> None:
    """Update [CAR_0] DRIVER_NAME and [REMOTE] NAME in race.ini. Preserves file structure."""
    if not race_ini_path.exists():
        raise FileNotFoundError(f"race.ini not found: {race_ini_path}")
    lines = race_ini_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    section: str = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            out.append(line)
            continue
        if section == "CAR_0" and stripped.upper().startswith("DRIVER_NAME="):
            out.append(f"DRIVER_NAME={driver_name}")
            continue
        if section == "REMOTE" and stripped.upper().startswith("NAME="):
            out.append(f"NAME={driver_name}")
            continue
        out.append(line)
    race_ini_path.write_text("\n".join(out) + "\n", encoding="utf-8")


@router.post("/set_driver_name", dependencies=[Depends(verify_token)])
async def set_driver_name(request: SetDriverNameRequest):
    """Update driver name in race.ini ([CAR_0] DRIVER_NAME and [REMOTE] NAME)."""
    from agent.config import get_config, get_controls_ini_dir
    config = get_config()
    cfg_dir = get_controls_ini_dir(config)
    if not cfg_dir:
        raise HTTPException(
            status_code=503,
            detail="cfg path not configured (set ac_cfg or ac_savedsetups)",
        )
    race_ini = Path(cfg_dir) / "race.ini"
    driver_name = (request.driver_name or "").strip()
    try:
        _update_race_ini_driver_name(race_ini, driver_name)
        _write_display_name(driver_name)
        return {"success": True, "message": f"Driver name set to {driver_name!r}"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to update race.ini: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply", dependencies=[Depends(verify_token)])
async def apply_kiosk(envelope: dict):
    """
    Unified kiosk apply: accept envelope (schema_version, source, target, intent, selection).
    intent.action: preview_only | apply_only | apply_and_launch | stop_session.
    Returns: request_id, ok, status (idle|configuring|launching|running|error), applied, warnings, errors.
    """
    try:
        result = apply_unified(envelope)
        return result
    except Exception as e:
        logger.exception("apply failed: %s", e)
        request_id = (envelope.get("request_id") or "").strip()
        return {
            "request_id": request_id,
            "ok": False,
            "status": "error",
            "applied": {},
            "warnings": [],
            "errors": [{"code": "APPLY_FAILED", "message": str(e)}],
        }


def _reset_steering_sync(preset_name: str) -> list:
    """Run steering preset apply in thread; returns list of error strings."""
    from agent.process_manager import _apply_steering_preset
    errs = []
    try:
        _apply_steering_preset(preset_name)
    except ValueError as e:
        errs.append(f"steering: {e}")
    return errs


def _reset_shifting_sync(cm_dir_raw: Optional[str], ac_cfg_raw: Optional[str], shifting_preset: str) -> list:
    """Run shifting preset apply in thread; returns list of error strings."""
    errs = []
    if not cm_dir_raw or not ac_cfg_raw:
        errs.append("shifting not configured (cm_assists_presets_dir/cm_assists_presets and ac_cfg_dir/ac_cfg)")
        return errs
    cm_dir = Path(cm_dir_raw)
    ac_cfg = Path(ac_cfg_raw)
    assists_ini = ac_cfg / "assists.ini"
    cmpreset_filename = ensure_extension(shifting_preset, ".cmpreset")
    src_file = cm_dir / cmpreset_filename
    if not src_file.is_file():
        errs.append(f"shifting preset not found: {cmpreset_filename} (path: {src_file})")
        return errs
    try:
        data = json.loads(src_file.read_text(encoding="utf-8"))
        flat = _extract_assists_data(data)
        content = cmpreset_to_assists_ini(data, preset_name=shifting_preset)
        ac_cfg.mkdir(parents=True, exist_ok=True)
        assists_ini.write_text(content, encoding="utf-8")
        ok, verification_errors = verify_assists_ini_after_write(assists_ini, flat)
        if not ok and verification_errors:
            logger.error("assists.ini verification failed for preset=%s: %s", shifting_preset, verification_errors)
            errs.extend(verification_errors)
    except (OSError, json.JSONDecodeError) as e:
        errs.append(f"shifting: {e}")
    return errs


@router.post("/reset_rig", dependencies=[Depends(verify_token)])
async def reset_rig(request: ResetRigRequest):
    """
    Apply default steering, shifting, and display name without launching acs.exe.
    Steering and shifting are applied in parallel to reduce latency.
    If acs.exe is running, apply anyway and return requires_restart=true.
    """
    from agent.config import get_config, get_controls_ini_dir, get_assists_presets_dir
    from agent.process_manager import get_process_status

    config = get_config()
    status = get_process_status()
    ac_running = status.get("ac_running", False)

    steering_preset = _validate_steering_shifting_agent((request.steering_preset or "Race").strip())
    shifting_preset = _validate_steering_shifting_agent((request.shifting_preset or "H-Pattern").strip())
    display_name = (request.display_name or "").strip() or _default_display_name()

    cm_dir_raw = get_assists_presets_dir(config)
    ac_cfg_raw = get_controls_ini_dir(config)
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=2) as pool:
        steering_fut = loop.run_in_executor(pool, _reset_steering_sync, steering_preset)
        shifting_fut = loop.run_in_executor(pool, _reset_shifting_sync, cm_dir_raw, ac_cfg_raw, shifting_preset)
        err_steering, err_shifting = await asyncio.gather(steering_fut, shifting_fut)
    errors = err_steering + err_shifting

    # Persist display_name and update race.ini (fast, keep sequential)
    try:
        _write_display_name(display_name)
        cfg_dir = get_controls_ini_dir(config)
        if cfg_dir:
            race_ini = Path(cfg_dir) / "race.ini"
            if race_ini.exists():
                _update_race_ini_driver_name(race_ini, display_name)
    except Exception as e:
        errors.append(f"display_name: {e}")

    message = "Rig reset applied."
    if errors:
        message += " Skipped: " + "; ".join(errors)
    if ac_running:
        message += " Changes will take effect on next launch."
    return {"success": True, "message": message.strip(), "requires_restart": ac_running}


class HotkeyRequest(BaseModel):
    """Request to send a hotkey to AC (employee control)."""
    action: str  # "toggle_manual" | "back_to_pits"


@router.post("/hotkey", dependencies=[Depends(verify_token)])
async def send_hotkey(request: HotkeyRequest):
    """Send Ctrl+G (toggle AUTO/MANUAL) or Ctrl+P (back to pits) to AC. Employee control."""
    from agent.hotkey import send_toggle_manual, send_back_to_pits, get_control_mode
    action = (request.action or "").strip().lower()
    if action == "toggle_manual":
        ok = send_toggle_manual()
        mode = get_control_mode()
        if ok:
            return {"success": True, "message": f"Sent Ctrl+G; mode is {mode}", "control_mode": mode}
        return {"success": False, "message": "Failed to send hotkey (Windows only)"}
    if action == "back_to_pits":
        ok = send_back_to_pits()
        if ok:
            return {"success": True, "message": "Sent Ctrl+P"}
        return {"success": False, "message": "Failed to send hotkey (Windows only)"}
    raise HTTPException(status_code=400, detail="action must be toggle_manual or back_to_pits")

@router.post("/update", dependencies=[Depends(verify_token)])
async def trigger_update():
    """Check for latest release on GitHub and launch PitBoxUpdater.exe if an update is available."""
    from agent.update_check import check_for_update, launch_pitbox_updater
    from pitbox_common.version import __version__ as CURRENT_VERSION

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, check_for_update)

    current = CURRENT_VERSION
    latest = result.get("latest_version") or current

    if result.get("error") and not result.get("latest_version"):
        return {
            "success": False,
            "update_available": False,
            "current_version": current,
            "latest_version": None,
            "message": result.get("error") or "Update check failed",
        }

    if not result.get("update_available"):
        return {
            "success": True,
            "update_available": False,
            "current_version": current,
            "latest_version": latest,
            "message": f"Already up to date ({current})",
        }

    installer_url = result.get("installer_url") or ""
    installer_sha256 = result.get("installer_sha256") or ""

    if not installer_url:
        return {
            "success": False,
            "update_available": True,
            "current_version": current,
            "latest_version": latest,
            "message": "Update available but no installer asset found in release",
        }

    launched = await loop.run_in_executor(
        None, launch_pitbox_updater, installer_url, latest, installer_sha256
    )
    if launched:
        return {
            "success": True,
            "update_available": True,
            "current_version": current,
            "latest_version": latest,
            "message": f"Updater launched: {current} → {latest}",
        }
    return {
        "success": False,
        "update_available": True,
        "current_version": current,
        "latest_version": latest,
        "message": "Update available but PitBoxUpdater.exe not found on this machine",
    }

@router.post("/close-display", dependencies=[Depends(verify_token)])
async def close_display_endpoint():
    """Kill the Chrome/Edge kiosk window that was launched for the sim display."""
    from agent.sim_display import close_display
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, close_display)
    return result


@router.post("/launch-mumble", dependencies=[Depends(verify_token)])
async def launch_mumble_endpoint():
    """Launch the Mumble desktop client on this sim PC."""
    from agent.mumble_client import launch_mumble
    from agent.config import get_config
    cfg = get_config()
    mumble_exe = getattr(cfg, "mumble_exe_path", None) or None
    server_url = getattr(cfg, "mumble_server_url", None) or None
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: launch_mumble(mumble_exe, server_url))
    return result


@router.post("/close-mumble", dependencies=[Depends(verify_token)])
async def close_mumble_endpoint():
    """Close the Mumble desktop client on this sim PC."""
    from agent.mumble_client import close_mumble
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, close_mumble)
    return result


@router.post("/self-update", dependencies=[Depends(verify_token)])
async def self_update_endpoint(request: Request):
    """
    Download a new PitBoxAgent.exe from the given URL and replace this agent.
    The agent launches a background PowerShell script to do the swap while this
    process is still running, then exits so the script can copy the new binary.
    Body: {"url": "http://controller-ip:9630/api/agent/download"}
    """
    import sys
    import tempfile
    import urllib.request
    from pathlib import Path

    body = await request.json()
    url = (body or {}).get("url", "").strip()
    if not url:
        return {"success": False, "message": "Missing 'url' in request body"}

    if sys.platform != "win32":
        return {"success": False, "message": "Self-update only supported on Windows"}

    exe_path = Path(r"C:\PitBox\PitBoxAgent.exe")
    update_path = Path(r"C:\PitBox\PitBoxAgent_update.exe")

    def _download_and_schedule():
        try:
            import urllib.request as _req
            _req.urlretrieve(url, str(update_path))
        except Exception as e:
            return {"success": False, "message": f"Download failed: {e}"}

        ps_script = (
            "Start-Sleep -Seconds 3\r\n"
            "taskkill /F /IM PitBoxAgent.exe 2>$null\r\n"
            "Start-Sleep -Seconds 2\r\n"
            f"Copy-Item '{update_path}' '{exe_path}' -Force\r\n"
            "$t = Get-ScheduledTask | Where-Object { $_.TaskName -like '*PitBox*' -or $_.TaskName -like '*Agent*' } | Select-Object -First 1\r\n"
            "if ($t) { Start-ScheduledTask -TaskName $t.TaskName } else { Start-Process 'C:\\PitBox\\PitBoxAgent.exe' }\r\n"
            f"Remove-Item '{update_path}' -Force -ErrorAction SilentlyContinue\r\n"
        )

        import tempfile, subprocess as _sp
        ps_path = Path(tempfile.gettempdir()) / "pitbox_agent_update.ps1"
        ps_path.write_text(ps_script, encoding="utf-8")

        _sp.Popen(
            ["powershell.exe", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", str(ps_path)],
            close_fds=True,
        )
        return {"success": True, "message": "Update downloaded — agent restarting in ~5 seconds"}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_and_schedule)


@router.post("/launch-display", dependencies=[Depends(verify_token)])
async def launch_display_endpoint():
    """Launch Chrome/Edge in kiosk fullscreen mode pointing at the controller /sim page."""
    from agent.sim_display import launch_display
    from agent.config import get_config
    from agent.identity import get_device_id
    try:
        from agent.pairing import is_paired, get_controller_url as _get_ctrl_url
        ctrl_url = (_get_ctrl_url() if is_paired() else None)
    except Exception:
        ctrl_url = None
    cfg = get_config()
    if not ctrl_url:
        ctrl_url = getattr(cfg, "controller_url", None) or ""
    if not ctrl_url:
        return {"success": False, "message": "No controller_url configured or paired"}
    try:
        agent_id = get_device_id()
    except Exception:
        agent_id = getattr(cfg, "agent_id", "unknown")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: launch_display(ctrl_url, agent_id))
    return result
