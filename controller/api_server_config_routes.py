"""Server INI read/write, blacklist, and acServer.exe lifecycle routes (mounted on main ``/api`` router)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from controller.ac_paths import (
    _cfg_dir_for_server,
    _server_config_paths,
    _server_config_paths_for_read,
    _server_root,
)
from controller.server_preset_helpers import (
    _build_favourite_server_cfg_snapshot,
    _get_car_display_name,
    _get_favourite_by_id,
    _get_server_preset_dir_safe,
    _invalidate_preset_disk_state_cache,
    _normalize_track_id_from_preset,
    _preset_ini_paths,
    _validate_server_preset_folder_name_http,
    _valid_server_id,
    get_merged_server_ids,
    parse_ac_server_cfg,
)
from controller.common.event_log import LogCategory as EventLogCategory, LogLevel as EventLogLevel, make_event as make_log_event
from controller.config import get_ac_server_presets_root
from controller.ini_io import get_file_revision, read_ini, write_ini_atomic, _ini_value
from controller.operator_auth import require_operator, require_operator_if_password_configured
from controller.service.event_store import append_event as event_store_append
from controller.timing.constants import (
    TIMING_UDP_PLUGIN_ADDRESS,
    TIMING_UDP_PLUGIN_LOCAL_PORT,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_AC_SERVER_EXE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\server\acServer.exe")


@dataclass
class ServerInstance:
    """One running acServer.exe process (multi-instance model)."""

    server_id: str
    preset_path: Path
    udp_port: int
    tcp_port: int
    http_port: int
    proc: subprocess.Popen
    status: str
    started_at: Optional[float] = None

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None


_running_servers: dict[str, ServerInstance] = {}
_running_servers_lock = threading.Lock()


def _acserver_log_path(server_id: str) -> Path:
    """Path to acServer console log file for this server_id (controller logs dir)."""
    safe_id = re.sub(r'[<>:"/\\|?*\s]', "_", (server_id or "default").strip()) or "default"
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"acserver_{safe_id}.log"


def _ensure_preset_cfg_for_ac_server(preset_dir: Path) -> None:
    """Ensure preset_dir/cfg/server_cfg.ini and entry_list.ini exist."""
    sc_path, el_path = _preset_ini_paths(preset_dir)
    cfg_dir = preset_dir / "cfg"
    cfg_sc = cfg_dir / "server_cfg.ini"
    cfg_el = cfg_dir / "entry_list.ini"
    if sc_path.exists():
        cfg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sc_path, cfg_sc)
    if el_path.exists():
        cfg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(el_path, cfg_el)


def _read_ports_from_preset(preset_dir: Path) -> tuple[int, int, int]:
    """Read UDP_PORT, TCP_PORT, HTTP_PORT from preset's server_cfg.ini."""
    sc_path, _ = _preset_ini_paths(preset_dir)
    if not sc_path.exists():
        raise HTTPException(
            status_code=404,
            detail="server_cfg.ini not found in preset: " + str(sc_path),
        )
    data = read_ini(sc_path)
    server_section = None
    for sect, opts in data.items():
        if sect.upper() == "SERVER":
            server_section = opts
            break
    if server_section is None:
        raise HTTPException(
            status_code=400,
            detail="[SERVER] section missing in server_cfg.ini",
        )
    udp, tcp, http = 9600, 9600, 8081
    for k, v in server_section.items():
        if k.upper() == "UDP_PORT":
            try:
                udp = int(str(v).strip())
            except ValueError:
                pass
        elif k.upper() == "TCP_PORT":
            try:
                tcp = int(str(v).strip())
            except ValueError:
                pass
        elif k.upper() == "HTTP_PORT":
            try:
                http = int(str(v).strip())
            except ValueError:
                pass
    return udp, tcp, http


_PORT_CHECK_TIMEOUT = 0.1


def _is_port_in_use(port: int, kind: str = "tcp") -> bool:
    try:
        if kind == "tcp":
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(_PORT_CHECK_TIMEOUT)
                s.bind(("127.0.0.1", port))
                return False
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(_PORT_CHECK_TIMEOUT)
                s.bind(("127.0.0.1", port))
                return False
    except OSError:
        return True


