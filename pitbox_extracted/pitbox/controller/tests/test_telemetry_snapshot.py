"""
Test telemetry ingest and timing snapshot: POST telemetry_tick (with mocked agent auth), GET /api/timing/snapshot.
"""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import MSG_OPERATOR_SIGN_IN
from controller.telemetry_store import clear_all


class TestTelemetrySnapshot(unittest.TestCase):
    """POST /api/agents/telemetry and GET /api/timing/snapshot return expected shape."""

    def setUp(self):
        clear_all()

    def test_timing_snapshot_empty_without_telemetry(self):
        """GET /api/timing/snapshot with no telemetry returns type=timing_snapshot and empty cars."""
        client = TestClient(app)
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            resp = client.get("/api/timing/snapshot")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
        self.assertEqual(data.get("type"), "timing_snapshot")
        self.assertEqual(data.get("v"), 1)
        self.assertIn("ts_ms", data)
        self.assertEqual(data.get("cars"), [])

    def test_timing_snapshot_denied_when_password_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="pw"):
            c = TestClient(app)
            r = c.get("/api/timing/snapshot")
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_SIGN_IN)

    @patch("controller.api_routes.require_agent", new_callable=AsyncMock, return_value="Sim5")
    def test_post_telemetry_then_snapshot_has_cars(self, mock_require_agent):
        """POST telemetry_tick then GET snapshot returns cars list with expected driver/car_model."""
        client = TestClient(app)
        tick = {
            "type": "telemetry_tick",
            "v": 1,
            "agent_id": "Sim5",
            "device_id": "Sim-5-xxxx",
            "ts_ms": 1700000000123,
            "seq": 567890,
            "session_key": "online|192.168.1.218:9616|tatuusfa1|Jaiden",
            "car": {"car_id": 0, "driver_name": "Jaiden", "car_model": "tatuusfa1"},
            "timing": {"lap": 3, "lap_time_ms": 74231, "best_lap_ms": 73510, "last_lap_ms": 74880, "sector": 2, "sector_time_ms": 25110},
            "track": {"track_id": "ks_red_bull_ring", "layout": "layout_national", "normalized_pos": 0.6342, "world": {"x": 123.4, "y": 2.1, "z": -88.7}, "speed_kmh": 167.2},
            "car_state": {"gear": 4, "rpm": 8120, "throttle": 0.91, "brake": 0.0, "in_pit": False},
        }
        post_resp = client.post(
            "/api/agents/telemetry",
            json=tick,
            headers={"X-Agent-Id": "Sim5", "X-Agent-Token": "test-token"},
        )
        self.assertIn(post_resp.status_code, (200, 201))
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            snap_resp = client.get("/api/timing/snapshot")
            self.assertEqual(snap_resp.status_code, 200)
            data = snap_resp.json()
        self.assertEqual(data.get("type"), "timing_snapshot")
        cars = data.get("cars") or []
        self.assertGreaterEqual(len(cars), 1)
        first = cars[0]
        self.assertEqual(first.get("driver"), "Jaiden")
        self.assertEqual(first.get("car_model"), "tatuusfa1")
        self.assertIn("live", first)
        self.assertIn("stale_ms", first.get("live", {}))
