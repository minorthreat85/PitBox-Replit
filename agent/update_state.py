"""
Persistent agent update state — stored outside user config so it survives config resets.

State file: %%LOCALAPPDATA%%\\PitBox\\agent_update_state.json
           (falls back to C:\\PitBox\\Agent\\update_state.json)

Fields:
  current_version   — version running now (from pitbox_common.version)
  target_version    — version being installed or pending (None = none)
  update_status     — idle | pending | downloading | installing | restarting | failed
  last_update_error — human-readable error string (None = no error)
  rollback_available — True if a backup exe exists
  pending_installer_url   — URL to download when AC finishes (pending only)
  pending_installer_sha256 — SHA-256 of pending installer (may be None)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_FILENAME = "agent_update_state.json"

_UPDATE_STATUS_VALUES = frozenset({
    "idle",
    "pending",
    "downloading",
    "installing",
    "restarting",
    "failed",
})


def _state_path() -> Path:
    """Resolve state file path — prefer LOCALAPPDATA, fall back to C:\\PitBox\\Agent."""
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        p = Path(localappdata) / "PitBox" / _STATE_FILENAME
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    fallback = Path(r"C:\PitBox\Agent") / _STATE_FILENAME
    try:
        fallback.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return fallback


def _default_state() -> dict:
    from pitbox_common.version import __version__
    return {
        "current_version": __version__,
        "target_version": None,
        "update_status": "idle",
        "last_update_error": None,
        "rollback_available": False,
        "pending_installer_url": None,
        "pending_installer_sha256": None,
    }


def load_state() -> dict:
    """Load update state from disk. Returns defaults if file is missing or corrupt."""
    p = _state_path()
    try:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Not a dict")
            merged = _default_state()
            merged.update(data)
            # Always reflect actual running version
            from pitbox_common.version import __version__
            merged["current_version"] = __version__
            return merged
    except Exception as e:
        logger.warning("Could not load update state from %s: %s — using defaults", p, e)
    return _default_state()


def save_state(state: dict) -> None:
    """Persist update state to disk."""
    p = _state_path()
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        logger.debug("Update state saved: status=%s target=%s", state.get("update_status"), state.get("target_version"))
    except Exception as e:
        logger.error("Could not save update state to %s: %s", p, e)


def get_status() -> dict:
    """Return current update state with fresh current_version."""
    return load_state()


def set_idle() -> None:
    """Reset to idle — clears target, error, pending fields."""
    state = load_state()
    state.update({
        "update_status": "idle",
        "target_version": None,
        "last_update_error": None,
        "pending_installer_url": None,
        "pending_installer_sha256": None,
    })
    save_state(state)


def set_pending(
    installer_url: str,
    target_version: Optional[str] = None,
    installer_sha256: Optional[str] = None,
) -> None:
    """Mark an update as pending (deferred because AC is running)."""
    state = load_state()
    state.update({
        "update_status": "pending",
        "target_version": target_version,
        "pending_installer_url": installer_url,
        "pending_installer_sha256": installer_sha256,
        "last_update_error": None,
    })
    save_state(state)
    logger.info("Update pending — will apply when AC stops. target=%s", target_version)


def set_status(
    status: str,
    target_version: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update status field (and optionally target_version / error)."""
    if status not in _UPDATE_STATUS_VALUES:
        logger.warning("Unknown update_status value: %r", status)
    state = load_state()
    state["update_status"] = status
    if target_version is not None:
        state["target_version"] = target_version
    if error is not None:
        state["last_update_error"] = error
    elif status not in ("failed",):
        state["last_update_error"] = None
    save_state(state)


def set_rollback_available(available: bool) -> None:
    state = load_state()
    state["rollback_available"] = available
    save_state(state)


def cancel_pending() -> dict:
    """Cancel a pending update. Returns the cleared state."""
    state = load_state()
    if state.get("update_status") == "pending":
        state.update({
            "update_status": "idle",
            "target_version": None,
            "pending_installer_url": None,
            "pending_installer_sha256": None,
            "last_update_error": None,
        })
        save_state(state)
        logger.info("Pending update cancelled")
    return state


def get_pending() -> Optional[dict]:
    """
    Return pending update details if status == 'pending', else None.
    Returns: {"installer_url": ..., "target_version": ..., "sha256": ...}
    """
    state = load_state()
    if state.get("update_status") != "pending":
        return None
    url = state.get("pending_installer_url")
    if not url:
        return None
    return {
        "installer_url": url,
        "target_version": state.get("target_version"),
        "sha256": state.get("pending_installer_sha256"),
    }
