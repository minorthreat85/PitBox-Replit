"""
Persistent device identity (device_id) for enrollment. Stored in identity.json.
Lives in the same folder as agent_config.json (Agent/config/).
"""
import json
import logging
import socket
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _identity_path() -> Path:
    """identity.json in canonical agent config dir (C:/PitBox/Agent/config or same folder as agent_config.json when inside that tree)."""
    try:
        from agent.config import get_agent_config_dir
        dir_ = get_agent_config_dir()
    except Exception:
        dir_ = Path.cwd()
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_ / "identity.json"


def get_device_id() -> str:
    """Load or create device_id. Creates identity.json if missing. Always uses canonical agent config dir (not CWD)."""
    path = _identity_path()
    logger.info("Identity path (canonical): %s", path.resolve())
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            did = (data.get("device_id") or "").strip()
            if did:
                return did
        except Exception as e:
            logger.warning("Could not load identity from %s: %s", path, e)
    # Create new: hostname + short uuid
    hostname = (socket.gethostname() or "sim").strip()[:32]
    device_id = f"{hostname}-{uuid.uuid4().hex[:8]}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"device_id": device_id}, f, indent=2)
        logger.info("Created identity device_id=%s at %s", device_id, path)
    except OSError as e:
        logger.warning("Could not save identity to %s: %s", path, e)
    return device_id
