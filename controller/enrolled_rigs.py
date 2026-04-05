"""
Enrolled rigs registry: device_id -> host, port, token. Order preserved for UI.
Persistence: enrolled_rigs.json (same dir as controller config).
Backup in AppData so enrollment survives redeploy (e.g. overwriting config dir with new build).
Optional display_name per rig (e.g. "Sim 5") so the card label matches the physical station.
"""
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ENROLLED_FILENAME = "enrolled_rigs.json"
_rigs: list[dict] = []  # [{ "agent_id", "host", "port", "token", "hostname?", "display_name?", "enrolled_at", "order" }]
_config_dir: Optional[Path] = None


def _registry_path() -> Path:
    """Canonical path for enrolled_rigs.json: always %APPDATA%/PitBox/Controller/enrolled_rigs.json. No cwd fallback."""
    if _config_dir is not None:
        return _config_dir / _ENROLLED_FILENAME
    from pitbox_common.runtime_paths import controller_dir
    return controller_dir() / _ENROLLED_FILENAME


def _registry_backup_path() -> Path:
    """Backup copy next to AppData (controller data dir) so enrollment survives redeploy."""
    from pitbox_common.runtime_paths import controller_data_dir
    return controller_data_dir() / "enrolled_rigs_backup.json"


def set_config_dir(path: Optional[Path]) -> None:
    """Set config directory for registry path (e.g. from main after loading config)."""
    global _config_dir
    _config_dir = path


def _parse_rigs_from_data(data: dict | list) -> list[dict]:
    """Parse and validate rigs from JSON structure. Supports backend 'agent' (default) and 'cm'."""
    raw = data.get("rigs") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not isinstance(r, dict) or not r.get("agent_id") or not r.get("host"):
            continue
        aid = (r.get("agent_id") or "").strip()
        backend = (r.get("backend") or "agent").strip().lower()
        if backend == "cm":
            # CM backend: require cm_port (default 11777); cm_password optional
            port = r.get("cm_port") or r.get("port")
            if port is not None:
                out.append({**r, "backend": "cm", "cm_port": int(port), "cm_password": r.get("cm_password") or ""})
            else:
                out.append({**r, "backend": "cm", "cm_port": 11777, "cm_password": r.get("cm_password") or ""})
        else:
            port = r.get("port")
            token = (r.get("token") or "").strip()
            if not token:
                logger.warning("Skipped rig agent_id=%r: missing or empty token (re-enroll this sim)", aid)
                continue
            try:
                port_int = int(port) if port is not None else 9631
            except (TypeError, ValueError):
                port_int = 9631
            if not (1 <= port_int <= 65535):
                logger.warning("Skipped rig agent_id=%r: port %s out of range (use 1-65535)", aid, port)
                continue
            out.append({**r, "backend": "agent", "port": port_int, "token": token})
    return out


def load() -> None:
    """Load enrolled rigs from disk. Canonical path is AppData; if missing, restore from backup in controller data dir."""
    global _rigs
    primary = _registry_path()
    backup = _registry_backup_path()

    if primary.is_file():
        try:
            with open(primary, "r", encoding="utf-8") as f:
                data = json.load(f)
            _rigs = _parse_rigs_from_data(data)
            logger.info("Loaded %d enrolled rig(s) from %s", len(_rigs), primary)
            _save_backup()
            return
        except Exception as e:
            logger.warning("Could not load enrolled rigs from %s: %s", primary, e)

    if backup.is_file():
        try:
            with open(backup, "r", encoding="utf-8") as f:
                data = json.load(f)
            _rigs = _parse_rigs_from_data(data)
            logger.info("Restored %d enrolled rig(s) from backup %s (primary missing or unreadable)", len(_rigs), backup)
            _save()
            return
        except Exception as e:
            logger.warning("Could not load enrolled rigs backup from %s: %s", backup, e)

    _rigs = []


def _save() -> None:
    """Write to canonical AppData path and to backup in controller data dir."""
    path = _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"rigs": _rigs}, f, indent=2, ensure_ascii=False)
        _save_backup()
    except OSError as e:
        logger.warning("Could not save enrolled rigs to %s: %s", path, e)


def _save_backup() -> None:
    """Write backup to controller data dir (next to AppData) so enrollment survives redeploy."""
    path = _registry_backup_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"rigs": _rigs}, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.debug("Could not save enrolled rigs backup to %s: %s", path, e)


def _display_name_from_hostname(hostname: Optional[str]) -> Optional[str]:
    """Derive 'Sim N' from hostname if it looks like sim5, Sim-5, SIM 5, etc. Otherwise None."""
    if not hostname or not isinstance(hostname, str):
        return None
    hostname = hostname.strip()
    if not hostname:
        return None
    m = re.search(r"sim\s*[-_]?\s*(\d+)", hostname, re.IGNORECASE)
    if m:
        return f"Sim {m.group(1)}"
    if hostname.isdigit():
        return f"Sim {hostname}"
    return None


def get_display_name_for_rig(rig: dict, index: int) -> str:
    """Display name for UI: rig.display_name, else from hostname (e.g. Sim5 -> Sim 5), else 'Sim {index+1}'."""
    dn = (rig.get("display_name") or "").strip()
    if dn:
        return dn
    from_hostname = _display_name_from_hostname(rig.get("hostname"))
    if from_hostname:
        return from_hostname
    return f"Sim {index + 1}"


def _normalize_display_label(label: str) -> str:
    """Normalize for matching: lowercase, collapse spaces (so 'Sim 5' and 'Sim5' both become 'sim5')."""
    if not label or not isinstance(label, str):
        return ""
    return "".join(label.strip().lower().split())