def _check_ports_available(udp_port: int, tcp_port: int, http_port: int) -> Optional[str]:
    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_udp = ex.submit(_is_port_in_use, udp_port, "udp")
        fut_tcp = ex.submit(_is_port_in_use, tcp_port, "tcp")
        fut_http = ex.submit(_is_port_in_use, http_port, "tcp")
        results["udp"] = fut_udp.result()
        results["tcp"] = fut_tcp.result()
        results["http"] = fut_http.result()
    if results["udp"]:
        return "UDP port %s is already in use" % udp_port
    if results["tcp"]:
        return "TCP port %s is already in use" % tcp_port
    if results["http"]:
        return "HTTP port %s is already in use" % http_port
    return None


def _server_root_for_ac_server(server_id: str) -> Optional[Path]:
    root = _server_root()
    if root and (root / "acServer.exe").exists():
        return root
    if DEFAULT_AC_SERVER_EXE.exists():
        return DEFAULT_AC_SERVER_EXE.parent
    return None


def _get_running_servers_list() -> list[dict[str, Any]]:
    with _running_servers_lock:
        dead = []
        for sid, inst in list(_running_servers.items()):
            if inst.proc.poll() is not None:
                dead.append(sid)
        for sid in dead:
            del _running_servers[sid]
        return [
            {
                "server_id": inst.server_id,
                "preset_path": str(inst.preset_path),
                "udp_port": inst.udp_port,
                "tcp_port": inst.tcp_port,
                "http_port": inst.http_port,
                "pid": inst.pid,
                "status": "crashed" if inst.proc.poll() is not None else inst.status,
                "started_at": inst.started_at,
            }
            for inst in _running_servers.values()
        ]


