"""
Shared preset / favourites / INI helpers and live AC server queries.

Used by api_routes, api_server_config_routes, and related code without importing api_routes.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from controller.ac_paths import _cars_dir, _content_root
from controller.cm_favourites import load_favourites_servers
from controller.config import get_ac_server_presets_root, get_config, list_server_preset_ids
from controller.ini_io import read_ini
from pitbox_common.safe_inputs import validate_ac_server_preset_folder_name

logger = logging.getLogger(__name__)

# Temporary hardcode for verification: AC server presets folder (do not rely on config/env).
PRESETS_DIR_DEBUG = Path(r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\server\presets")

# Content Manager / CSP: exclude from car and track catalogs and skin lists.
STFOLDER_NAME = ".stfolder"

_presets_scan_logged = False


def discover_presets(presets_dir: Path) -> list[str]:
    """
    List all preset folder names (directories only). No regex or name filter; server_id equals folder name exactly.
    Sorted. Logs: [presets] root=, found=, accepted=.
    """
    global _presets_scan_logged
    root = Path(presets_dir).resolve()
    found: list[str] = []
    if root.is_dir():
        found = sorted(p.name for p in root.iterdir() if p.is_dir())
    accepted = list(found)
    logger.info("[presets] root=%s", root)
    logger.info("[presets] found=%s", found)
    logger.info("[presets] accepted=%s", accepted)
    if not _presets_scan_logged:
        _presets_scan_logged = True
    return accepted


def _valid_server_id(server_id: str) -> bool:
    if not server_id:
        return False
    s = (server_id or "").strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        return False
    return True


def _validate_server_preset_folder_name_http(name: str) -> str:
    try:
        return validate_ac_server_preset_folder_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _get_presets_root() -> Path:
    """Presets root: hardcoded debug path if it exists, else config."""
    if PRESETS_DIR_DEBUG.is_dir():
        return PRESETS_DIR_DEBUG
    return get_ac_server_presets_root()


def _preset_ini_paths(preset_dir: Path) -> tuple[Path, Path]:
    """
    Return (server_cfg.ini path, entry_list.ini path) with Option B layout.
    Primary: <preset_folder>/server_cfg.ini and entry_list.ini
    Fallback: <preset_folder>/cfg/server_cfg.ini and entry_list.ini (backwards compatible).
    """
    sc_primary = preset_dir / "server_cfg.ini"
    el_primary = preset_dir / "entry_list.ini"
    sc_fallback = preset_dir / "cfg" / "server_cfg.ini"
    el_fallback = preset_dir / "cfg" / "entry_list.ini"
    sc_path = sc_primary if sc_primary.exists() else sc_fallback
    el_path = el_primary if el_primary.exists() else el_fallback
    return sc_path, el_path


def _get_server_preset_dir_safe(server_id: str) -> Path:
    """Return preset directory for server_id. Validates server_id to prevent path traversal."""
    if not _valid_server_id(server_id):
        raise HTTPException(status_code=400, detail="Invalid server_id")
    root = _get_presets_root()
    name = server_id.strip() if (server_id or "").strip() != "default" else "SERVER_01"
    return root / name


def _get_server_join_host() -> str:
    """Host for join when server runs on Admin PC: config server_host, or detected LAN IP, else 127.0.0.1."""
    try:
        cfg = get_config()
        if getattr(cfg, "server_host", None) and str(cfg.server_host).strip():
            return str(cfg.server_host).strip()
    except Exception:
        pass
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def _parse_server_section(server_ini: dict) -> tuple[dict[str, str], str | None]:
    """
    Extract [SERVER] section from parsed INI (case-insensitive; works with __CM_* sections).
    Returns (opts_upper, None) or ({}, error_message) if [SERVER] missing or TCP_PORT/UDP_PORT invalid.
    """
    server_opts: dict[str, str] = {}
    for sect, opts in server_ini.items():
        if sect.upper() == "SERVER":
            server_opts = {str(k).upper(): (v or "").strip() for k, v in opts.items()}
            break
    if not server_opts:
        return {}, "missing [SERVER] section"
    tcp_s = (server_opts.get("TCP_PORT") or "").strip()
    udp_s = (server_opts.get("UDP_PORT") or "").strip()
    if not tcp_s and not udp_s:
        return {}, "TCP_PORT and UDP_PORT missing or empty in [SERVER]"
    tcp_port: int | None = None
    udp_port: int | None = None
    if tcp_s.isdigit() and 1 <= int(tcp_s) <= 65535:
        tcp_port = int(tcp_s)
    if udp_s.isdigit() and 1 <= int(udp_s) <= 65535:
        udp_port = int(udp_s)
    if tcp_port is None and udp_port is None:
        return {}, "TCP_PORT and UDP_PORT unparseable or out of range (1-65535)"
    return server_opts, None


def parse_ac_server_cfg(cfg_path: Path) -> dict[str, Any] | None:
    """
    Parse preset server_cfg.ini and return { tcp_port, udp_port, http_port, name, ip }.
    TCP_PORT and UDP_PORT required; HTTP_PORT, NAME, IP optional. IP is NOT required.
    Returns None only if [SERVER] missing or TCP_PORT/UDP_PORT missing/unparseable.
    """
    if not cfg_path.exists():
        return None
    try:
        server_ini = read_ini(cfg_path)
    except Exception:
        return None
    server_opts, parse_err = _parse_server_section(server_ini)
    if parse_err:
        return None
    tcp_s = (server_opts.get("TCP_PORT") or "").strip()
    udp_s = (server_opts.get("UDP_PORT") or "").strip()
    http_s = (server_opts.get("HTTP_PORT") or "").strip()
    name_s = (server_opts.get("NAME") or "").strip()
    ip_s = (server_opts.get("LISTEN_IP") or server_opts.get("IP") or "").strip() or None
    tcp_port = int(tcp_s) if tcp_s.isdigit() and 1 <= int(tcp_s) <= 65535 else None
    udp_port = int(udp_s) if udp_s.isdigit() and 1 <= int(udp_s) <= 65535 else None
    http_port: int | None = int(http_s) if http_s.isdigit() and 1 <= int(http_s) <= 65535 else None
    if tcp_port is None and udp_port is None:
        return None
    return {
        "tcp_port": tcp_port or udp_port,
        "udp_port": udp_port or tcp_port,
        "http_port": http_port,
        "name": name_s or "",
        "ip": ip_s,
    }


def _is_favourite_server_id(server_id: str) -> bool:
    """True if server_id is an ip:port-style favourite id."""
    if not server_id or ":" not in (server_id or ""):
        return False
    s = (server_id or "").strip()
    host, _, port_s = s.rpartition(":")
    return bool(host and port_s and port_s.isdigit() and 1 <= int(port_s) <= 65535)


def _get_favourite_by_id(server_id: str) -> Optional[dict[str, Any]]:
    """Return favourite entry for server_id (ip:port) or None."""
    if not _is_favourite_server_id(server_id):
        return None
    for f in load_favourites_servers():
        if f.get("server_id") == server_id:
            return f
    return None


def get_merged_server_ids() -> list[str]:
    """
    Merge preset server ids and Content Manager favourites.
    Order: presets first, then CM favourites. Dedupes by (ip, port):
    if a preset already has that address, the favourite entry is skipped.
    """
    if PRESETS_DIR_DEBUG.is_dir():
        preset_ids = discover_presets(PRESETS_DIR_DEBUG)
    else:
        preset_ids = list_server_preset_ids()
    if not preset_ids:
        preset_ids = ["SERVER_01"]
    preset_addrs: set[tuple[str, int]] = set()
    try:
        for sid in preset_ids:
            try:
                preset_dir = _get_server_preset_dir_safe(sid)
                sc_path, _ = _preset_ini_paths(preset_dir)
                if sc_path.exists():
                    parsed = parse_ac_server_cfg(sc_path)
                    if parsed:
                        host = (parsed.get("ip") or _get_server_join_host() or "").strip()
                        port = parsed.get("tcp_port") or parsed.get("udp_port")
                        if host and port is not None:
                            preset_addrs.add((host, int(port)))
            except Exception:
                pass
    except Exception:
        pass
    favourites = load_favourites_servers()
    favourite_ids: list[str] = []
    for f in favourites:
        addr = (f.get("ip") or "", int(f.get("port") or 0))
        if addr not in preset_addrs:
            favourite_ids.append(f["server_id"])
    return list(preset_ids) + favourite_ids


_live_server_cache: dict[tuple[str, int], tuple[float, bool, dict[str, Any]]] = {}
_LIVE_SERVER_CACHE_TTL_SEC = 20.0
_LIVE_SERVER_FAIL_CACHE_TTL_SEC = 5.0
_LIVE_SERVER_TIMEOUT_SEC = 3.0


def _split_combined_track_layout(combined: str) -> tuple[str, str]:
    s = (combined or "").strip()
    if not s or "-" not in s:
        return (s, "")
    parts = s.rsplit("-", 1)
    if len(parts) != 2:
        return (s, "")
    base, layout = parts[0].strip(), parts[1].strip()
    if not base or not layout:
        logger.debug("[live-server] combined track split ambiguous: %r -> base=%r layout=%r", s, base, layout)
        return (s, "")
    return (base, layout)


def _parse_ac_live_info(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cars": [],
        "track": {},
        "track_id": "",
        "layout": "",
        "name": "",
        "game_port": None,
    }
    if not isinstance(data, dict):
        return out
    out["name"] = (data.get("name") or data.get("serverName") or data.get("server_name") or "").strip()
    track_raw = (data.get("track") or data.get("trackName") or data.get("track_name") or "").strip()
    config_raw = (data.get("config") or data.get("configTrack") or data.get("config_track") or data.get("layout") or "").strip()
    if track_raw:
        if config_raw:
            out["track_id"] = track_raw
            out["layout"] = config_raw
            out["track"] = {"id": track_raw, "config": config_raw, "name": track_raw}
        else:
            base, layout = _split_combined_track_layout(track_raw)
            if layout:
                out["track_id"] = base
                out["layout"] = layout
                out["track"] = {"id": base, "config": layout, "name": track_raw}
                logger.debug("[live-server] split combined track: raw=%r -> track_id=%r layout=%r", track_raw, base, layout)
            else:
                out["track_id"] = track_raw
                out["layout"] = ""
                out["track"] = {"id": track_raw, "config": "", "name": track_raw}
    cars: list[str] = []
    car_list = data.get("cars") or data.get("carList") or data.get("car_list") or data.get("entries")
    if isinstance(car_list, list):
        for c in car_list:
            if isinstance(c, str) and c.strip():
                cars.append(c.strip())
            elif isinstance(c, dict):
                model = (c.get("model") or c.get("MODEL") or c.get("car") or c.get("name") or "").strip()
                if model:
                    cars.append(model)
    elif isinstance(data.get("CARS"), str):
        cars = [x.strip() for x in data["CARS"].split(";") if x.strip()]
    out["cars"] = list(dict.fromkeys(cars))
    port_val = data.get("port") or data.get("tcp_port") or data.get("tcpPort") or data.get("udp_port") or data.get("udpPort") or data.get("listen_port")
    if port_val is not None:
        try:
            out["game_port"] = int(port_val)
        except (TypeError, ValueError):
            pass
    return out


def get_live_server_info(ip: str, port: int) -> dict[str, Any]:
    """
    Query a remote AC server directly for live metadata (track, cars, layout, ports).
    Uses HTTP /INFO; tries seed port, port+1, and 8080.
    """
    global _live_server_cache
    ip = (ip or "").strip()
    if not ip or port < 1 or port > 65535:
        return {
            "cars": [], "track": {}, "track_id": "", "layout": "", "name": "",
            "game_port": port, "http_port": None, "error": "invalid_params",
        }
    key = (ip, port)
    now = time.time()
    if key in _live_server_cache:
        ts, success, data = _live_server_cache[key]
        ttl = _LIVE_SERVER_CACHE_TTL_SEC if success else _LIVE_SERVER_FAIL_CACHE_TTL_SEC
        if now - ts <= ttl:
            logger.debug("[live-server] cache hit %s:%s game_port=%s cars=%d", ip, port, data.get("game_port"), len(data.get("cars") or []))
            return dict(data)
    logger.debug("[live-server] query start %s:%s (discovery)", ip, port)
    candidates = [port, port + 1, 8080]
    for http_port in candidates:
        if http_port < 1 or http_port > 65535:
            continue
        url = f"http://{ip}:{http_port}/INFO"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=_LIVE_SERVER_TIMEOUT_SEC) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                parsed = _parse_ac_live_info(data)
                parsed["http_port"] = http_port
                if parsed.get("game_port") is None:
                    parsed["game_port"] = port
                _live_server_cache[key] = (now, True, dict(parsed))
                logger.info(
                    "[live-server] success %s discovery_port=%s game_port=%s http_port=%s cars=%d track=%s layout=%s",
                    ip, port, parsed.get("game_port"), http_port, len(parsed.get("cars") or []),
                    parsed.get("track_id") or "—", parsed.get("layout") or "(none)",
                )
                return parsed
        except urllib.error.HTTPError as e:
            logger.debug("[live-server] %s:%s HTTP %s", ip, http_port, e.code)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("[live-server] %s:%s error=%s", ip, http_port, e)
    fail_data = {
        "cars": [], "track": {}, "track_id": "", "layout": "", "name": "",
        "game_port": port, "http_port": None, "error": "unreachable",
    }
    _live_server_cache[key] = (now, False, fail_data)
    logger.info("[live-server] failed %s:%s (tried %s); join will use seed port %s", ip, port, candidates, port)
    return fail_data


def _normalize_track_id_from_preset(track_raw: str) -> str:
    """
    Normalize TRACK from preset server_cfg.ini into the actual AC track folder id for content/tracks lookup.
    Handles CSP-style virtual paths like 'csp/3749/../H/../lilski_watkins_glen' -> 'lilski_watkins_glen'.
    """
    s = (track_raw or "").strip().replace("\\", "/")
    if "/" in s:
        s = s.split("/")[-1]
    s = s.strip().strip("_")
    if s and re.match(r"^[A-Za-z0-9_.-]+$", s):
        return s
    return ""


def _build_favourite_server_cfg_snapshot(fav: dict[str, Any], server_id: str) -> dict[str, Any]:
    """
    Build server_cfg-shaped snapshot for a favourite (Content Manager–style).
    Seed from Favourites.txt (ip, port, name); resolve live via get_live_server_info.
    """
    host = (fav.get("ip") or "").strip() or ""
    seed_port = int(fav.get("port") or 0)
    name = (fav.get("name") or "").strip() or server_id
    live = get_live_server_info(host, seed_port) if host and seed_port else {}
    game_port = live.get("game_port") if live.get("game_port") is not None else seed_port
    http_port_val = live.get("http_port")
    cars_list = list(live.get("cars") or [])
    track_from_live = (live.get("track") or {}).get("id") or live.get("track_id") or ""
    track_id = _normalize_track_id_from_preset(track_from_live) or track_from_live
    layout_raw = (live.get("track") or {}).get("config") or live.get("layout") or ""
    raw_resolved_track = (live.get("track") or {}).get("name") or live.get("track_id") or ""
    if live.get("name"):
        name = (live.get("name") or "").strip() or name
    cars_str = ";".join(cars_list) if cars_list else ""
    server_section: dict[str, str] = {
        "NAME": name or server_id,
        "TCP_PORT": str(game_port),
        "UDP_PORT": str(game_port),
        "IP": host,
        "CARS": cars_str,
        "TRACK": track_id,
    }
    if layout_raw:
        server_section["CONFIG_TRACK"] = layout_raw
    if http_port_val is not None:
        server_section["HTTP_PORT"] = str(http_port_val)
    snapshot: dict[str, Any] = {"SERVER": server_section}
    logger.info(
        "[live-server] race.ini favourite %s: raw_track=%r normalized_track=%r normalized_layout=%r -> TRACK=%r CONFIG_TRACK=%s",
        server_id, raw_resolved_track or "(none)", track_id or "(none)", layout_raw or "(none)",
        server_section.get("TRACK") or "(none)", server_section.get("CONFIG_TRACK") or "(omit)",
    )
    return snapshot


def _prettify_car_id(car_id: str) -> str:
    """Prettify car_id for display when ui_car.json name is missing."""
    if not car_id or not (s := car_id.strip()):
        return ""
    s = re.sub(r"[-_]+", " ", s)
    acronyms = {"gt3", "gt4", "f1", "bmw", "amg", "tcr", "ks", "cup", "nx"}
    words = []
    for w in s.split():
        if not w:
            continue
        low = w.lower()
        if low in acronyms and len(w) <= 5:
            words.append(w.upper() if len(w) <= 3 else w[:1].upper() + w[1:].lower())
        elif w.isdigit() or (len(w) >= 2 and w[:-1].isdigit() and w[-1].isalpha()):
            words.append(w)
        else:
            words.append(w[:1].upper() + w[1:].lower() if len(w) > 1 else w.upper())
    return " ".join(words)


def _parse_ui_car_json(path: Path) -> dict:
    """Parse ui_car.json; return dict with name, class, bhp, weight, topspeed (optional keys)."""
    out = {"name": "", "class": "", "bhp": None, "weight": None, "topspeed": None}
    if not path.exists():
        return out
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return out
    if not isinstance(data, dict):
        return out
    out["name"] = (
        data.get("name") or data.get("screenName") or data.get("uiName") or data.get("displayName") or ""
    )
    out["class"] = data.get("class") or data.get("brand") or ""
    specs = data.get("specs")
    if isinstance(specs, dict):
        out["bhp"] = specs.get("bhp") or specs.get("power")
        out["weight"] = specs.get("weight")
        out["topspeed"] = specs.get("topspeed") or specs.get("speed")
    return out


def _get_car_display_name(car_id: str) -> str:
    """Return display name from content/cars/<car_id>/ui/ui_car.json (same path as car list)."""
    if not car_id or ".." in car_id or "/" in car_id or "\\" in car_id or car_id.strip() == STFOLDER_NAME:
        return ""
    raw = car_id.strip()
    if not raw:
        return ""
    cars_dir = _cars_dir()
    ui_car = cars_dir / raw / "ui" / "ui_car.json"
    if not ui_car.is_file():
        content_cars = _content_root() / "content" / "cars"
        if content_cars != cars_dir and content_cars.is_dir():
            ui_car = content_cars / raw / "ui" / "ui_car.json"
    meta = _parse_ui_car_json(ui_car)
    name = (meta.get("name") or "").strip()
    return name if name else _prettify_car_id(car_id)


_PRESET_DISK_STATE_CACHE_TTL = 5.0
_preset_disk_state_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _invalidate_preset_disk_state_cache(server_id: Optional[str] = None) -> None:
    """Drop disk_state cache for one preset id, or clear all."""
    global _preset_disk_state_cache
    if server_id is not None and str(server_id).strip():
        _preset_disk_state_cache.pop(str(server_id).strip(), None)
    else:
        _preset_disk_state_cache.clear()


def _get_cached_preset_disk_state(preset_id: str) -> Optional[dict[str, Any]]:
    entry = _preset_disk_state_cache.get(preset_id)
    if not entry:
        return None
    ts, payload = entry
    if (time.time() - ts) > _PRESET_DISK_STATE_CACHE_TTL:
        del _preset_disk_state_cache[preset_id]
        return None
    return payload


def _set_cached_preset_disk_state(preset_id: str, payload: dict[str, Any]) -> None:
    _preset_disk_state_cache[preset_id] = (time.time(), copy.deepcopy(payload))
