"""
Minimal tests for event_store: append and query.
Run from repo root: python -m pytest controller/tests/test_event_store.py -v
Or: python -m controller.tests.test_event_store (self-test at bottom, no pytest required).
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from controller.common.event_log import LogCategory, LogLevel, make_event
from controller.service.event_store import append_event, query_events, query_summary_last_minutes

try:
    import pytest
except ImportError:
    pytest = None


def _patch_events_dir(tmp_path):
    return patch("controller.service.event_store._events_dir", return_value=tmp_path)


if pytest is not None:
    @pytest.fixture
    def temp_events_dir(tmp_path):
        with _patch_events_dir(tmp_path):
            yield tmp_path


def test_append_and_query(temp_events_dir=None):
    if temp_events_dir is None:
        return  # skip when run without fixture (e.g. __main__ uses inline test)
    e1 = make_event(LogLevel.INFO, LogCategory.SYSTEM, "Controller", "Test event", rig_id="Sim1")
    append_event(e1)
    e2 = make_event(LogLevel.ERROR, LogCategory.PRESET, "Agent", "Preset missing", rig_id="Sim2", event_code="PRESET_STEERING_MISSING")
    append_event(e2)
    events = query_events(since_minutes=60, limit=10)
    assert len(events) >= 2
    messages = [x.message for x in events]
    assert "Test event" in messages
    assert "Preset missing" in messages


def test_query_filter_level(temp_events_dir=None):
    if temp_events_dir is None:
        return
    e1 = make_event(LogLevel.INFO, LogCategory.SYSTEM, "Controller", "Info only")
    e2 = make_event(LogLevel.ERROR, LogCategory.ERROR, "Agent", "Error only", rig_id="Sim1")
    append_event(e1)
    append_event(e2)
    errors = query_events(since_minutes=60, limit=10, level=LogLevel.ERROR)
    assert all(x.level == LogLevel.ERROR for x in errors)
    assert any(x.message == "Error only" for x in errors)


def test_summary(temp_events_dir=None):
    if temp_events_dir is None:
        return
    append_event(make_event(LogLevel.ERROR, LogCategory.PRESET, "Agent", "Err", rig_id="Sim1"))
    append_event(make_event(LogLevel.WARN, LogCategory.SESSION, "Agent", "Warn"))
    summary = query_summary_last_minutes(60)
    assert summary["total_errors"] >= 1
    assert summary["total_warns"] >= 1
    assert "Sim1" in summary["errors_by_rig"] or "_" in summary["errors_by_rig"]


if __name__ == "__main__":
    # Self-test with temp dir (no pytest required)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with _patch_events_dir(tmp):
            e1 = make_event(LogLevel.INFO, LogCategory.SYSTEM, "Controller", "Test event", rig_id="Sim1")
            append_event(e1)
            events = query_events(since_minutes=60, limit=10)
            assert len(events) >= 1 and any(x.message == "Test event" for x in events)
            print("append and query: ok")
            summary = query_summary_last_minutes(60)
            assert "total_errors" in summary
            print("summary: ok")
    print("Self-tests passed.")
