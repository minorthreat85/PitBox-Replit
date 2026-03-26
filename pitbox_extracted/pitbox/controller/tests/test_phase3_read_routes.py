"""Phase 3: sensitive GET routes require operator; conditional routes when employee_password is set."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import EMPLOYEE_COOKIE, MSG_OPERATOR_REMOTE_DISABLED, MSG_OPERATOR_SIGN_IN


class TestRegistryAndLogsAlwaysOperator(unittest.TestCase):
    def test_registry_denied_no_password_remote(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            r = c.get("/api/agents/registry")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_REMOTE_DISABLED)

    def test_registry_ok_with_cookie_when_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            c = TestClient(app)
            c.cookies.set(EMPLOYEE_COOKIE, "1")
            r = c.get("/api/agents/registry")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsInstance(r.json(), dict)

    def test_discovered_same_as_registry_matrix(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/agents/discovered")
            self.assertEqual(r.status_code, 403, r.text)

    def test_logs_events_denied_remote_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/logs/events")
            self.assertEqual(r.status_code, 403, r.text)

    def test_logs_events_ok_localhost_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            with patch("controller.operator_auth.is_localhost_request", return_value=True):
                r = TestClient(app).get("/api/logs/events")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIsInstance(r.json(), list)

    def test_logs_summary_denied_remote_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/logs/summary")
            self.assertEqual(r.status_code, 403, r.text)

    def test_presets_info_debug_denied_remote_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            self.assertEqual(c.get("/api/servers/presets-info").status_code, 403)
            self.assertEqual(c.get("/api/debug/presets").status_code, 403)
            self.assertEqual(c.get("/api/debug/favourites").status_code, 403)

    def test_pool_server_status_denied_remote_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/server/status")
            self.assertEqual(r.status_code, 403, r.text)


class TestServerConfigGetConditional(unittest.TestCase):
    def test_meta_public_when_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/server-config/meta")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertIn("server_ids", data)
            self.assertIn("preset_names", data)

    def test_meta_denied_when_password_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            r = TestClient(app).get("/api/server-config/meta")
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_SIGN_IN)


if __name__ == "__main__":
    unittest.main()