def get_agent_id_by_display_name(label: str) -> Optional[str]:
    """Return rig agent_id (device_id) whose display name matches label (e.g. Sim5, Sim 5, or just 5). Case-insensitive."""
    if not label or not (label := label.strip()):
        return None
    key = _normalize_display_label(label)
    if not key:
        return None
    for i, r in enumerate(get_all_ordered()):
        dn = get_display_name_for_rig(r, i)
        dn_key = _normalize_display_label(dn)
        if dn_key == key:
            return (r.get("agent_id") or "").strip()
        # Allow "5" to match "Sim 5" / "Sim5" (so /kiosk?agent_id=5 works on sim 5)
        if key.isdigit() and dn_key == "sim" + key:
            return (r.get("agent_id") or "").strip()
    return None


def add(agent_id: str, host: str, port: int, hostname: Optional[str] = None) -> str:
    """
    Add or update an enrolled rig by device_id (dedupe). For existing device_id: update host/port/hostname only, keep token.
    For new device_id: create entry with new token. Returns the token (existing or new).
    """
    global _rigs
    agent_id = (agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    if not host or not (1 <= port <= 65535):
        raise ValueError("host and port required")
    existing = next((r for r in _rigs if (r.get("agent_id") or "").strip() == agent_id), None)
    hn = (hostname or "").strip() or None
    display_name = _display_name_from_hostname(hn)
    if existing:
        logger.info(
            "Enrollment updating existing rig device_id=%r (was %s:%s, now %s:%s). "
            "If this is a different physical sim, give it a unique device_id (delete identity.json on that PC to generate a new one).",
            agent_id, existing.get("host"), existing.get("port"), host.strip(), port,
        )
        existing_dn = (existing.get("display_name") or "").strip()
        if existing_dn:
            display_name = existing_dn
        token = (existing.get("token") or "").strip() or secrets.token_urlsafe(24)
        entry = {
            "agent_id": agent_id,
            "host": host.strip(),
            "port": int(port),
            "token": token,
            "hostname": hn,
            "display_name": display_name,
            "enrolled_at": existing.get("enrolled_at") or __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "order": existing.get("order", 0),
        }
        idx = next(i for i, r in enumerate(_rigs) if (r.get("agent_id") or "").strip() == agent_id)
        _rigs[idx] = entry
        _save()
        logger.info("Rig update: device_id=%s ip=%s port=%s existing=true", agent_id, host.strip(), port)
        return token
    token = secrets.token_urlsafe(24)
    display_name = display_name or _display_name_from_hostname(hn)
    entry = {
        "agent_id": agent_id,
        "host": host.strip(),
        "port": int(port),
        "token": token,
        "hostname": hn,
        "display_name": display_name,
        "enrolled_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "order": len(_rigs),
    }
    _rigs.append(entry)
    _save()
    logger.info("Rig update: device_id=%s ip=%s port=%s existing=false", agent_id, host.strip(), port)
    return token


def add_cm(
    agent_id: str,
    host: str,
    cm_port: int = 11777,
    cm_password: str = "",
    display_name: Optional[str] = None,
    hostname: Optional[str] = None,
) -> None:
    """
    Add or update an enrolled rig that uses Content Manager remote control (backend=cm).
    No PitBox Agent required on the sim; CM must be running with remote control enabled.
    """
    global _rigs
    agent_id = (agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    if not host or not host.strip():
        raise ValueError("host required")
    if not (1 <= cm_port <= 65535):
        raise ValueError("cm_port must be 1-65535")
    hn = (hostname or "").strip() or None
    dn = (display_name or "").strip() or _display_name_from_hostname(hn)
    existing = next((r for r in _rigs if (r.get("agent_id") or "").strip() == agent_id), None)
    entry = {
        "agent_id": agent_id,
        "host": host.strip(),
        "backend": "cm",
        "cm_port": int(cm_port),
        "cm_password": (cm_password or "").strip(),
        "hostname": hn,
        "display_name": dn or _display_name_from_hostname(hn),
        "enrolled_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "order": existing.get("order", len(_rigs)) if existing else len(_rigs),
    }
    if existing:
        idx = next(i for i, r in enumerate(_rigs) if (r.get("agent_id") or "").strip() == agent_id)
        _rigs[idx] = entry
        logger.info("CM rig update: device_id=%s host=%s cm_port=%s", agent_id, host.strip(), cm_port)
    else:
        _rigs.append(entry)
        logger.info("CM rig added: device_id=%s host=%s cm_port=%s", agent_id, host.strip(), cm_port)
    _save()


def get(agent_id: str) -> Optional[dict]:
    """Return rig dict (host, port, token, ...) or None."""
    agent_id = (agent_id or "").strip()
    for r in _rigs:
        if (r.get("agent_id") or "").strip() == agent_id:
            return r
    return None


def get_all_ordered() -> list[dict]:
    """Return all rigs in enrollment order (order field, then list order)."""
    return sorted(_rigs, key=lambda r: (r.get("order", 999), _rigs.index(r) if r in _rigs else 999))


def update_display_name(agent_id: str, display_name: Optional[str]) -> bool:
    """Set (or clear) the display name for a rig. Returns True if found and saved."""
    agent_id = (agent_id or "").strip()
    for r in _rigs:
        if (r.get("agent_id") or "").strip() == agent_id:
            dn = (display_name or "").strip() or None
            r["display_name"] = dn
            _save()
            return True
    return False


def remove(agent_id: str) -> bool:
    """Remove a rig. Returns True if removed."""
    global _rigs
    agent_id = (agent_id or "").strip()
    before = len(_rigs)
    _rigs = [r for r in _rigs if (r.get("agent_id") or "").strip() != agent_id]
    if len(_rigs) < before:
        _save()
        return True
    return False
