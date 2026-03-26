"""
Emit structured events: write to local JSONL and best-effort POST to Controller.
Never crash agent if controller is offline (swallow network errors, log WARN locally).
"""
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.common.event_log import EventLogEntry, LogCategory, LogLevel, make_event

logger = logging.getLogger(__name__)

_POST_TIMEOUT = 1.0
_lock = threading.Lock()


def _events_dir() -> Path:
    """Agent local event JSONL directory: <agent_config_dir>/../logs/events."""
    from agent.config import get_agent_config_dir
    return get_agent_config_dir().parent / "logs" / "events"


def _file_for_date(d: datetime) -> Path:
    date_str = d.strftime("%Y-%m-%d")
    return _events_dir() / f"{date_str}.jsonl"


def _write_local(entry: EventLogEntry) -> None:
    """Append one event to local JSONL. Best-effort; never raise."""
    try:
        base = _events_dir()
        base.mkdir(parents=True, exist_ok=True)
        path = _file_for_date(entry.timestamp)
        line = entry.to_jsonl_line() + "\n"
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        logger.warning("Event log local write failed: %s", e)


def _post_to_controller(entry: EventLogEntry, controller_url: str, agent_id: str, token: str) -> None:
    """POST entry to Controller /api/logs/event. Swallow all errors (do not crash agent)."""
    url = controller_url.rstrip("/") + "/api/logs/event"
    try:
        body = entry.model_dump_json()
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Agent-Id", agent_id)
        req.add_header("X-Agent-Token", token)
        with urllib.request.urlopen(req, timeout=_POST_TIMEOUT) as resp:
            if resp.status not in (200, 201, 204):
                logger.warning("Event POST returned %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.warning("Event POST failed: %s %s", e.code, e.reason)
    except urllib.error.URLError as e:
        logger.debug("Event POST unreachable: %s", e.reason)
    except OSError as e:
        logger.warning("Event POST error: %s", e)


def emit_event(
    entry: EventLogEntry,
    *,
    controller_url: Optional[str] = None,
    agent_id: Optional[str] = None,
    token: Optional[str] = None,
) -> None:
    """
    1) Write to local JSONL at Agent logs/events/YYYY-MM-DD.jsonl
    2) Best-effort POST to Controller /api/logs/event (timeout 1s)
    Never raises; network errors are swallowed and logged at WARN.
    """
    _write_local(entry)
    if controller_url and agent_id and token:
        _post_to_controller(entry, controller_url, agent_id, token)


def emit(
    level: LogLevel,
    category: LogCategory,
    message: str,
    *,
    rig_id: Optional[str] = None,
    session_id: Optional[str] = None,
    event_code: Optional[str] = None,
    details: Optional[dict] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Convenience: build Agent event and emit. Uses current config for controller_url/agent_id/token if available."""
    entry = make_event(level, category, "Agent", message, rig_id=rig_id, session_id=session_id, event_code=event_code, details=details, trace_id=trace_id)
    controller_url = None
    agent_id = None
    token = None
    try:
        from agent.config import get_config
        cfg = get_config()
        if cfg.controller_url and cfg.agent_id and cfg.token:
            controller_url = cfg.controller_url.rstrip("/")
            agent_id = cfg.agent_id
            token = cfg.token
        if rig_id is None and agent_id:
            entry = entry.model_copy(update={"rig_id": agent_id})
    except Exception:
        pass
    emit_event(entry, controller_url=controller_url, agent_id=agent_id, token=token)
