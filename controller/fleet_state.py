"""
PitBox Fleet Rollout State — persistent per-agent update tracking.

Stored as JSON so the admin UI survives controller restarts.
The controller is the single orchestrator; agents receive instructions from here.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

AGENT_UPDATE_STATUS = (
    "idle",
    "available",
    "staged",
    "pending_idle",
    "downloading",
    "installing",
    "restarting",
    "updated",
    "failed",
    "offline",
    "unknown",
)

_STATE_DIR = Path(os.environ.get("PITBOX_DATA_DIR", r"C:\PitBox\data"))
_STATE_FILE = _STATE_DIR / "fleet_rollout_state.json"

_DEV_STATE_DIR = Path(__file__).resolve().parent.parent / "data"
_DEV_STATE_FILE = _DEV_STATE_DIR / "fleet_rollout_state.json"


def _state_path() -> Path:
    if _STATE_DIR.exists() or os.name == "nt":
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return _STATE_FILE
    try:
        _DEV_STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return _DEV_STATE_FILE


def _default_state() -> dict:
    return {
        "approved_version": None,
        "target_version": None,
        "agents": {},
        "last_updated_at": None,
    }


def _default_agent_state(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "installed_version": None,
        "update_status": "unknown",
        "target_version": None,
        "last_update_error": None,
        "last_update_at": None,
        "ac_running": False,
        "online": False,
    }


def reset_all_agent_states() -> None:
    """Clear all per-agent update statuses and errors (keeps agent IDs)."""
    state = load_state()
    for aid, agent in state.get("agents", {}).items():
        agent["update_status"] = "unknown"
        agent["target_version"] = None
        agent["last_update_error"] = None
    state["target_version"] = None
    save_state(state)
    logger.info("Fleet rollout state reset for %d agents", len(state.get("agents", {})))


def load_state() -> dict:
    p = _state_path()
    try:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = _default_state()
                merged.update(data)
                return merged
    except Exception as e:
        logger.warning("Could not load fleet state from %s: %s", p, e)
    return _default_state()


def save_state(state: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        state["last_updated_at"] = time.time()
        with p.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error("Could not save fleet state to %s: %s", p, e)


def get_agent_state(agent_id: str) -> dict:
    state = load_state()
    agents = state.get("agents", {})
    return agents.get(agent_id, _default_agent_state(agent_id))


def update_agent_state(agent_id: str, **kwargs) -> dict:
    state = load_state()
    if "agents" not in state:
        state["agents"] = {}
    if agent_id not in state["agents"]:
        state["agents"][agent_id] = _default_agent_state(agent_id)
    state["agents"][agent_id].update(kwargs)
    save_state(state)
    return state["agents"][agent_id]


def update_agent_from_poll(agent_id: str, poll_data: dict) -> dict:
    state = load_state()
    if "agents" not in state:
        state["agents"] = {}
    if agent_id not in state["agents"]:
        state["agents"][agent_id] = _default_agent_state(agent_id)
    agent = state["agents"][agent_id]
    agent["online"] = poll_data.get("online", True)
    if poll_data.get("current_version"):
        agent["installed_version"] = poll_data["current_version"]
    if "update_status" in poll_data:
        agent["update_status"] = poll_data["update_status"]
    if "target_version" in poll_data:
        agent["target_version"] = poll_data["target_version"]
    if "last_update_error" in poll_data:
        agent["last_update_error"] = poll_data["last_update_error"]
    if "ac_running" in poll_data:
        agent["ac_running"] = poll_data["ac_running"]
    save_state(state)
    return agent


def set_agent_offline(agent_id: str) -> dict:
    return update_agent_state(agent_id, online=False, update_status="offline")


def set_approved_version(version: str) -> dict:
    state = load_state()
    state["approved_version"] = version
    state["target_version"] = version
    save_state(state)
    return state


def get_fleet_summary() -> dict:
    state = load_state()
    agents = state.get("agents", {})
    total = len(agents)
    online = sum(1 for a in agents.values() if a.get("online"))
    approved = state.get("approved_version")
    up_to_date = 0
    outdated = 0
    pending = 0
    failed = 0
    for a in agents.values():
        st = a.get("update_status", "unknown")
        if st in ("idle", "updated"):
            if approved and a.get("installed_version") == approved:
                up_to_date += 1
            elif approved:
                outdated += 1
            else:
                up_to_date += 1
        elif st in ("pending_idle", "staged", "available"):
            pending += 1
        elif st == "failed":
            failed += 1
        elif st == "offline":
            pass
        else:
            outdated += 1
    return {
        "total": total,
        "online": online,
        "up_to_date": up_to_date,
        "outdated": outdated,
        "pending": pending,
        "failed": failed,
        "approved_version": approved,
        "target_version": state.get("target_version"),
    }


def get_all_agent_states() -> list[dict]:
    state = load_state()
    agents = state.get("agents", {})
    result = []
    for agent_id, agent in agents.items():
        entry = _default_agent_state(agent_id)
        entry.update(agent)
        entry["agent_id"] = agent_id
        approved = state.get("approved_version")
        entry["approved_version"] = approved
        entry["is_outdated"] = bool(
            approved
            and entry.get("installed_version")
            and entry["installed_version"] != approved
        )
        result.append(entry)
    return result


def cancel_pending(agent_ids: list[str] | None = None) -> list[dict]:
    state = load_state()
    agents = state.get("agents", {})
    results = []
    for agent_id, agent in agents.items():
        if agent_ids and agent_id not in agent_ids:
            continue
        if agent.get("update_status") in ("pending_idle", "staged", "available"):
            agent["update_status"] = "idle"
            agent["target_version"] = None
            agent["last_update_error"] = None
            results.append({"agent_id": agent_id, "cancelled": True})
    save_state(state)
    return results


def mark_failed_for_retry(agent_ids: list[str] | None = None) -> list[str]:
    state = load_state()
    agents = state.get("agents", {})
    retried = []
    for agent_id, agent in agents.items():
        if agent_ids and agent_id not in agent_ids:
            continue
        if agent.get("update_status") == "failed":
            agent["update_status"] = "staged"
            agent["last_update_error"] = None
            retried.append(agent_id)
    save_state(state)
    return retried
