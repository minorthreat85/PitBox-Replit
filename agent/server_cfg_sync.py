"""
Sync selected server_cfg.ini preset → Documents\\Assetto Corsa\\cfg\\race.ini.

Policy: PATCH only. We do NOT rebuild the file from scratch. We read existing race.ini,
patch only controlled keys (REMOTE, RACE, DYNAMIC_TRACK), and preserve everything else
exactly (HEADER, OPTIONS, __CM_*, unknown sections/keys and ordering).
"""
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _section_get(ini: dict[str, dict[str, str]], section: str, key: str, default: str = "") -> str:
    """Case-insensitive get from section."""
    for sect_name, opts in ini.items():
        if sect_name.upper() == section.upper():
            for k, v in opts.items():
                if (k or "").upper() == (key or "").upper():
                    return (v or "").strip()
            return default
    return default


def _section_upper(ini: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Normalize to section_upper -> { key_upper: value }."""
    out: dict[str, dict[str, str]] = {}
    for sect, opts in ini.items():
        su = (sect or "").strip().upper()
        if not su:
            continue
        out[su] = {(k or "").strip().upper(): (v or "").strip() for k, v in (opts or {}).items() if (k or "").strip()}
    return out


def _normalize_track(track_raw: str) -> str:
    """Normalize TRACK from preset (e.g. csp/.../ks_red_bull_ring -> ks_red_bull_ring)."""
    s = (track_raw or "").strip().replace("\\", "/")
    if "/" in s:
        s = s.split("/")[-1]
    s = s.strip().strip("_")
    if s and re.match(r"^[A-Za-z0-9_.-]+$", s):
        return s
    return "unknown"


def _parse_cars_list(cars_str: str) -> list[str]:
    """Parse [SERVER].CARS: Assetto Corsa uses semicolon; support comma as fallback. Returns list of car ids."""
    s = (cars_str or "").strip()
    if not s:
        return []
    # AC server_cfg.ini CARS= is semicolon-separated (e.g. "car1;car2;car3")
    if ";" in s:
        parts = [p.strip() for p in s.split(";") if (p or "").strip()]
    else:
        parts = [p.strip() for p in s.split(",") if (p or "").strip()]
    return parts


def _first_car_from_cars(cars_str: str) -> str:
    """Parse [SERVER].CARS; return first car id."""
    parts = _parse_cars_list(cars_str)
    return parts[0] if parts else "unknown"


def _validate_selected_car(selected: str, cars_str: str) -> str:
    """If selected is in cars list, return it; else return first car. Uses same CARS format as AC (semicolon-separated).
    Always returns a single car id (never the full CARS string)."""
    selected = (selected or "").strip()
    if not selected:
        return _first_car_from_cars(cars_str)
    parts = _parse_cars_list(cars_str)
    if not parts:
        return selected
    selected_lower = selected.lower()
    for p in parts:
        if p.lower() == selected_lower:
            return p
    result = parts[0]
    return result


def _single_car_id(value: str) -> str:
    """Ensure value is a single car id: if it contains semicolon (e.g. full CARS list), take the first only."""
    if not value or not (value := (value or "").strip()):
        return "unknown"
    if ";" in value:
        first, _, _ = value.partition(";")
        return (first or "").strip() or "unknown"
    return value


# ----- Ordered INI parse/serialize (preserve section order, key order, unknown keys) -----

def _parse_race_ini_to_sections(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """
    Parse race.ini text into ordered list of (section_name_upper, [(key_orig, value), ...]).
    Preserves section order, key order, and original key strings for patching.
    """
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] = []
    section_name = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section_name or current:
                sections.append((section_name, current))
            section_name = stripped[1:-1].strip().upper()
            current = []
            continue
        if section_name and "=" in stripped:
            key, _, val = stripped.partition("=")
            key_orig = key.strip()
            if key_orig:
                current.append((key_orig, val.strip()))
    if section_name or current:
        sections.append((section_name, current))
    return sections


def _serialize_sections_to_text(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """Serialize sections back to INI text."""
    lines: list[str] = []
    for name, pairs in sections:
        lines.append(f"[{name}]")
        for k, v in pairs:
            lines.append(f"{k}={v}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _patch_remote(
    existing_pairs: list[tuple[str, str]],
    join_ip: str,
    join_port: int,
    server_name: str,
    http_port: str,
    car: str,
    password: str,
) -> list[tuple[str, str]]:
    """
    Build [REMOTE] key list: controlled keys first, preserved keys (GUID, NAME, TEAM, __WSS, etc.),
    then PASSWORD last so it cannot be overwritten by preserve/merge.
    """
    controlled_upper = {"ACTIVE", "SERVER_IP", "SERVER_PORT", "SERVER_NAME", "SERVER_HTTP_PORT", "REQUESTED_CAR", "PASSWORD"}
    out: list[tuple[str, str]] = [
        ("ACTIVE", "1"),
        ("SERVER_IP", join_ip),
        ("SERVER_PORT", str(join_port)),
        ("SERVER_NAME", server_name or ""),
    ]
    if http_port:
        out.append(("SERVER_HTTP_PORT", http_port))
    out.append(("REQUESTED_CAR", car))
    # Preserve all existing keys we don't control (GUID, NAME, TEAM, __WSS, __CM_EXTENDED, etc.)
    for k, v in existing_pairs:
        if k.upper() not in controlled_upper:
            out.append((k, v))
    # PASSWORD last (global only; never leave stale)
    out.append(("PASSWORD", password))
    return out


def _patch_race(
    existing_pairs: list[tuple[str, str]],
    track: str,
    config_track: str,
    car: str,
) -> list[tuple[str, str]]:
    """
    Patch [RACE]: TRACK, CONFIG_TRACK (only when known), MODEL. Add CARS=1, RACE_LAPS=1 only if missing.
    Content Manager–style: do not write CONFIG_TRACK when layout is unknown (omit key).
    """
    override_upper = {"TRACK", "CONFIG_TRACK", "MODEL"}
    out: list[tuple[str, str]] = [("TRACK", track), ("MODEL", car)]
    if (config_track or "").strip():
        out.insert(1, ("CONFIG_TRACK", (config_track or "").strip()))
    has_cars = False
    has_race_laps = False
    for k, v in existing_pairs:
        ku = k.upper()
        if ku in override_upper:
            continue
        if ku == "CARS":
            has_cars = True
        elif ku == "RACE_LAPS":
            has_race_laps = True
        out.append((k, v))
    if not has_cars:
        out.append(("CARS", "1"))
    if not has_race_laps:
        out.append(("RACE_LAPS", "1"))
    return out


def _patch_dynamic_track(
    existing_pairs: list[tuple[str, str]],
    server_cfg_dynamic: dict[str, str],
) -> list[tuple[str, str]]:
    """
    Only update SESSION_START, RANDOMNESS, LAP_GAIN, SESSION_TRANSFER if present in server_cfg.
    Preserve all other keys and their order.
    """
    dt_keys = ["SESSION_START", "RANDOMNESS", "LAP_GAIN", "SESSION_TRANSFER"]
    override: dict[str, str] = {}
    for k in dt_keys:
        v = server_cfg_dynamic.get(k)
        if v is not None:
            override[k.upper()] = str(v).strip()
    out: list[tuple[str, str]] = []
    for k, v in existing_pairs:
        ku = k.upper()
        if ku in override:
            out.append((k, override[ku]))
        else:
            out.append((k, v))
    # Add any of the 4 that weren't in existing
    for k in dt_keys:
        ku = k.upper()
        if ku in override and not any(p[0].upper() == ku for p in existing_pairs):
            out.append((k, override[ku]))
    return out


def patch_race_ini_for_online_join(
    existing_race_ini_text: str,
    server_cfg: dict[str, Any],
    join_ip: str,
    join_port: int,
    selected_car: Optional[str],
    global_password: Optional[str],
) -> str:
    """
    Patch only controlled keys for online join. Preserve everything else exactly.
    Returns patched INI text (caller writes atomically).

    Controlled: [REMOTE] (ACTIVE, SERVER_IP, SERVER_PORT, SERVER_NAME, SERVER_HTTP_PORT, REQUESTED_CAR, PASSWORD),
    [RACE] (TRACK, CONFIG_TRACK, MODEL; CARS/RACE_LAPS only if missing), [DYNAMIC_TRACK] (only if in server_cfg).
    Weather/temps/wind/lighting: preserved (not patched).
    """
    sc = _section_upper(server_cfg) if server_cfg else {}
    server = sc.get("SERVER") or {}
    dynamic = sc.get("DYNAMIC_TRACK") or {}

    cars_str = server.get("CARS") or ""
    car = _single_car_id(_validate_selected_car(selected_car or "", cars_str))
    track_raw = server.get("TRACK") or ""
    config_track = (server.get("CONFIG_TRACK") or "").strip()
    server_name = (server.get("NAME") or "").strip()
    http_port = (server.get("HTTP_PORT") or "").strip()
    # Password: global only (never from preset). Set last in REMOTE so nothing overwrites it.
    if global_password is not None:
        password = (global_password if isinstance(global_password, str) else str(global_password)).strip()
    else:
        password = (server.get("PASSWORD") or "").strip()
    track = _normalize_track(track_raw)

    sections = _parse_race_ini_to_sections(existing_race_ini_text)

    # Build new section list: patch REMOTE, RACE, DYNAMIC_TRACK; pass through everything else
    out_sections: list[tuple[str, list[tuple[str, str]]]] = []
    seen_remote = False
    seen_race = False
    seen_dt = False

    for name, pairs in sections:
        if name == "REMOTE":
            seen_remote = True
            out_sections.append(("REMOTE", _patch_remote(pairs, join_ip, join_port, server_name, http_port, car, password)))
        elif name == "RACE":
            seen_race = True
            out_sections.append(("RACE", _patch_race(pairs, track, config_track, car)))
        elif name == "DYNAMIC_TRACK":
            seen_dt = True
            out_sections.append(("DYNAMIC_TRACK", _patch_dynamic_track(pairs, dynamic)))
        else:
            out_sections.append((name, list(pairs)))

    # If controlled section didn't exist, create it
    if not seen_remote:
        out_sections.append(("REMOTE", _patch_remote([], join_ip, join_port, server_name, http_port, car, password)))
    if not seen_race:
        out_sections.append(("RACE", _patch_race([], track, config_track, car)))
    if not seen_dt and dynamic:
        out_sections.append(("DYNAMIC_TRACK", _patch_dynamic_track([], dynamic)))

    return _serialize_sections_to_text(out_sections)


def _read_race_ini_text(path: Path) -> str:
    """Read race.ini with encoding fallbacks. Returns empty string if file missing."""
    if not path.exists():
        return ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError, OSError):
            continue
    return ""


def _verify_and_log_after_sync(
    race_ini_path: Path,
    *,
    global_password_configured: bool,
    password_source: str = "preset",
) -> None:
    """
    Re-read race.ini and log [REMOTE] and [RACE] key values.
    If PASSWORD_LEN==0 and global password was configured, log ERROR.
    """
    try:
        from agent.race_ini import _read_ini
        ini = _read_ini(race_ini_path)
        remote = ini.get("REMOTE") or {}
        race = ini.get("RACE") or {}
        active = remote.get("ACTIVE", "")
        sip = remote.get("SERVER_IP", "")
        sport = remote.get("SERVER_PORT", "")
        sname = remote.get("SERVER_NAME", "")
        http_port = remote.get("SERVER_HTTP_PORT", "")
        req_car = remote.get("REQUESTED_CAR", "")
        pwd_val = remote.get("PASSWORD", "")
        pwd_len = len(pwd_val)
        logger.info(
            "[sync-write][REMOTE] ACTIVE=%s SERVER_IP=%s SERVER_PORT=%s SERVER_NAME=%s SERVER_HTTP_PORT=%s REQUESTED_CAR=%s PASSWORD_LEN=%s PASSWORD_SOURCE=%s",
            active, sip, sport, sname or "(empty)", http_port or "(empty)", req_car, pwd_len, password_source,
        )
        tr = race.get("TRACK", "")
        ct = race.get("CONFIG_TRACK", "")
        model = race.get("MODEL", "")
        logger.info("[sync-write][RACE] TRACK=%s CONFIG_TRACK=%s MODEL=%s", tr, ct or "(blank)", model)
        if global_password_configured and pwd_len == 0:
            logger.error(
                "global_server_password is configured but [REMOTE].PASSWORD is empty after sync. "
                "Joins may fail. Check that global_password is passed to sync and set last in REMOTE."
            )
    except Exception as e:
        logger.warning("Could not verify race.ini after sync: %s", e)


def sync_race_ini_from_server_cfg(
    server_cfg: dict[str, Any],
    join_ip: str,
    join_port: int,
    selected_car: Optional[str],
    race_ini_path: Path,
    *,
    preset_name: Optional[str] = None,
    global_password: Optional[str] = None,
) -> None:
    """
    Patch race.ini for online join (minimal update). Does NOT rewrite the file;
    only controlled keys in [REMOTE], [RACE], [DYNAMIC_TRACK] are updated.
    Everything else (HEADER, OPTIONS, __CM_*, etc.) is preserved exactly.
    Writes patched content atomically, then verifies and logs.
    """
    sc = _section_upper(server_cfg) if server_cfg else {}
    preset_label = preset_name or "?"
    server = sc.get("SERVER") or {}

    if not server:
        logger.warning("[sync] preset=%s missing [SERVER] section", preset_label)

    cars_str = server.get("CARS") or ""
    car = _single_car_id(_validate_selected_car(selected_car or "", cars_str))
    if selected_car and selected_car.strip() and car.lower() != (selected_car or "").strip().lower():
        logger.warning("[sync] preset=%s selected_car=%r not in CARS list; using first car=%s", preset_label, selected_car, car)

    track_raw = server.get("TRACK") or ""
    track = _normalize_track(track_raw)
    if not track or track == "unknown":
        logger.error(
            "[sync] preset=%s TRACK raw=%r -> normalized empty/unknown -- refusing to write invalid race.ini",
            preset_label, track_raw,
        )
        raise ValueError(
            f"Cannot sync race.ini: server track is '{track_raw or chr(40) + 'empty' + chr(41)}' which resolves to 'unknown'. "
            "The server metadata could not be resolved. Ensure the server is online and reachable."
        )

    if not race_ini_path.parent.exists():
        race_ini_path.parent.mkdir(parents=True, exist_ok=True)

    existing_text = _read_race_ini_text(race_ini_path)
    global_pwd = global_password
    if global_pwd is not None and isinstance(global_pwd, str):
        global_pwd = global_pwd.strip() or None
    password_source = "global" if global_pwd is not None else "preset"
    patched = patch_race_ini_for_online_join(
        existing_text,
        server_cfg,
        join_ip,
        int(join_port),
        selected_car,
        global_pwd,
    )

    tmp_fd, tmp_path = tempfile.mkstemp(dir=race_ini_path.parent, prefix=".race_ini.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(patched)
        os.replace(tmp_path, race_ini_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    _verify_and_log_after_sync(
        race_ini_path,
        global_password_configured=global_password is not None and bool((global_password or "").strip()),
        password_source=password_source,
    )