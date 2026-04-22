"""Tests for the per-agent telemetry store."""
import asyncio
import math
import time

from controller.telemetry.store import (
    TelemetryStore,
    STALE_AFTER_SEC,
    OFFLINE_AFTER_SEC,
    _sanitize_norm_pos,
    _bad_norm_pos_logged,
)


def test_sanitize_norm_pos_accepts_valid_range():
    assert _sanitize_norm_pos(0.0) == 0.0
    assert _sanitize_norm_pos(0.5) == 0.5
    assert _sanitize_norm_pos(1.0) == 1.0
    # tiny float slop is clamped, not rejected
    assert _sanitize_norm_pos(1.0001) == 1.0
    assert _sanitize_norm_pos(-0.0005) == 0.0


def test_sanitize_norm_pos_rejects_garbage():
    # The actual symptom from the bug report — `3.99e+28` was reaching the
    # UI as `3.99e+28%`. It MUST be dropped to None here.
    assert _sanitize_norm_pos(3.99e28) is None
    assert _sanitize_norm_pos(1.5) is None
    assert _sanitize_norm_pos(-1.0) is None
    assert _sanitize_norm_pos(float("nan")) is None
    assert _sanitize_norm_pos(float("inf")) is None
    assert _sanitize_norm_pos(float("-inf")) is None
    assert _sanitize_norm_pos(None) is None
    assert _sanitize_norm_pos("garbage") is None


def test_project_for_engine_drops_invalid_norm_pos():
    """End-to-end: a frame with a bonkers normalized_car_position is
    projected with norm_pos=None, NEVER the raw garbage value."""
    _bad_norm_pos_logged.clear()
    s = TelemetryStore()
    asyncio.run(s.update("BadSim", {
        "ts": time.time(), "available": True,
        "physics": {"speed_kmh": 50.0, "rpms": 3000, "gear": 2, "gas": 0.3, "brake": 0.0, "fuel": 10.0},
        "graphics": {"is_in_pit": 0, "i_current_time_ms": 1000, "completed_laps": 0,
                     "normalized_car_position": 3.99e28,  # the actual bug value
                     "coord_x": 0, "coord_y": 0, "coord_z": 0,
                     "status_name": "LIVE", "session_name": "RACE", "tyre_compound": "Hard",
                     "current_sector_index": 0, "i_last_time_ms": 0, "i_best_time_ms": 0,
                     "last_sector_time_ms": 0},
        "static": {"player_nick": "x", "car_model": "y", "track": "z"},
    }))
    proj = s.project_for_engine()
    assert proj["BadSim"]["norm_pos"] is None


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_closed() else asyncio.new_event_loop().run_until_complete(coro)


def test_update_and_get():
    s = TelemetryStore()
    asyncio.run(s.update("Sim1", {
        "available": True, "ts": time.time(),
        "physics": {"speed_kmh": 187.3, "rpms": 7100, "gear": 4, "gas": 0.9, "brake": 0.0, "fuel": 32.1},
        "graphics": {"is_in_pit": 0, "current_sector_index": 1, "i_current_time_ms": 12345,
                     "i_last_time_ms": 81000, "i_best_time_ms": 80500, "completed_laps": 5,
                     "normalized_car_position": 0.42, "coord_x": 1.0, "coord_y": 2.0, "coord_z": 3.0,
                     "status_name": "LIVE", "session_name": "RACE", "tyre_compound": "Soft",
                     "last_sector_time_ms": 27000},
        "static": {"player_nick": "LH44", "car_model": "ks_porsche_911_gt3_r_2016", "track": "spa"},
    }))
    f = s.get("Sim1")
    assert f is not None
    assert f["physics"]["speed_kmh"] == 187.3


def test_all_agents_status_buckets():
    s = TelemetryStore()
    now = time.time()
    asyncio.run(s.update("FRESH", {"ts": now - 0.5, "available": True}))
    asyncio.run(s.update("STALE", {"ts": now - (STALE_AFTER_SEC + 1.0), "available": True}))
    asyncio.run(s.update("DEAD",  {"ts": now - (OFFLINE_AFTER_SEC + 5.0), "available": False}))
    by_id = {a["agent_id"]: a for a in s.all_agents()}
    assert by_id["FRESH"]["status"] == "live"
    assert by_id["STALE"]["status"] == "stale"
    assert by_id["DEAD"]["status"] == "offline"


def test_project_for_engine_omits_offline():
    s = TelemetryStore()
    now = time.time()
    asyncio.run(s.update("LIVE1", {
        "ts": now, "available": True,
        "physics": {"speed_kmh": 100.0, "rpms": 5000, "gear": 3, "gas": 0.5, "brake": 0.0, "fuel": 20.0},
        "graphics": {"is_in_pit": 0, "i_current_time_ms": 5000, "completed_laps": 2,
                     "normalized_car_position": 0.1, "coord_x": 0, "coord_y": 0, "coord_z": 0,
                     "status_name": "LIVE", "session_name": "RACE", "tyre_compound": "Hard",
                     "current_sector_index": 0, "i_last_time_ms": 0, "i_best_time_ms": 0,
                     "last_sector_time_ms": 0},
        "static": {"player_nick": "x", "car_model": "y", "track": "z"},
    }))
    asyncio.run(s.update("OLD", {"ts": now - (OFFLINE_AFTER_SEC + 1.0), "available": True}))
    proj = s.project_for_engine()
    assert "LIVE1" in proj
    assert "OLD" not in proj
    assert proj["LIVE1"]["speed_kmh"] == 100.0
    assert proj["LIVE1"]["stale"] is False


if __name__ == "__main__":
    test_update_and_get()
    test_all_agents_status_buckets()
    test_project_for_engine_omits_offline()
    print("ALL TESTS PASS")
