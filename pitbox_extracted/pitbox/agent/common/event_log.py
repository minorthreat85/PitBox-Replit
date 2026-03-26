"""
Structured event log model (mirror of controller/common/event_log.py).
Every entry has exactly one category; ERROR level should use category ERROR or include event_code.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def now_utc() -> datetime:
    """Current UTC time for event timestamps."""
    return datetime.now(timezone.utc)


class LogLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogCategory(str, Enum):
    SYSTEM = "SYSTEM"
    RIG = "RIG"
    SESSION = "SESSION"
    PRESET = "PRESET"
    BOOKING = "BOOKING"
    SERVER = "SERVER"
    ERROR = "ERROR"


EventSource = Literal["Controller", "Agent", "Kiosk"]


class EventLogEntry(BaseModel):
    """Structured log entry for operator-facing event log."""

    timestamp: datetime = Field(default_factory=now_utc, description="UTC ISO timestamp")
    level: LogLevel = Field(..., description="INFO, WARN, ERROR")
    category: LogCategory = Field(..., description="Exactly one category per entry")
    source: EventSource = Field(..., description="Controller, Agent, or Kiosk")
    message: str = Field(..., min_length=1, description="Human-readable message")

    rig_id: Optional[str] = Field(default=None, description="e.g. Sim5")
    session_id: Optional[str] = Field(default=None, description="Booking/session correlation")
    event_code: Optional[str] = Field(default=None, description="Stable code e.g. PRESET_STEERING_MISSING")
    details: Optional[dict[str, Any]] = Field(default=None, description="Extra data")
    trace_id: Optional[str] = Field(default=None, description="Multi-step flow correlation")

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if v is None:
            return now_utc()
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        raise ValueError("Invalid timestamp")

    def to_jsonl_line(self) -> str:
        """One JSON line for appending to JSONL file (no trailing newline; caller adds it)."""
        return self.model_dump_json()

    @classmethod
    def from_jsonl_line(cls, line: str) -> "EventLogEntry":
        """Parse one JSON line into EventLogEntry."""
        return cls.model_validate_json(line)


def make_event(
    level: LogLevel,
    category: LogCategory,
    source: EventSource,
    message: str,
    *,
    rig_id: Optional[str] = None,
    session_id: Optional[str] = None,
    event_code: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> EventLogEntry:
    """Build an EventLogEntry with optional fields. Timestamp defaults to now_utc()."""
    return EventLogEntry(
        timestamp=timestamp or now_utc(),
        level=level,
        category=category,
        source=source,
        message=message,
        rig_id=rig_id,
        session_id=session_id,
        event_code=event_code,
        details=details,
        trace_id=trace_id,
    )
