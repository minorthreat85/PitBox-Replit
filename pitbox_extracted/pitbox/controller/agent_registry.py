"""
Agent registration registry: track per-agent IP, first/last seen, token.
Persists to a JSON file (configurable path).
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentRecord:
    """Per-agent registration record."""
    agent_id: str
    token: str
    registered_ip: str
    last_seen_ip: str
    first_seen: str  # ISO format
    last_seen: str   # ISO format
    ip_mismatch_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentRecord":
        return cls(
            agent_id=d["agent_id"],
            token=d["token"],
            registered_ip=d["registered_ip"],
            last_seen_ip=d["last_seen_ip"],
            first_seen=d["first_seen"],
            last_seen=d["last_seen"],
            ip_mismatch_count=d.get("ip_mismatch_count", 0),
        )


def _default_registry_path() -> Path:
    """Default path for controller_agents.json (configurable via env)."""
    import os
    env_path = os.environ.get("PITBOX_CONTROLLER_AGENTS_JSON")
    if env_path:
        return Path(env_path)
    return Path(r"C:\PitBox\controller_agents.json")


class AgentRegistry:
    """In-memory registry of agent records with JSON persistence."""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else _default_registry_path()
        self._records: dict[str, AgentRecord] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from JSON file."""
        if not self._path.exists():
            self._records = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._records = {}
            for agent_id, raw in (data.get("agents") or data or {}).items():
                try:
                    self._records[agent_id] = AgentRecord.from_dict(raw)
                except (KeyError, TypeError) as e:
                    logger.warning("Skip invalid agent record %s: %s", agent_id, e)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load agent registry from %s: %s", self._path, e)
            self._records = {}

    def _save(self) -> None:
        """Persist registry to JSON file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"agents": {aid: r.to_dict() for aid, r in self._records.items()}}
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning("Could not save agent registry to %s: %s", self._path, e)

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        """Return record for agent_id or None."""
        return self._records.get(agent_id)

    def register_or_update(
        self, agent_id: str, token: str, ip: str
    ) -> tuple[AgentRecord, bool, bool]:
        """
        Register a new agent or update last_seen for an existing one.

        Returns:
            (record, is_new_registration, ip_changed)
        """
        now = datetime.utcnow().isoformat() + "Z"
        prev = self._records.get(agent_id)
        is_new = prev is None
        ip_changed = False

        if is_new:
            record = AgentRecord(
                agent_id=agent_id,
                token=token,
                registered_ip=ip,
                last_seen_ip=ip,
                first_seen=now,
                last_seen=now,
                ip_mismatch_count=0,
            )
            self._records[agent_id] = record
        else:
            ip_changed = prev.last_seen_ip != ip
            record = AgentRecord(
                agent_id=agent_id,
                token=prev.token,
                registered_ip=prev.registered_ip,
                last_seen_ip=ip,
                first_seen=prev.first_seen,
                last_seen=now,
                ip_mismatch_count=prev.ip_mismatch_count + (1 if ip_changed else 0),
            )
            self._records[agent_id] = record

        self._save()
        return record, is_new, ip_changed

    def all_records(self) -> dict[str, dict]:
        """Return all records as agent_id -> dict (for API)."""
        return {aid: r.to_dict() for aid, r in self._records.items()}
