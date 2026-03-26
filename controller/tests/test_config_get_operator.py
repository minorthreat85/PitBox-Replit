"""GET /api/config requires operator; GET /api/status exposes public-safe poll_interval_sec."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import EMPLOYEE_COOKIE, MSG_OPERATOR_REMOTE_DISABLED, MSG_OPERATOR_SIGN_IN


class TestGetConfigOperator(unittest.TestCase):
    def test_denied_when_password_set_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="secret"):
            c = TestClient(app)
            r = c.get("/api/config")
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_SIGN_IN)

    def test_allowed_when_password_set_with_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="secret"):
            c = TestClient(app)
            c.cookies.set(EMPLOYEE_COOKIE, "1")
            r = c.get("/api/config")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertIn("config", body)
            self.assertIn("config_path", body)

    def test_denied_non_loopback_when_no_password(self):
        """TestClient host is not loopback; require_operator forbids remote without employee_password."""
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            r = c.get("/api/config")
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_REMOTE_DISABLED)

    def test_allowed_loopback_when_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            with patch("controller.operator_auth.is_localhost_request", return_value=True):
                c = TestClient(app)
                r = c.get("/api/config")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIn("config", r.json())


class TestStatusPollIntervalPublic(unittest.TestCase):
    def test_status_includes_poll_interval_without_auth(self):
        """Without employee_password, LAN clients may poll /status unauthenticated."""
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            r = c.get("/api/status")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertIn("poll_interval_sec", data)
            sec = data["poll_interval_sec"]
            self.assertIsInstance(sec, float)
            self.assertGreater(sec, 0)
            self.assertLessEqual(sec, 3600)

    def test_status_does_not_include_config_path_or_raw_secrets(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            data = c.get("/api/status").json()
            self.assertNotIn("config_path", data)
            self.assertNotIn("employee_password", data)
            self.assertNotIn("kiosk_secret", data)

    def test_status_denied_when_password_set_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="x"):
            c = TestClient(app)
            r = c.get("/api/status")
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_SIGN_IN)

    def test_status_ok_when_password_set_with_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="x"):
            c = TestClient(app)
            c.cookies.set(EMPLOYEE_COOKIE, "1")
            r = c.get("/api/status")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("agents", r.json())


if __name__ == "__main__":
    unittest.main()
