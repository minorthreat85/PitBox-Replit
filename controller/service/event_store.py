"""
Event log storage: JSONL per day, 7-day retention.
Path: %APPDATA%\\PitBox\\Controller\\logs\\events\\YYYY-MM-DD.jsonl
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from controller.common.event_log import EventLogEntry, LogCategory, LogLevel

logger = logging.getLogger(__name__)

# Retention: keep this many days of JSONL files
RETENTION_DAYS = 7


def _events_dir() -> Path:
    """Base directory for event JSONL files (APPDATA or fallback)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", "") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME", "") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "PitBox" / "Controller" / "logs" / "events"


def _file_for_date(d: datetime) -> Path:
    """Path to JSONL file for the given date (UTC date used for filename)."""
    date_str = d.strftime("%Y-%m-%d")
    return _events_dir() / f"{date_str}.jsonl"


def _run_retention() -> None:
    """Delete JSONL files older than RETENTION_DAYS. Best-effort on startup."""
    base = _events_dir()
    if not base.is_dir():
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).date()
    for p in base.glob("*.jsonl"):
        try:
            # Parse YYYY-MM-DD from filename
            name = p.stem
            if len(name) == 10 and name[4] == "-" and name[7] == "-":
                file_date = datetime.strptime(name, "%Y-%m-%d").date()
                if file_date < cutoff:
                    p.unlink()
                    logger.info("Event log retention: removed %s", p.name)
        except (ValueError, OSError) as e:
            logger.debug("Retention skip %s: %s", p, e)


def ensure_events_dir() -> Path:
    """Create events directory if needed; run retention. Returns events dir."""
    base = _events_dir()
    base.mkdir(parents=True, exist_ok=True)
    _run_retention()
    return base


def append_event(entry: EventLogEntry) -> None:
    """Append one event to today's JSONL file. Creates dir and file if needed."""
    ensure_events_dir()
    path = _file_for_date(entry.timestamp)
    line = entry.to_jsonl_line() + "\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Failed to append event to %s: %s", path, e)


def _parse_line(line: str) -> Optional[EventLogEntry]:
    """Parse one JSONL line; return None if invalid."""
    line = line.strip()
    if not line:
        return None
    try:
        return EventLogEntry.from_jsonl_line(line)
    except Exception:
        return None


def query_events(
    *,
    rig_id: Optional[str] = None,
    category: Optional[LogCategory] = None,
    level: Optional[LogLevel] = None,
    since_minutes: int = 60,
    limit: int = 300,
    search: Optional[str] = None,
) -> list[EventLogEntry]:
    """
    Load events from JSONL files in time window, apply filters, newest first.
    For MVP we scan files for the last since_minutes (and today); no index.
    """
    since_minutes = max(0, min(since_minutes, 60 * 24 * 8))  # cap ~8 days
    limit = max(1, min(limit, 2000))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    base = _events_dir()
    if not base.is_dir():
        return []

    collected: list[EventLogEntry] = []
    # Collect dates to scan: from cutoff date to today (UTC)
    start_date = cutoff.date()
    end_date = datetime.now(timezone.utc).date()
    dates_to_scan: list[datetime] = []
    d = start_date
    while d <= end_date:
        dates_to_scan.append(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc))
        d += timedelta(days=1)

    # Read newest first (reverse date order)
    for dt in reversed(dates_to_scan):
        path = _file_for_date(dt)
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.debug("Event query skip %s: %s", path, e)
            continue
        # Lines are chronological; reverse so we get newest first per file
        for line in reversed(lines):
            entry = _parse_line(line)
            if entry is None:
                continue
            if entry.timestamp.tzinfo is None:
                entry.timestamp = entry.timestamp.replace(tzinfo=timezone.utc)
            if entry.timestamp < cutoff:
                continue
            if rig_id is not None and (entry.rig_id or "").strip() != rig_id.strip():
                continue
            if category is not None and entry.category != category:
                continue
            if level is not None and entry.level != level:
                continue
            if search and search.strip():
                q = search.strip().lower()
                msg_ok = q in (entry.message or "").lower()
                details_ok = False
                if entry.details:
                    details_ok = q in str(entry.details).lower()
                if not msg_ok and not details_ok:
                    continue
            collected.append(entry)
            if len(collected) >= limit:
                break
        if len(collected) >= limit:
            break

    # Sort newest first (we may have merged multiple files)
    collected.sort(key=lambda e: e.timestamp, reverse=True)
    return collected[:limit]


def query_summary_last_minutes(minutes: int = 60) -> dict[str, Any]:
    """Rollup counts for last N minutes: errors_by_rig, errors_by_category, total_errors, total_warns."""
    events = query_events(since_minutes=minutes, limit=5000, level=None)
    errors_by_rig: dict[str, int] = {}
    errors_by_category: dict[str, int] = {}
    total_errors = 0
    total_warns = 0
    for e in events:
        if e.level == LogLevel.ERROR:
            total_errors += 1
            rig = (e.rig_id or "").strip() or "_"
            errors_by_rig[rig] = errors_by_rig.get(rig, 0) + 1
            cat = e.category.value
            errors_by_category[cat] = errors_by_category.get(cat, 0) + 1
        elif e.level == LogLevel.WARN:
            total_warns += 1
    return {
        "errors_by_rig": errors_by_rig,
        "errors_by_category": errors_by_category,
        "total_errors": total_errors,
        "total_warns": total_warns,
    }