def _ac_server_start(server_id: str) -> dict:
    if not _valid_server_id(server_id):
        raise HTTPException(status_code=400, detail="Invalid server_id")
    preset_path = _get_server_preset_dir_safe(server_id)
    if not preset_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Preset directory does not exist: " + str(preset_path),
        )
    server_root = _server_root_for_ac_server(server_id)
    if not server_root:
        raise HTTPException(
            status_code=404,
            detail="AC server path not configured and default exe not found: " + str(DEFAULT_AC_SERVER_EXE),
        )
    exe = server_root / "acServer.exe"
    if not exe.exists():
        raise HTTPException(status_code=404, detail="acServer.exe not found in server root.")
    with _running_servers_lock:
        existing = _running_servers.get(server_id)
        if existing is not None:
            if existing.proc.poll() is None:
                return {
                    "success": True,
                    "message": "Already running",
                    "pid": existing.pid,
                    "udp_port": existing.udp_port,
                    "tcp_port": existing.tcp_port,
                    "http_port": existing.http_port,
                    "preset_path": str(existing.preset_path),
                    "started_at": existing.started_at,
                }
            del _running_servers[server_id]
    udp_port, tcp_port, http_port = _read_ports_from_preset(preset_path)
    with _running_servers_lock:
        for sid, inst in _running_servers.items():
            if inst.proc.poll() is not None:
                continue
            if (inst.udp_port, inst.tcp_port, inst.http_port) == (udp_port, tcp_port, http_port):
                raise HTTPException(
                    status_code=400,
                    detail="Ports UDP %s / TCP %s / HTTP %s already in use by server '%s'" % (udp_port, tcp_port, http_port, sid),
                )
    port_err = _check_ports_available(udp_port, tcp_port, http_port)
    if port_err:
        raise HTTPException(status_code=400, detail=port_err)
    _ensure_preset_cfg_for_ac_server(preset_path)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    log_path = _acserver_log_path(server_id)
    try:
        with open(log_path, "ab") as log_file:
            proc = subprocess.Popen(
                [str(exe)],
                cwd=str(preset_path),
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        started_at = time.time()
        instance = ServerInstance(
            server_id=server_id,
            preset_path=preset_path,
            udp_port=udp_port,
            tcp_port=tcp_port,
            http_port=http_port,
            proc=proc,
            status="running",
            started_at=started_at,
        )
        with _running_servers_lock:
            _running_servers[server_id] = instance
        logger.info("Started acServer.exe pid=%s cwd=%s (server %s)", proc.pid, preset_path, server_id)
        try:
            from controller.server_control.adapter import get_adapter as _get_admin_adapter
            _get_admin_adapter().invalidate_target(server_id)
        except Exception:
            pass
        return {
            "success": True,
            "message": "Started",
            "pid": proc.pid,
            "udp_port": udp_port,
            "tcp_port": tcp_port,
            "http_port": http_port,
            "preset_path": str(preset_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to start acServer.exe")
        raise HTTPException(status_code=500, detail=str(e))


def _ac_server_stop(server_id: str) -> dict:
    with _running_servers_lock:
        instance = _running_servers.pop(server_id, None)
    # Drop any cached UDP-admin target so the next start re-resolves
    # the port from the (possibly edited) server_cfg.ini. Best-effort:
    # never let adapter errors block the stop sequence.
    try:
        from controller.server_control.adapter import get_adapter as _get_admin_adapter
        _get_admin_adapter().invalidate_target(server_id)
    except Exception:
        pass
    if not instance:
        return {"success": True, "message": "Not running"}
    proc = instance.proc
    pid = proc.pid
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    except Exception as e:
        logger.warning("Stop acServer pid=%s: %s", pid, e)
    logger.info("Stopped acServer.exe pid=%s for server_id=%s", pid, server_id)
    return {"success": True, "message": "Stopped"}


_SESSION_OFF_NAMES = {"BOOK": "__CM_BOOK_OFF", "PRACTICE": "__CM_PRACTICE_OFF", "QUALIFY": "__CM_QUALIFY_OFF", "RACE": "__CM_RACE_OFF"}

_PRESET_LIST_CACHE_TTL = 15.0
_preset_list_cache: Optional[tuple[float, list[str], dict[str, str]]] = None

_SERVER_CONFIG_FULL_CACHE_TTL_SEC = 10.0
_server_config_full_cache: dict[str, tuple[float, dict]] = {}


def _invalidate_server_config_full_cache(server_id: Optional[str] = None) -> None:
    global _server_config_full_cache
    if server_id:
        _server_config_full_cache.pop(server_id, None)
    else:
        _server_config_full_cache.clear()


def _section_key_match(data: dict, name: str) -> Optional[str]:
    u = name.upper()
    for k in data:
        if k.upper() == u:
            return k
    return None


def _normalize_session_sections(data: dict[str, dict[str, str]]) -> None:
    for base_name, off_name in _SESSION_OFF_NAMES.items():
        normal_key = _section_key_match(data, base_name)
        off_key = _section_key_match(data, off_name)
        section_key = normal_key or off_key
        if not section_key:
            continue
        opts = data[section_key]
        is_open_key = next((k for k in opts if k.upper() == "IS_OPEN"), None)
        is_open_val = opts.get(is_open_key, "1") if is_open_key else "1"
        is_open = str(is_open_val).strip() in ("1", "true", "yes")
        if is_open:
            if off_key:
                data[base_name] = dict(opts)
                del data[off_key]
        else:
            if normal_key:
                data[off_name] = dict(opts)
                del data[normal_key]
                if base_name == "QUALIFY":
                    qk = _section_key_match(data, "QUALIFYING")
                    if qk:
                        del data[qk]


def _apply_ini_updates(
    data: dict[str, dict[str, str]],
    updates: list[dict],
) -> None:
    section_map = {s.upper(): s for s in data}
    for u in updates:
        sect = (u.get("section") or "").strip()
        key = (u.get("key") or "").strip()
        if not sect or not key:
            continue
        val_str = _ini_value(u.get("value"))
        sect_upper = sect.upper()
        if sect_upper in section_map:
            existing_section = section_map[sect_upper]
            opts = data[existing_section]
            key_map = {k.upper(): k for k in opts}
            if key.upper() in key_map:
                opts[key_map[key.upper()]] = val_str
            else:
                opts[key] = val_str
        else:
            data[sect] = data.get(sect, {})
            data[sect][key] = val_str
            section_map[sect_upper] = sect
    _normalize_session_sections(data)


def _ensure_preset_list_cache() -> tuple[list[str], dict[str, str]]:
    global _preset_list_cache
    now = time.time()
    if _preset_list_cache is not None:
        cache_ts, cache_ids, cache_names = _preset_list_cache
        if (now - cache_ts) <= _PRESET_LIST_CACHE_TTL:
            return cache_ids, cache_names
        _preset_list_cache = None
    if _preset_list_cache is None:
        server_ids = get_merged_server_ids()
        preset_names: dict[str, str] = {}
        for sid in server_ids:
            fav = _get_favourite_by_id(sid)
            if fav:
                preset_names[sid] = (fav.get("name") or "").strip() or sid
            else:
                try:
                    preset_dir = _get_server_preset_dir_safe(sid)
                    p_sc_path, _ = _preset_ini_paths(preset_dir)
                    if p_sc_path.exists():
                        parsed = parse_ac_server_cfg(p_sc_path)
                        name = (parsed.get("name") or "").strip() if parsed else ""
                        preset_names[sid] = name if name else sid
                    else:
                        preset_names[sid] = sid
                except Exception:
                    preset_names[sid] = sid
        server_ids.sort(key=lambda sid: preset_names.get(sid, sid).strip().lower())
        _preset_list_cache = (now, server_ids, preset_names)
    return _preset_list_cache[1], _preset_list_cache[2]


@router.get("/server-config/raw")
async def get_server_config_raw(server_id: str = "default", _: None = Depends(require_operator_if_password_configured)):
    cfg_dir = _cfg_dir_for_server(server_id)
    sc_path, _ = _server_config_paths(cfg_dir)
    if not sc_path.exists():
        return {}
    data = read_ini(sc_path)
    return data


@router.get("/server-config/revision")
async def get_server_config_revision(server_id: str = "default", _: None = Depends(require_operator_if_password_configured)):
    cfg_dir = _cfg_dir_for_server(server_id)
    sc_path, _ = _server_config_paths(cfg_dir)
    mtime, size = get_file_revision(sc_path)
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)) if mtime else ""
    revision = f"{mtime or 0}_{size or 0}"
    return {"revision": revision, "updated_at": updated_at, "path": str(sc_path)}


class ServerConfigPatchBody(BaseModel):
    server_id: str = "default"
    updates: list[dict]


@router.patch("/server-config")
async def patch_server_config(body: ServerConfigPatchBody, _: None = Depends(require_operator)):
    cfg_dir = _cfg_dir_for_server(body.server_id)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    sc_path, _ = _server_config_paths(cfg_dir)
    data = read_ini(sc_path) if sc_path.exists() else {}
    _apply_ini_updates(data, body.updates)
    write_ini_atomic(sc_path, data)
    _invalidate_preset_disk_state_cache(body.server_id)
    _invalidate_server_config_full_cache(body.server_id)
    return data


@router.get("/server-config/meta")
async def get_server_config_meta(_: None = Depends(require_operator_if_password_configured)):
    server_ids, preset_names = _ensure_preset_list_cache()
    return {"server_ids": server_ids, "preset_names": preset_names}


@router.get("/server-config")
async def get_server_config(server_id: str = "default", _: None = Depends(require_operator_if_password_configured)):
    server_root = _server_root()
    server_ids, preset_names = _ensure_preset_list_cache()
    effective_id = server_id if server_id != "default" else server_ids[0]
    _now = time.time()
    _cached = _server_config_full_cache.get(effective_id)
    if _cached and (_now - _cached[0]) < _SERVER_CONFIG_FULL_CACHE_TTL_SEC:
        return _cached[1]
    cfg_dir = _cfg_dir_for_server(effective_id)
    sc_path, el_path = _server_config_paths_for_read(cfg_dir)
    logger.debug("Read server_cfg.ini for server_id=%s effective_id=%s at %s", server_id, effective_id, sc_path)
    server_cfg_raw = read_ini(sc_path) if sc_path.exists() else {}
    server_cfg = {}
    for sect, opts in server_cfg_raw.items():
        s_key = sect.upper()
        if s_key not in server_cfg:
            server_cfg[s_key] = {k.upper(): v for k, v in opts.items()}
    entry_list_ini = read_ini(el_path) if el_path.exists() else {}
    entry_list_entries = []
    for sect, opts in sorted(entry_list_ini.items(), key=lambda x: x[0]):
        if sect.startswith("CAR_"):
            entry = {k.upper(): str(v) for k, v in opts.items()}
            model = entry.get("MODEL", "").strip()
            if model:
                display_name = _get_car_display_name(model)
                if display_name:
                    entry["CAR_NAME"] = display_name
            entry_list_entries.append(entry)
    fav_online = _get_favourite_by_id(effective_id)
    if fav_online and not server_cfg.get("SERVER"):
        snapshot = _build_favourite_server_cfg_snapshot(fav_online, effective_id)
        server_cfg["SERVER"] = snapshot.get("SERVER") or {}
    srv = server_cfg.get("SERVER", {})
    track_raw = (srv.get("TRACK") or "").strip()
    config_track = (srv.get("CONFIG_TRACK") or "").strip()
    track_id = _normalize_track_id_from_preset(track_raw) or track_raw
    race = {"track_raw": track_raw, "track_id": track_id, "config_track": config_track}
    _payload = {
        "server_id": effective_id,
        "server_ids": server_ids,
        "server_cfg": server_cfg,
        "entry_list": entry_list_entries,
        "race": race,
        "server_root": str(server_root),
        "cfg_dir": str(cfg_dir),
        "server_cfg_path": str(sc_path),
        "presets": server_ids,
        "preset_names": preset_names,
        "blacklist_path": str(server_root / "blacklist.txt"),
        "logs_path": str(server_root / "logs"),
        "content_path": str(server_root / "content"),
    }
    _server_config_full_cache[effective_id] = (time.time(), _payload)
    return _payload


class ServerConfigPutBody(BaseModel):
    server_id: str = "default"
    server_cfg: dict[str, dict[str, str]]
    entry_list: list[dict[str, Any]]


def _ensure_timing_plugin_defaults(server_cfg: dict[str, dict[str, str]]) -> bool:
    """Auto-fill UDP_PLUGIN_LOCAL_PORT / UDP_PLUGIN_ADDRESS in [SERVER] when blank.

    PitBox's native timing engine listens on the standard AC UDP plugin port; AC dedicated servers must forward telemetry to it. We only fill
    the values when they are missing or empty so an operator override is never
    clobbered. Returns True if anything was changed.
    """
    server_key = None
    for k in server_cfg.keys():
        if str(k).upper() == "SERVER":
            server_key = k
            break
    if server_key is None:
        server_key = "SERVER"
        server_cfg[server_key] = {}
    sect = server_cfg[server_key]
    changed = False
    if not str(sect.get("UDP_PLUGIN_LOCAL_PORT", "")).strip():
        sect["UDP_PLUGIN_LOCAL_PORT"] = str(TIMING_UDP_PLUGIN_LOCAL_PORT)
        changed = True
    if not str(sect.get("UDP_PLUGIN_ADDRESS", "")).strip():
        try:
            from controller.config import get_config as _get_cfg
            _adv = (getattr(_get_cfg(), "timing_udp_advertise_address", None) or "").strip()
        except Exception:
            _adv = ""
        sect["UDP_PLUGIN_ADDRESS"] = _adv or TIMING_UDP_PLUGIN_ADDRESS
        changed = True
    return changed


@router.put("/server-config")
async def put_server_config(body: ServerConfigPutBody, _: None = Depends(require_operator)):
    cfg_dir = _cfg_dir_for_server(body.server_id)
    sc_path, el_path = _server_config_paths(cfg_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    seen_models: set[str] = set()
    unique_models: list[str] = []
    for c in body.entry_list:
        model = (c or {}).get("MODEL", "").strip()
        if model and model not in seen_models:
            seen_models.add(model)
            unique_models.append(model)
    cars_value = ";".join(unique_models)
    server_cfg_out = {}
    for sect_name, opts in body.server_cfg.items():
        opts = dict(opts)
        if sect_name.upper() == "SERVER":
            opts["CARS"] = cars_value
        server_cfg_out[sect_name] = opts
    if not any(s.upper() == "SERVER" for s in server_cfg_out):
        server_cfg_out["SERVER"] = {"CARS": cars_value}
    if _ensure_timing_plugin_defaults(server_cfg_out):
        logger.info("Auto-filled UDP_PLUGIN_* defaults for PitBox timing engine in %s", sc_path)
    write_ini_atomic(sc_path, server_cfg_out)
    entry_list_ini: dict[str, dict[str, str]] = {}
    for i, car in enumerate(body.entry_list):
        entry_list_ini[f"CAR_{i}"] = {
            k: _ini_value(v) for k, v in (car or {}).items() if k.upper() != "CAR_NAME"
        }
    write_ini_atomic(el_path, entry_list_ini)
    logger.info(
        "Wrote server_cfg.ini and entry_list.ini to %s (CARS=%d unique, entries=%d)",
        cfg_dir,
        len(unique_models),
        len(entry_list_ini),
    )
    _invalidate_preset_disk_state_cache(body.server_id)
    _invalidate_server_config_full_cache(body.server_id)
    global _preset_list_cache
    _preset_list_cache = None
    try:
        event_store_append(
            make_log_event(
                EventLogLevel.INFO,
                EventLogCategory.SERVER,
                "Controller",
                "Server config saved",
                details={"server_id": body.server_id},
            )
        )
    except Exception:
        pass
    return {"success": True, "message": "Saved"}


class LoadPresetBody(BaseModel):
    server_id: str = "default"
    preset_name: str


@router.post("/server-config/load-preset")
async def load_server_config_preset(body: LoadPresetBody, _: None = Depends(require_operator)):
    safe_src = _validate_server_preset_folder_name_http(body.preset_name)
    presets_root = get_ac_server_presets_root()
    src_dir = presets_root / safe_src
    if not src_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Preset '{safe_src}' not found.")
    dst_dir = _cfg_dir_for_server(body.server_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    sc_src, el_src = src_dir / "server_cfg.ini", src_dir / "entry_list.ini"
    sc_dst, el_dst = dst_dir / "server_cfg.ini", dst_dir / "entry_list.ini"
    if not sc_src.exists() and not el_src.exists():
        raise HTTPException(status_code=404, detail=f"Preset '{safe_src}' has no server_cfg.ini or entry_list.ini.")
    if sc_src.exists():
        data = read_ini(sc_src)
        if _ensure_timing_plugin_defaults(data):
            logger.info("Auto-filled UDP_PLUGIN_* defaults for PitBox timing engine in preset load -> %s", sc_dst)
        write_ini_atomic(sc_dst, data)
    if el_src.exists():
        data = read_ini(el_src)
        write_ini_atomic(el_dst, data)
    _invalidate_preset_disk_state_cache(body.server_id)
    _invalidate_server_config_full_cache(body.server_id)
    return {"success": True, "message": f"Loaded preset '{safe_src}'."}


@router.get("/server-config/blacklist")
async def get_server_blacklist(server_id: str = "default", _: None = Depends(require_operator_if_password_configured)):
    server_root = _server_root()
    path = server_root / "blacklist.txt"
    if not path.exists():
        return {"path": str(path), "lines": []}
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    return {"path": str(path), "lines": lines}


class BlacklistPutBody(BaseModel):
    server_id: str = "default"
    lines: list[str]


@router.put("/server-config/blacklist")
async def put_server_blacklist(body: BlacklistPutBody, _: None = Depends(require_operator)):
    server_root = _server_root()
    path = server_root / "blacklist.txt"
    with open(path, "w", encoding="utf-8") as f:
        for line in body.lines:
            f.write(line.strip() + "\n")
    return {"success": True, "message": "Blacklist saved."}


@router.get("/server-config/process-status")
async def get_server_process_status(_: None = Depends(require_operator_if_password_configured)):
    servers = _get_running_servers_list()
    return {"servers": servers}


@router.get("/server-config/process-log")
async def get_server_process_log(
    server_id: str = "default",
    tail: int = 1000,
    _: None = Depends(require_operator_if_password_configured),
):
    if tail < 1:
        tail = 100
    if tail > 20000:
        tail = 20000
    path = _acserver_log_path(server_id)
    if not path.is_file():
        return JSONResponse(
            content={
                "server_id": server_id,
                "path": str(path),
                "lines": [],
                "message": "No log file yet. Start the server to capture console output.",
            },
            status_code=200,
        )
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        lines = [s.rstrip("\r") for s in text.splitlines()]
        if len(lines) > tail:
            lines = lines[-tail:]
        return {"server_id": server_id, "path": str(path), "lines": lines}
    except OSError as e:
        logger.warning("Read acServer log %s: %s", path, e)
        raise HTTPException(status_code=500, detail="Could not read log file: " + str(e))


class ServerIdBody(BaseModel):
    server_id: str = "default"


class ServerIdsBody(BaseModel):
    server_ids: list[str] = Field(default_factory=list, description="e.g. ['SERVER_01', 'SERVER_02']")


@router.post("/server-config/start")
async def start_ac_server(body: ServerIdBody, _: None = Depends(require_operator)):
    logger.info("POST /server-config/start server_id=%s", body.server_id)
    try:
        result = _ac_server_start(body.server_id)
        logger.info("Start result: %s", result)
        try:
            event_store_append(
                make_log_event(
                    EventLogLevel.INFO,
                    EventLogCategory.SERVER,
                    "Controller",
                    "AC server started",
                    details={"server_id": body.server_id, "pid": result.get("pid")},
                )
            )
        except Exception:
            pass
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Start failed: %s", e)
        try:
            event_store_append(
                make_log_event(
                    EventLogLevel.ERROR,
                    EventLogCategory.SERVER,
                    "Controller",
                    "AC server start failed",
                    details={"server_id": body.server_id, "error": str(e)},
                )
            )
        except Exception:
            pass
        raise


@router.post("/server-config/start-batch")
async def start_ac_servers_batch(body: ServerIdsBody, _: None = Depends(require_operator)):
    if not body.server_ids:
        return {"results": [], "message": "No server_ids provided"}
    server_ids = [s.strip() for s in body.server_ids if s and str(s).strip()]
    if not server_ids:
        return {"results": [], "message": "No valid server_ids"}
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(server_ids), 8)) as ex:
        future_to_id = {ex.submit(_ac_server_start, sid): sid for sid in server_ids}
        for future in as_completed(future_to_id):
            sid = future_to_id[future]
            try:
                result = future.result()
                results.append({"server_id": sid, **result})
                try:
                    event_store_append(
                        make_log_event(
                            EventLogLevel.INFO,
                            EventLogCategory.SERVER,
                            "Controller",
                            "AC server started",
                            details={"server_id": sid, "pid": result.get("pid")},
                        )
                    )
                except Exception:
                    pass
            except HTTPException as e:
                errors.append({"server_id": sid, "detail": e.detail, "status_code": e.status_code})
                try:
                    event_store_append(
                        make_log_event(
                            EventLogLevel.ERROR,
                            EventLogCategory.SERVER,
                            "Controller",
                            "AC server start failed",
                            details={"server_id": sid, "error": str(e.detail)},
                        )
                    )
                except Exception:
                    pass
            except Exception as e:
                errors.append({"server_id": sid, "detail": str(e), "status_code": 500})
                try:
                    event_store_append(
                        make_log_event(
                            EventLogLevel.ERROR,
                            EventLogCategory.SERVER,
                            "Controller",
                            "AC server start failed",
                            details={"server_id": sid, "error": str(e)},
                        )
                    )
                except Exception:
                    pass
    return {"results": results, "errors": errors}


@router.post("/server-config/stop")
async def stop_ac_server(body: ServerIdBody, _: None = Depends(require_operator)):
    result = _ac_server_stop(body.server_id)
    try:
        event_store_append(
            make_log_event(
                EventLogLevel.INFO,
                EventLogCategory.SERVER,
                "Controller",
                "AC server stopped",
                details={"server_id": body.server_id},
            )
        )
    except Exception:
        pass
    return result


@router.post("/server-config/restart")
async def restart_ac_server(body: ServerIdBody, _: None = Depends(require_operator)):
    _ac_server_stop(body.server_id)
    result = _ac_server_start(body.server_id)
    try:
        event_store_append(
            make_log_event(
                EventLogLevel.INFO,
                EventLogCategory.SERVER,
                "Controller",
                "AC server restarted",
                details={"server_id": body.server_id, "pid": result.get("pid")},
            )
        )
    except Exception:
        pass
    return result


def _bring_explorer_window_to_front(path_str: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        time.sleep(0.35)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        SW_RESTORE = 9
        folder_name = Path(path_str).name
        found_hwnd: list = [None]

        def enum_cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if path_str in title or folder_name in title:
                found_hwnd[0] = hwnd
                return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found_hwnd[0] is not None:
            user32.ShowWindow(found_hwnd[0], SW_RESTORE)
            time.sleep(0.05)
            user32.SetForegroundWindow(found_hwnd[0])
    except Exception as e:
        logger.debug("Could not bring Explorer to front: %s", e)


@router.post("/server-config/open-preset-folder")
async def open_preset_folder(body: ServerIdBody, _: None = Depends(require_operator)):
    preset_dir = _get_server_preset_dir_safe(body.server_id)
    if not preset_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Preset folder not found: "
            + str(preset_dir)
            + ". Check presets path in controller config (ac_server_root / ac_server_presets_root).",
        )
    path_str = str(preset_dir.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(path_str)
            _bring_explorer_window_to_front(path_str)
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False)
        else:
            subprocess.run(["xdg-open", path_str], check=False)
    except OSError as e:
        logger.warning("[open-preset-folder] %s: %s", body.server_id, e)
        raise HTTPException(status_code=500, detail="Could not open folder: " + str(e))
    return {"ok": True, "path": path_str}
