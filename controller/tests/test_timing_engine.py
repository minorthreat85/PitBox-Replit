"""Phase 12: deterministic unit tests for the live-timing engine.

Covers the core invariants established by Phases 2, 4, 5, 6, 7, 8 and 11.
Tests exercise the engine directly (no UDP, no network, no real time) by
constructing a TimingEngine, calling _record_event / mutating drivers /
patching last_packet_unix, then asserting on snapshot() and events_since().
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from controller.timing.engine import (
    DriverState,
    TimingEngine,
    _RESYNC_STALE_AFTER_S,
    _RESYNC_STARTUP_GRACE_S,
    _TIMING_HEALTH_LIVE_S,
    _TIMING_HEALTH_OFFLINE_S,
)


CANONICAL_EVENT_FIELDS = {"seq", "ts", "type", "car_id", "driver", "track", "lap_ms", "payload"}


def _fresh_engine() -> TimingEngine:
    """Return an isolated TimingEngine (avoids the module singleton)."""
    return TimingEngine()


class TestCanonicalEventSchema(unittest.TestCase):
    """Phase 2: every event has the same top-level shape; no aliases."""

    def test_lap_completed_has_canonical_fields(self):
        eng = _fresh_engine()
        eng._record_event(
            "lap_completed", car_id=3, driver="Alice", lap_ms=123456,
            cuts=0, total_laps=5, position=1, grip_level=0.99,
        )
        ev = eng.events_since(0)["events"][-1]
        self.assertEqual(set(ev.keys()), CANONICAL_EVENT_FIELDS)
        self.assertEqual(ev["type"], "lap_completed")
        self.assertEqual(ev["car_id"], 3)
        self.assertEqual(ev["driver"], "Alice")
        self.assertEqual(ev["lap_ms"], 123456)
        # Per-type extras land in payload, not top-level
        self.assertEqual(ev["payload"]["cuts"], 0)
        self.assertEqual(ev["payload"]["position"], 1)
        # No legacy aliases
        self.assertNotIn("kind", ev)
        self.assertNotIn("driver_name", ev)

    def test_session_and_connection_events_have_canonical_fields(self):
        eng = _fresh_engine()
        eng._record_event("new_session", session_name="Race", session_type="Race", layout="")
        eng._record_event("driver_connected", car_id=0, driver="Bob", car_model="ferrari")
        eng._record_event("driver_disconnected", car_id=0, driver="Bob")
        events = eng.events_since(0)["events"]
        self.assertEqual(len(events), 3)
        for ev in events:
            self.assertEqual(set(ev.keys()), CANONICAL_EVENT_FIELDS)
            self.assertNotIn("kind", ev)
        types = [e["type"] for e in events]
        self.assertEqual(types, ["new_session", "driver_connected", "driver_disconnected"])


class TestEventCursorMonotonicity(unittest.TestCase):
    """Phase 9: shared cursor only moves forward; no duplicates across reads."""

    def test_seq_strictly_increases(self):
        eng = _fresh_engine()
        for i in range(5):
            eng._record_event("chat", car_id=0, driver="x", message=str(i))
        seqs = [e["seq"] for e in eng.events_since(0)["events"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))  # all distinct

    def test_events_since_filters_out_already_seen(self):
        eng = _fresh_engine()
        for i in range(3):
            eng._record_event("chat", message=str(i))
        first = eng.events_since(0)
        cursor = first["next_seq"]
        # No new events: events_since(cursor) returns empty list, same next_seq.
        second = eng.events_since(cursor)
        self.assertEqual(second["events"], [])
        self.assertEqual(second["next_seq"], cursor)
        # Add one more; only that one should come back.
        eng._record_event("chat", message="last")
        third = eng.events_since(cursor)
        self.assertEqual(len(third["events"]), 1)
        self.assertEqual(third["events"][0]["payload"]["message"], "last")
        self.assertGreater(third["next_seq"], cursor)

    def test_transport_overlap_simulation(self):
        """Simulate WS delivers seq 10-12, fallback HTTP returns 11-13.

        Engine is the single source of truth for seq. The frontend's
        consumeEvents() does the dedupe; here we verify the BACKEND never
        emits the same seq twice and that events_since with a higher cursor
        skips earlier ones.
        """
        eng = _fresh_engine()
        for i in range(13):
            eng._record_event("chat", message=str(i))
        # "WS" sees up to seq 12 -> cursor 12
        ws_view = eng.events_since(9, limit=200)
        self.assertEqual([e["seq"] for e in ws_view["events"]], [10, 11, 12, 13])
        # "HTTP fallback" later asks since cursor=12; only seq 13 returns.
        http_view = eng.events_since(12, limit=200)
        self.assertEqual([e["seq"] for e in http_view["events"]], [13])


class TestSnapshotMonotonicOrdering(unittest.TestCase):
    """Phase 6: snapshot_seq strictly increases per call."""

    def test_snapshot_seq_increments_each_call(self):
        eng = _fresh_engine()
        seqs = [eng.snapshot()["snapshot_seq"] for _ in range(5)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_generated_unix_present_and_monotonic(self):
        eng = _fresh_engine()
        s1 = eng.snapshot()
        s2 = eng.snapshot()
        self.assertIn("generated_unix", s1)
        self.assertGreaterEqual(s2["generated_unix"], s1["generated_unix"])


class TestGapIntervalEdgeCases(unittest.TestCase):
    """Phase 5: backend is authoritative; rules verified end-to-end via snapshot()."""

    def _seed(self, eng: TimingEngine, rows):
        """rows = [(car_id, total_laps, gap_ms, position)]"""
        for car_id, laps, gap, pos in rows:
            d = DriverState(car_id=car_id)
            d.connected = True
            d.total_laps = laps
            d.gap_ms = gap
            d.position = pos
            d.driver_name = f"D{car_id}"
            eng.drivers[car_id] = d

    def test_no_completed_laps_renders_dash(self):
        eng = _fresh_engine()
        self._seed(eng, [(1, 0, 0, 1)])
        d = eng.snapshot()["drivers"][0]
        self.assertIsNone(d["gap_to_leader_ms"])
        self.assertIsNone(d["interval_to_ahead_ms"])

    def test_leader_has_zero_gap_and_null_interval(self):
        eng = _fresh_engine()
        self._seed(eng, [(1, 5, 0, 1), (2, 5, 1500, 2)])
        snap = eng.snapshot()
        leader, second = snap["drivers"][0], snap["drivers"][1]
        self.assertEqual(leader["gap_to_leader_ms"], 0)
        self.assertIsNone(leader["interval_to_ahead_ms"])
        self.assertEqual(second["gap_to_leader_ms"], 1500)
        self.assertEqual(second["interval_to_ahead_ms"], 1500)

    def test_interval_is_never_negative(self):
        """Truly non-monotonic raw gap_ms must clamp to >= 0, not go negative."""
        eng = _fresh_engine()
        # P2's raw gap (1200) is LARGER than P3's (500) — pathological ordering
        # that would produce interval=-700 if the engine naively did p3.gap-p2.gap.
        self._seed(eng, [(1, 5, 0, 1), (2, 5, 1200, 2), (3, 5, 500, 3)])
        snap = eng.snapshot()
        by_pos = {d["position"]: d for d in snap["drivers"]}
        self.assertIsNone(by_pos[1]["interval_to_ahead_ms"])
        for pos in (2, 3):
            self.assertGreaterEqual(by_pos[pos]["interval_to_ahead_ms"], 0)

    def test_mixed_laps_partial_grid(self):
        """No-lap drivers report gap=None; ordering is owned by AC `position`."""
        eng = _fresh_engine()
        self._seed(eng, [(1, 3, 0, 1), (2, 0, 0, 2), (3, 3, 800, 3)])
        snap = eng.snapshot()
        by_car = {d["car_id"]: d for d in snap["drivers"]}
        self.assertEqual(by_car[1]["gap_to_leader_ms"], 0)
        self.assertIsNone(by_car[2]["gap_to_leader_ms"])
        self.assertIsNone(by_car[2]["interval_to_ahead_ms"])
        self.assertEqual(by_car[3]["gap_to_leader_ms"], 800)


class TestTimingHealthSemantics(unittest.TestCase):
    """Phase 7: backend is the only source of truth for live/stale/offline."""

    def test_no_packets_yet_is_offline(self):
        eng = _fresh_engine()
        h = eng.snapshot()["health"]["timing"]
        self.assertEqual(h["state"], "offline")
        self.assertIsNone(h["last_packet_age_s"])

    def test_recent_packet_is_live(self):
        eng = _fresh_engine()
        eng.last_packet_unix = time.time() - 1.0
        self.assertEqual(eng.snapshot()["health"]["timing"]["state"], "live")

    def test_above_live_threshold_is_stale(self):
        eng = _fresh_engine()
        eng.last_packet_unix = time.time() - (_TIMING_HEALTH_LIVE_S + 2.0)
        self.assertEqual(eng.snapshot()["health"]["timing"]["state"], "stale")

    def test_above_offline_threshold_is_offline(self):
        eng = _fresh_engine()
        eng.last_packet_unix = time.time() - (_TIMING_HEALTH_OFFLINE_S + 5.0)
        self.assertEqual(eng.snapshot()["health"]["timing"]["state"], "offline")

    def test_disconnected_driver_freshness_is_offline_even_when_feed_live(self):
        eng = _fresh_engine()
        eng.last_packet_unix = time.time() - 0.1  # global feed = live
        d = DriverState(car_id=1, connected=False, total_laps=0)
        eng.drivers[1] = d
        snap = eng.snapshot()
        self.assertEqual(snap["health"]["timing"]["state"], "live")
        self.assertEqual(snap["drivers"][0]["freshness"]["timing_state"], "offline")

    def test_connected_driver_freshness_follows_global_feed(self):
        eng = _fresh_engine()
        eng.last_packet_unix = time.time() - (_TIMING_HEALTH_LIVE_S + 2.0)  # stale
        d = DriverState(car_id=1, connected=True, total_laps=1)
        eng.drivers[1] = d
        snap = eng.snapshot()
        self.assertEqual(snap["drivers"][0]["freshness"]["timing_state"], "stale")
        self.assertEqual(snap["drivers"][0]["freshness"]["telemetry_state"], "missing")


class TestResyncTriggers(unittest.TestCase):
    """Phase 4: cold_start vs stale_feed; bounded; idempotent under healthy feed."""

    def test_not_started_returns_none(self):
        eng = _fresh_engine()
        self.assertIsNone(eng._resync_diagnose(time.time()))

    def test_cold_start_only_after_grace(self):
        eng = _fresh_engine()
        eng._started_at_unix = 1000.0
        # Inside grace window -> no nudge yet.
        self.assertIsNone(eng._resync_diagnose(1000.0 + _RESYNC_STARTUP_GRACE_S - 0.1))
        # Past grace window with no packets -> cold_start.
        self.assertEqual(
            eng._resync_diagnose(1000.0 + _RESYNC_STARTUP_GRACE_S + 0.1),
            "cold_start",
        )

    def test_stale_feed_after_threshold(self):
        eng = _fresh_engine()
        eng._started_at_unix = 1000.0
        eng.last_packet_unix = 1000.0
        # Recent packet -> healthy.
        self.assertIsNone(eng._resync_diagnose(1000.0 + _RESYNC_STALE_AFTER_S - 1.0))
        # No packet for stale threshold -> stale_feed.
        self.assertEqual(
            eng._resync_diagnose(1000.0 + _RESYNC_STALE_AFTER_S + 0.1),
            "stale_feed",
        )

    def test_fresh_packet_clears_stale_signal(self):
        eng = _fresh_engine()
        eng._started_at_unix = 1000.0
        eng.last_packet_unix = 2000.0  # very recent relative to now=2000.5
        self.assertIsNone(eng._resync_diagnose(2000.5))


class TestResyncProbeBoundedness(unittest.TestCase):
    """Resync supervisor must NOT spam: no running servers => no probes fired."""

    def test_no_running_servers_returns_false_without_raising(self):
        import asyncio
        eng = _fresh_engine()
        with patch(
            "controller.api_server_config_routes._get_running_servers_list",
            return_value=[],
        ):
            ok = asyncio.run(eng._fire_resync_probes("cold_start"))
        self.assertFalse(ok)


# ----- Phase 8: selection invalidation is enforced in the FRONTEND only -----
# (applySnapshot in live_timing.js clears state.selectedCarId when the
# selected car is missing from the new snapshot). The backend snapshot is
# unaware of UI selection, so there is nothing to assert in Python here.
# We document the gap below in MANUAL_VALIDATION.md instead of writing a
# brittle JS test harness.


if __name__ == "__main__":
    unittest.main()

def test_compute_map_key_strips_csp_path():
    from controller.timing.engine import _compute_map_key
    # The actual bug input from the report.
    assert _compute_map_key("CSP/3749/.../JR_ROAD_ATLANTA_2022", "FULL") == "jr_road_atlanta_2022__full"
    assert _compute_map_key("csp/3749/x/y/jr_road_atlanta_2022", "full") == "jr_road_atlanta_2022__full"


def test_compute_map_key_simple_cases():
    from controller.timing.engine import _compute_map_key
    assert _compute_map_key("jr_mosport_2021", "") == "jr_mosport_2021"
    assert _compute_map_key("ks_silverstone", "gp") == "ks_silverstone__gp"
    assert _compute_map_key("", "") == ""
    assert _compute_map_key(None, None) == ""


def test_snapshot_includes_map_key():
    from controller.timing.engine import TimingEngine
    eng = TimingEngine()
    eng.session.track_name = "csp/3749/x/jr_road_atlanta_2022"
    eng.session.track_config = "FULL"
    snap = eng.snapshot()
    assert snap["session"]["map_key"] == "jr_road_atlanta_2022__full"
    # backwards compat: raw fields still present, untouched
    assert snap["session"]["track_name"] == "csp/3749/x/jr_road_atlanta_2022"
    assert snap["session"]["track_config"] == "FULL"

