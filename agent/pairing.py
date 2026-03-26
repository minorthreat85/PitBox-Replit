"""
Pairing state: controller_base_url and token after enrollment. Stored in pairing.json.
Lives in the same folder as agent_config.json (Agent/config/).
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _pairing_path() -> Path:
    """pairing.json in agent config dir (same folder as agent_config.json)."""
    try:
        from agent.config import get_agent_config_dir
        dir_ = get_agent_config_dir()
    except Exception:
        dir_ = Path.cwd()
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_ / "pairing.json"


def is_paired() -> bool:
    """True if pairing.json exists and has controller_base_url and token."""
    path = _pairing_path()
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool((data.get("controller_base_url") or "").strip() and (data.get("token") or "").strip())
    except Exception:
        return False


def get_controller_url() -> str:
    """Controller base URL from pairing.json, or empty string."""
    path = _pairing_path()
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("controller_base_url") or "").strip()
    except Exception:
        return ""


def get_token() -> str:
    """Token from pairing.json, or empty string."""
    path = _pairing_path()
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("token") or "").strip()
    except Exception:
        return ""


def save_paired(controller_base_url: str, token: str, device_id: str) -> None:
    """Write pairing.json after successful enrollment."""
    import datetime
    path = _pairing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "controller_base_url": (controller_base_url or "").strip().rstrip("/"),
        "token": (token or "").strip(),
        "device_id": (device_id or "").strip(),
        "paired_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved pairing to %s (controller=%s)", path, data["controller_base_url"])


def clear_paired() -> bool:
    """
    Unpair: notify controller to remove this rig (sim card disappears), then delete pairing.json.
    Returns True if pairing was cleared (file deleted). Call when user unpairs on this sim.
    """
    path = _pairing_path()
    url = get_controller_url()
    token = get_token()
    device_id = ""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                device_id = (data.get("device_id") or "").strip()
        except Exception:
            pass
    if url and token:
        try:
            import httpx
            r = httpx.post(
                f"{url}/api/pair/unenroll",
                headers={"X-Agent-Id": device_id or "unknown", "X-Agent-Token": token},
                timeout=5.0,
            )
            if r.status_code in (200, 404):
                logger.info("Notified controller to unenroll (status=%s)", r.status_code)
            else:
                logger.warning("Controller unenroll returned %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("Could not notify controller of unenroll: %s", e)
    if path.exists():
        try:
            path.unlink()
            logger.info("Removed pairing file %s", path)
            return True
        except OSError as e:
            logger.warning("Could not delete pairing file: %s", e)
            return False
    return True
