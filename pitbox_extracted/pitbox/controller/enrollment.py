"""
Enrollment mode state: ON for 10 minutes, random secret, countdown.
"""
import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

ENROLLMENT_DURATION_SEC = 600  # 10 minutes

_enabled = False
_until_ts: float = 0.0
_secret: str = ""


def start_enrollment() -> str:
    """Turn enrollment ON for 10 minutes. Returns the new enrollment_secret."""
    global _enabled, _until_ts, _secret
    _enabled = True
    _until_ts = time.time() + ENROLLMENT_DURATION_SEC
    _secret = secrets.token_urlsafe(16)
    logger.info("Enrollment mode ON for %s s, secret=%s...", ENROLLMENT_DURATION_SEC, _secret[:4])
    return _secret


def stop_enrollment() -> None:
    """Turn enrollment OFF."""
    global _enabled
    _enabled = False
    logger.info("Enrollment mode OFF")


def get_state() -> dict[str, Any]:
    """Return { enabled, until_ts, seconds_remaining, secret }. Auto-disable when expired."""
    global _enabled
    now = time.time()
    if _enabled and now >= _until_ts:
        _enabled = False
        logger.info("Enrollment mode expired (timer)")
    remaining = max(0.0, _until_ts - now) if _enabled else 0.0
    return {
        "enabled": _enabled,
        "until_ts": _until_ts,
        "seconds_remaining": int(remaining),
        "secret": _secret if _secret else "",
    }


def is_enabled() -> bool:
    """True if enrollment is on and not expired."""
    s = get_state()
    return s["enabled"] and s["seconds_remaining"] > 0


def verify_secret(provided: str) -> bool:
    """True if provided non-empty string matches current enrollment secret."""
    if not provided or not _secret:
        return False
    return secrets.compare_digest(provided.strip(), _secret)
