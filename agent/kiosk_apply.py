"""
Kiosk unified apply: accept envelope (selection + intent), write race.ini, apply assists, launch acs.exe or aclink join.
"""
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Tuple


def parse_join_addr(join_addr: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Parse join address string "ip:port" (e.g. "192.168.1.218:9616") into (ip, port).
    Returns (None, None) if invalid.
    """
    s = (join_addr or "").strip()
    if not s or ":" not in s:
        return None, None
    ip_part, _, port_part = s.rpartition(":")
    ip = ip_part.strip()
    if not ip:
        return None, None
    try:
        port = int(port_part.strip())
        if 1 <= port <= 65535:
            return ip, port
    except ValueError:
        pass
    return None, None

from agent.config import get_config, get_controls_ini_dir, get_preset_dir, get_assists_presets_dir
from agent.process_manager import (
    get_status as get_ac_status,
    start_process,
    stop_process,
    _apply_steering_preset,
)
from agent.utils.files import ensure_extension
from agent.utils.cmpreset import (
    cmpreset_to_assists_ini,
    _extract_assists_data,
    verify_assists_ini_after_write,
)

logger = logging.getLogger(__name__)


def _write_race_ini(
    race_ini_path: Path,
    *,
    track_id: str,
    layout_id: str,
    car_id: str,
    skin_id: str = "default",
    driver_name: str = "Driver",
    mode: str = "singleplayer",
    server_ip: Optional[str] = None,
    server_port: Optional[int] = None,
    server_name: Optional[str] = None,
) -> None:
    """Write minimal race.ini for singleplayer or online (RACE, CAR_0, REMOTE)."""
    race_ini_path.parent.mkdir(parents=True, exist_ok=True)
    layout = (layout_id or "").strip() or "default"
    skin = (skin_id or "").strip() or "default"
    lines = [
        "[RACE]",
        f"TRACK={track_id}",
        f"CONFIG_TRACK={layout}",
        f"MODEL={car_id}",
        "",
        "[CAR_0]",
        f"MODEL={car_id}",
        f"SKIN={skin}",
        f"DRIVER_NAME={driver_name}",
        "",
        "[REMOTE]",
        "ACTIVE=1" if mode == "online" else "ACTIVE=0",
    ]
    if mode == "online" and (server_ip or server_port):
        if server_name:
            lines.append(f"SERVER_NAME={server_name}")
        if server_ip:
            lines.append(f"SERVER_IP={server_ip}")
        if server_port is not None:
            lines.append(f"SERVER_PORT={server_port}")
    race_ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote race.ini track=%s layout=%s car=%s mode=%s", track_id, layout, car_id, mode)


def _read_race_ini_encoding(path: Path) -> Tuple[list[Tuple[str, list[Tuple[str, str]]]], str]:
    """
    Read race.ini preserving section order. Returns (sections, encoding_used).
    sections = [ (section_name, [(key, value), ...]), ... ]
    """
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            sections: list[Tuple[str, list[Tuple[str, str]]]] = []
            current: list[Tuple[str, str]] = []
            section_name = ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    if section_name or current:
                        sections.append((section_name, current))
                    section_name = stripped[1:-1].strip().upper()
                    current = []
                    continue
                if section_name and "=" in stripped:
                    key, _, val = stripped.partition("=")
                    key_stripped = key.strip()
                    if key_stripped:
                        current.append((key_stripped, val.strip()))
            if section_name or current:
                sections.append((section_name, current))
            return sections, enc
        except (UnicodeDecodeError, LookupError, OSError):
            continue
    return [], "utf-8"


def _update_race_ini_remote(
    race_ini_path: Path,
    *,
    server_ip: str,
    server_port: int,
    server_name: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """
    Ensure race.ini has [REMOTE] with ACTIVE=1, SERVER_IP, SERVER_PORT, SERVER_NAME (optional), PASSWORD (optional).
    Preserves other sections and other keys in [REMOTE]. Creates file with minimal [RACE]/[CAR_0]/[REMOTE] if missing.
    """
    remote_keys = {
        "ACTIVE": "1",
        "SERVER_IP": server_ip,
        "SERVER_PORT": str(server_port),
    }
    if server_name is not None and (str(server_name).strip() or ""):
        remote_keys["SERVER_NAME"] = str(server_name).strip()
    if password is not None and (str(password).strip() or ""):
        remote_keys["PASSWORD"] = str(password).strip()

    if race_ini_path.exists():
        sections, _ = _read_race_ini_encoding(race_ini_path)
    else:
        race_ini_path.parent.mkdir(parents=True, exist_ok=True)
        sections = [
            ("RACE", [("TRACK", "unknown"), ("CONFIG_TRACK", "default"), ("MODEL", "unknown")]),
            ("CAR_0", [("MODEL", "unknown"), ("SKIN", "default"), ("DRIVER_NAME", "Driver")]),
        ]

    # Build new REMOTE key list: our keys first (in order), then any existing key not overwritten
    our_keys_upper = {k.upper() for k in remote_keys}
    new_remote: list[Tuple[str, str]] = [
        ("ACTIVE", remote_keys["ACTIVE"]),
        ("SERVER_IP", remote_keys["SERVER_IP"]),
        ("SERVER_PORT", remote_keys["SERVER_PORT"]),
    ]
    if "SERVER_NAME" in remote_keys:
        new_remote.append(("SERVER_NAME", remote_keys["SERVER_NAME"]))
    if "PASSWORD" in remote_keys:
        new_remote.append(("PASSWORD", remote_keys["PASSWORD"]))

    found_remote = False
    new_sections: list[Tuple[str, list[Tuple[str, str]]]] = []
    for name, pairs in sections:
        if name == "REMOTE":
            found_remote = True
            for k, v in pairs:
                if k.upper() not in our_keys_upper:
                    new_remote.append((k, v))
            new_sections.append(("REMOTE", new_remote))
        else:
            new_sections.append((name, pairs))

    if not found_remote:
        new_sections.append(("REMOTE", new_remote))

    # If file was empty, ensure we have at least [RACE] and [CAR_0] so AC is happy
    if not new_sections:
        new_sections = [
            ("RACE", [("TRACK", "unknown"), ("CONFIG_TRACK", "default"), ("MODEL", "unknown")]),
            ("CAR_0", [("MODEL", "unknown"), ("SKIN", "default"), ("DRIVER_NAME", "Driver")]),
            ("REMOTE", new_remote),
        ]
    elif not any(s[0] == "REMOTE" for s in new_sections):
        new_sections.append(("REMOTE", new_remote))

    lines: list[str] = []
    for name, pairs in new_sections:
        lines.append(f"[{name}]")
        for k, v in pairs:
            lines.append(f"{k}={v}")
        lines.append("")
    race_ini_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _verify_and_log_remote(race_ini_path: Path) -> None:
    """Re-read race.ini and log [REMOTE] ACTIVE, SERVER_IP, SERVER_PORT."""
    try:
        from agent.race_ini import _read_ini
        ini = _read_ini(race_ini_path)
        remote = ini.get("REMOTE") or {}
        active = remote.get("ACTIVE", "")
        sip = remote.get("SERVER_IP", "")
        sport = remote.get("SERVER_PORT", "")
        logger.info("[remote-verify] path='%s' ACTIVE=%s SERVER_IP=%s SERVER_PORT=%s", race_ini_path, active, sip, sport)
    except Exception as e:
        logger.warning("Could not verify race.ini [REMOTE]: %s", e)


def _update_race_ini_car(
    race_ini_path: Path,
    *,
    car_id: str,
    skin_id: str = "default",
) -> None:
    """
    Update race.ini [RACE] MODEL, [CAR_0] MODEL and SKIN, [REMOTE] REQUESTED_CAR.
    Preserves all other sections and keys. Creates minimal file if missing.
    """
    car = (car_id or "").strip() or "unknown"
    skin = (skin_id or "").strip() or "default"
    race_keys_upper = {"MODEL"}
    car0_keys_upper = {"MODEL", "SKIN"}
    remote_requested_key = "REQUESTED_CAR"

    if race_ini_path.exists():
        sections, _ = _read_race_ini_encoding(race_ini_path)
    else:
        race_ini_path.parent.mkdir(parents=True, exist_ok=True)
        sections = [
            ("RACE", [("TRACK", "unknown"), ("CONFIG_TRACK", "default"), ("MODEL", car)]),
            ("CAR_0", [("MODEL", car), ("SKIN", skin), ("DRIVER_NAME", "Driver")]),
            ("REMOTE", [("ACTIVE", "0")]),
        ]

    new_sections: list[Tuple[str, list[Tuple[str, str]]]] = []
    for name, pairs in sections:
        if name == "RACE":
            new_pairs = [("MODEL", car)]
            for k, v in pairs:
                if k.upper() not in race_keys_upper:
                    new_pairs.append((k, v))
            new_sections.append(("RACE", new_pairs))
        elif name == "CAR_0":
            new_pairs = [("MODEL", car), ("SKIN", skin)]
            for k, v in pairs:
                if k.upper() not in car0_keys_upper:
                    new_pairs.append((k, v))
            new_sections.append(("CAR_0", new_pairs))
        elif name == "REMOTE":
            new_pairs = []
            for k, v in pairs:
                if k.upper() == remote_requested_key:
                    new_pairs.append((k, car))
                else:
                    new_pairs.append((k, v))
            if not any(p[0].upper() == remote_requested_key for p in new_pairs):
                new_pairs.append((remote_requested_key, car))
            new_sections.append(("REMOTE", new_pairs))
        else:
            new_sections.append((name, pairs))

    # Ensure RACE and CAR_0 exist if file was empty
    has_race = any(s[0] == "RACE" for s in new_sections)
    has_car0 = any(s[0] == "CAR_0" for s in new_sections)
    has_remote = any(s[0] == "REMOTE" for s in new_sections)
    if not has_race:
        new_sections.insert(0, ("RACE", [("TRACK", "unknown"), ("CONFIG_TRACK", "default"), ("MODEL", car)]))
    if not has_car0:
        idx = 1 if has_race else 0
        new_sections.insert(idx, ("CAR_0", [("MODEL", car), ("SKIN", skin), ("DRIVER_NAME", "Driver")]))
    if not has_remote:
        new_sections.append(("REMOTE", [("ACTIVE", "0"), ("REQUESTED_CAR", car)]))

    lines = []
    for name, pairs in new_sections:
        lines.append(f"[{name}]")
        for k, v in pairs:
            lines.append(f"{k}={v}")
        lines.append("")
    race_ini_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("[race-ini-car] path='%s' MODEL=%s SKIN=%s REQUESTED_CAR=%s", race_ini_path, car, skin, car)


def _apply_shifting_preset(preset_name: str) -> list[str]:
    """
    Apply shifting/assists from .cmpreset by name only (writes assists.ini).
    Returns list of error messages (empty if ok).
    """
    errors = []
    config = get_config()
    cm_dir_raw = get_assists_presets_dir(config)
    ac_cfg_raw = get_controls_ini_dir(config)
    if not cm_dir_raw or not ac_cfg_raw:
        errors.append("assists presets dir not configured")
        return errors
    cm_dir = Path(cm_dir_raw)
    ac_cfg = Path(ac_cfg_raw)
    assists_ini = ac_cfg / "assists.ini"
    name = (preset_name or "").strip()
    if not name:
        return errors
    cmpreset_filename = ensure_extension(name, ".cmpreset")
    src_file = cm_dir / cmpreset_filename
    if not src_file.is_file():
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            emit(LogLevel.ERROR, LogCategory.PRESET, f"Shifting preset not found: {cmpreset_filename}", rig_id=get_config().agent_id, event_code="PRESET_SHIFTING_MISSING", details={"path": str(src_file), "preset": preset_name})
        except Exception:
            pass
        errors.append(f"assists preset not found: {cmpreset_filename}")
        return errors
    try:
        data = json.loads(src_file.read_text(encoding="utf-8"))
        logger.debug("assists preset=%s raw_json=%s", name, data)
        flat = _extract_assists_data(data)
        content = cmpreset_to_assists_ini(data, preset_name=name)
        ac_cfg.mkdir(parents=True, exist_ok=True)
        assists_ini.write_text(content, encoding="utf-8")
        ok, verification_errors = verify_assists_ini_after_write(assists_ini, flat)
        if not ok and verification_errors:
            logger.error("assists.ini verification failed for preset=%s: %s", name, verification_errors)
            errors.extend(verification_errors)
    except (OSError, json.JSONDecodeError) as e:
        errors.append(f"assists: {e}")
    return errors


def _apply_assists_preset(preset_id: str) -> list[str]:
    """
    Apply assists by preset_id: steering (savedsetup_file) + shifting (.cmpreset with same stem).
    Returns list of error messages (empty if ok).
    """
    errors = []
    config = get_config()
    # Resolve preset name: catalog uses race/drift/kids; agent uses Race.ini / Race.cmpreset
    preset_name = (preset_id or "race").strip()
    if preset_name.lower() == "race":
        preset_name = "Race"
    elif preset_name.lower() == "drift":
        preset_name = "Drift"
    elif preset_name.lower() == "kids":
        preset_name = "Kids"
    # Steering from savedsetups (e.g. Race.ini)
    try:
        _apply_steering_preset(preset_name)
    except ValueError as e:
        err_str = str(e)
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            emit(LogLevel.ERROR, LogCategory.PRESET, err_str, rig_id=get_config().agent_id, event_code="PRESET_STEERING_MISSING", details={"preset": preset_name})
        except Exception:
            pass
        errors.append(f"steering: {e}")
    # Shifting/assists from .cmpreset
    errs = _apply_shifting_preset(preset_name)
    errors.extend(errs)
    return errors


def _update_race_ini_driver_name(race_ini_path: Path, driver_name: str) -> None:
    """Update [CAR_0] DRIVER_NAME and [REMOTE] NAME in race.ini. Preserves file structure."""
    if not race_ini_path.exists():
        return
    lines = race_ini_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    section = ""
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


def _launch_ac(aclink_url: Optional[str] = None) -> tuple[bool, Optional[int], str]:
    """
    Launch acs.exe. If aclink_url is set (e.g. aclink://join/192.168.1.50:9600), pass as argument for online join.
    Returns (success, pid, message).
    """
    config = get_config()
    acs_exe = Path(config.paths.acs_exe)
    if not acs_exe.exists():
        return False, None, f"acs.exe not found: {acs_exe}"
    running, pid, _ = get_ac_status()
    if running:
        return True, pid, "Already running"
    try:
        args = [str(acs_exe)]
        if aclink_url and (aclink_url := (aclink_url or "").strip()):
            args.append(aclink_url)
            logger.info("Launching AC with aclink: %s", aclink_url)
        proc = subprocess.Popen(
            args,
            cwd=acs_exe.parent,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
        )
        logger.info("AC started PID=%s", proc.pid)
        return True, proc.pid, f"Started PID {proc.pid}"
    except Exception as e:
        logger.exception("Launch failed: %s", e)
        return False, None, str(e)


def apply_unified(envelope: dict[str, Any]) -> dict[str, Any]:
    """
    Execute unified kiosk payload. intent.action: preview_only | apply_only | apply_and_launch | stop_session.
    Returns standard response: request_id, ok, status, applied, warnings, errors.
    """
    request_id = (envelope.get("request_id") or "").strip() or None
    selection = envelope.get("selection") or {}
    intent = envelope.get("intent") or {}
    action = (intent.get("action") or "").strip() or "apply_only"
    target = envelope.get("target") or {}
    agent_id = (target.get("agent_id") or "").strip()

    status = "idle"
    applied: dict[str, Any] = {}
    warnings: list[dict] = []
    errors: list[dict] = []

    if action == "stop_session":
        success, msg = stop_process()
        status = "idle"
        return {
            "request_id": request_id,
            "ok": success,
            "status": status,
            "applied": applied,
            "warnings": warnings,
            "errors": errors if not success else [],
        }

    mode = selection.get("mode") or {}
    kind = (mode.get("kind") or "singleplayer").strip().lower()
    submode = (mode.get("submode") or "").strip().lower()
    car = selection.get("car") or {}
    track = selection.get("track") or {}
    assists = selection.get("assists") or {}
    server = selection.get("server") or {}
    preset_id = (assists.get("preset_id") or "race").strip()
    # Optional per-sim steering/shifting (same as sim cards); fallback to preset_id mapping
    _pid_lower = preset_id.lower()
    _assist_steering = "Race" if _pid_lower == "race" else "Drift" if _pid_lower == "drift" else "Kids" if _pid_lower == "kids" else "Race"
    _assist_shifting = "Race" if _pid_lower == "race" else "Drift" if _pid_lower == "drift" else "Kids" if _pid_lower == "kids" else "Race"
    steering_preset_name = (selection.get("steering_preset") or "").strip() or _assist_steering
    shifting_preset_name = (selection.get("shifting_preset") or "").strip() or _assist_shifting
    car_id = (car.get("car_id") or "").strip()
    skin_id = (car.get("skin_id") or "default").strip()
    track_id = (track.get("track_id") or "").strip()
    layout_id = (track.get("layout_id") or "default").strip()
    driver_name = (selection.get("driver_name") or "").strip() or "Driver"

    config = get_config()
    cfg_dir = get_controls_ini_dir(config)
    if not cfg_dir:
        return {
            "request_id": request_id,
            "ok": False,
            "status": "error",
            "applied": {},
            "warnings": warnings,
            "errors": [{"code": "CONFIG", "message": "cfg path not configured"}],
        }
    race_ini = Path(cfg_dir) / "race.ini"

    if action == "preview_only":
        return {
            "request_id": request_id,
            "ok": True,
            "status": "paired",
            "applied": {},
            "warnings": warnings,
            "errors": [],
        }

    status = "configuring"
    # Build address for online: prefer join_addr string "ip:port", then address_override, then address
    join_addr_raw = (server.get("join_addr") or "").strip() if isinstance(server.get("join_addr"), str) else None
    server_ip = (server.get("address_override") or {}).get("host") if isinstance(server.get("address_override"), dict) else None
    server_port = (server.get("address_override") or {}).get("port") if isinstance(server.get("address_override"), dict) else None
    if (server_ip is None or server_port is None) and join_addr_raw:
        parsed_ip, parsed_port = parse_join_addr(join_addr_raw)
        if parsed_ip is not None and parsed_port is not None:
            server_ip = parsed_ip
            server_port = parsed_port
    if not server_ip and isinstance(server.get("address"), dict):
        server_ip = (server.get("address") or {}).get("host")
        server_port = (server.get("address") or {}).get("port") if server_port is None else server_port
    server_id = (server.get("server_id") or "").strip()
    server_name = server_id  # or from catalog
    server_password = (server.get("password") or "").strip() or None if isinstance(server.get("password"), str) else None

    if kind == "online":
        join_addr_display = join_addr_raw or (f"{server_ip}:{server_port}" if server_ip and server_port is not None else None)
        if join_addr_display:
            logger.info("[server-addr] join_addr='%s'", join_addr_display)
        if not server_ip and server_id:
            warnings.append({"code": "ONLINE_CAR_SELECTION_BEST_EFFORT", "message": "Server address may be from catalog; car selection is best-effort."})
        if not server_ip:
            server_ip = "127.0.0.1"
        if server_port is None:
            server_port = 9600
    else:
        server_ip = None
        server_port = None

    if kind == "online":
        # Online: ALWAYS sync race.ini from server_cfg preset. No fallback to remote-only.
        if server_ip and server_port is not None:
            server_cfg = server.get("server_cfg_snapshot") if isinstance(server.get("server_cfg_snapshot"), dict) else None
            preset_name = (server.get("preset_name") or "").strip() or None
            if not server_cfg:
                logger.error("SKIPPING FULL SYNC: snapshot missing (server_cfg_snapshot required for online join)")
                try:
                    from agent.service.event_emitter import emit
                    from agent.common.event_log import LogCategory, LogLevel
                    emit(LogLevel.ERROR, LogCategory.SERVER, "Server preset config missing; cannot sync race.ini", rig_id=get_config().agent_id, event_code="SYNC_REQUIRED", details={"message": "Ensure controller has preset server_cfg.ini."})
                except Exception:
                    pass
                errors.append({"code": "SYNC_REQUIRED", "message": "Server preset config missing; cannot sync race.ini. Ensure controller has preset server_cfg.ini."})
                return {
                    "request_id": request_id,
                    "ok": False,
                    "status": "error",
                    "applied": {"car_id": car_id, "track_id": track_id, "layout_id": layout_id, "assists_preset_id": preset_id, "mode_kind": kind, "mode_submode": submode},
                    "warnings": warnings,
                    "errors": errors,
                }
            else:
                from agent.server_cfg_sync import sync_race_ini_from_server_cfg
                global_pwd = server.get("global_server_password")
                if global_pwd is not None and isinstance(global_pwd, str):
                    global_pwd = global_pwd.strip() or None
                else:
                    global_pwd = None
                try:
                    sync_race_ini_from_server_cfg(
                        server_cfg, server_ip, server_port, car_id or None, race_ini,
                        preset_name=preset_name, global_password=global_pwd,
                    )
                except ValueError as ve:
                    logger.error("Online join aborted: %s", ve)
                    errors.append({"code": "TRACK_UNRESOLVABLE", "message": str(ve)})
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "status": "error",
                        "applied": {"car_id": car_id, "track_id": track_id, "layout_id": layout_id, "assists_preset_id": preset_id, "mode_kind": kind, "mode_submode": submode},
                        "warnings": warnings,
                        "errors": errors,
                    }
                _verify_and_log_remote(race_ini)
                try:
                    _update_race_ini_driver_name(race_ini, driver_name)
                except Exception as e:
                    logger.warning("Could not set driver name in race.ini after sync: %s", e)
    else:
        # Singleplayer: write full minimal race.ini (overwrite)
        _write_race_ini(
            race_ini,
            track_id=track_id or "unknown",
            layout_id=layout_id,
            car_id=car_id or "unknown",
            skin_id=skin_id,
            driver_name=driver_name,
            mode="singleplayer",
            server_ip=None,
            server_port=None,
            server_name=None,
        )
    applied = {
        "car_id": car_id,
        "track_id": track_id,
        "layout_id": layout_id,
        "assists_preset_id": preset_id,
        "mode_kind": kind,
        "mode_submode": submode,
    }

    # Apply steering (savedsetups) and shifting (.cmpreset) — same as sim cards
    try:
        _apply_steering_preset(steering_preset_name)
    except ValueError as e:
        warnings.append({"code": "ASSISTS_APPLY", "message": f"steering: {e}"})
    for e in _apply_shifting_preset(shifting_preset_name):
        warnings.append({"code": "ASSISTS_APPLY", "message": e})

    if action != "apply_and_launch":
        return {
            "request_id": request_id,
            "ok": True,
            "status": "idle",
            "applied": applied,
            "warnings": warnings,
            "errors": errors,
        }

    status = "launching"
    if kind == "online" and server_ip:
        aclink_url = f"aclink://join/{server_ip}:{server_port or 9600}"
        success, pid, message = _launch_ac(aclink_url)
    else:
        success, pid, message = start_process(steering_preset=steering_preset_name)

    if not success:
        status = "error"
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            emit(LogLevel.ERROR, LogCategory.SESSION, f"AC launch failed: {message}", rig_id=get_config().agent_id, event_code="LAUNCH_FAILED", details={"message": message})
        except Exception:
            pass
        errors.append({"code": "LAUNCH_FAILED", "message": message})
        return {
            "request_id": request_id,
            "ok": False,
            "status": status,
            "applied": applied,
            "warnings": warnings,
            "errors": errors,
        }
    status = "running"
    if kind == "online":
        warnings.append({"code": "ONLINE_CAR_SELECTION_BEST_EFFORT", "message": "Car selection for online is best-effort; server may override."})
    return {
        "request_id": request_id,
        "ok": True,
        "status": status,
        "applied": applied,
        "warnings": warnings,
        "errors": [],
    }


