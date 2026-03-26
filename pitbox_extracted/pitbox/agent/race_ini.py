"""
Parse Assetto Corsa race.ini into a normalized "last session" object.

race.ini has two save-state formats:
- Single Player: [REMOTE] ACTIVE=0, full scenario (sessions + AI grid [CAR_0]..[CAR_N])
- Online: [REMOTE] ACTIVE=1, minimal snapshot (SERVER_* fields, usually no CAR_1+)

Do NOT assume one schema. Detect mode first, then extract fields.
"""
from pathlib import Path
from typing import Any, Optional


def _read_ini_raw(path: Path, encoding: str) -> dict[str, dict[str, str]]:
    """Read INI file with given encoding into { section_upper: { key_upper: value } }."""
    result: dict[str, dict[str, str]] = {}
    section = ""
    for line in path.read_text(encoding=encoding, errors="strict").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().upper()
            if section and section not in result:
                result[section] = {}
            continue
        if not section or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip().upper()
        val = val.strip()
        if key:
            result[section][key] = val
    return result


def _read_ini(path: Path) -> dict[str, dict[str, str]]:
    """Read INI file with encoding fallbacks (utf-8-sig, utf-8, cp1252, latin-1) so ACTIVE is read correctly."""
    if not path.exists():
        return {}
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return _read_ini_raw(path, enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return {}


def _detect_mode(ini: dict[str, dict[str, str]]) -> str:
    """Return 'online' or 'singleplayer' from [REMOTE] ACTIVE only. ACTIVE=1 -> Online, ACTIVE=0 or missing -> Single."""
    remote = ini.get("REMOTE") or {}
    active = (remote.get("ACTIVE") or "").strip()
    return "online" if active == "1" else "singleplayer"


def parse_last_session(race_ini_path: Path) -> Optional[dict[str, Any]]:
    """
    Parse race.ini into a normalized last-session object for UI.

    Returns:
        {
          "mode": "singleplayer" | "online",
          "modeLabel": "Single Player" | "Online",
          "car": str,      # MODEL from [RACE] or [CAR_0]
          "skin": str,     # SKIN from [CAR_0] (e.g. 00_official) for preview path
          "track": str,    # TRACK from [RACE]
          "layout": str,   # CONFIG_TRACK from [RACE] (e.g. chicane for ui/chicane/preview.png)
          "server": None | { "name": str, "ip": str, "port": str }
        }
        or None if file missing / empty / no [RACE].
    """
    ini = _read_ini(race_ini_path)
    race = ini.get("RACE") or {}
    if not race:
        return None

    car_0 = ini.get("CAR_0") or {}
    mode = _detect_mode(ini)
    mode_label = "Online" if mode == "online" else "Single Player"
    car = (race.get("MODEL") or car_0.get("MODEL") or "").strip()
    skin = (car_0.get("SKIN") or race.get("SKIN") or "").strip()
    track = (race.get("TRACK") or "").strip()
    layout = (race.get("CONFIG_TRACK") or "").strip()

    server: Optional[dict[str, str]] = None
    if mode == "online":
        remote = ini.get("REMOTE") or {}
        sname = (remote.get("SERVER_NAME") or "").strip()
        sip = (remote.get("SERVER_IP") or "").strip()
        sport = (remote.get("SERVER_PORT") or "").strip()
        if sip or sport or sname:
            server = {"name": sname or "—", "ip": sip or "—", "port": sport or "—"}

    return {
        "mode": mode,
        "modeLabel": mode_label,
        "car": car or "—",
        "skin": skin or "—",
        "track": track or "—",
        "layout": layout or "—",
        "server": server,
    }
