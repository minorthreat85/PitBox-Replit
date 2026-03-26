"""Employee login redirect path sanitization and session flags for operator LAN workflow."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import EMPLOYEE_COOKIE, sanitize_employee_login_next


class TestSanitizeEmployeeLoginNext(unittest.TestCase):
    def test_root_default(self):
        self.assertEqual(sanitize_employee_login_next(None), "/")
        self.assertEqual(sanitize_employee_login_next(""), "/")
        self.assertEqual(sanitize_employee_login_next("   "), "/")

    def test_allows_safe_paths(self):
        self.assertEqual(sanitize_employee_login_next("/sims"), "/sims")
        self.assertEqual(sanitize_employee_login_next("/?x=1"), "/?x=1")

    def test_rejects_open_redirect_and_schemes(self):
        self.assertEqual(sanitize_employee_login_next("//evil.com"), "/")
        self.assertEqual(sanitize_employee_login_next("https://evil.com"), "/")
        self.assertEqual(sanitize_employee_login_next("/x://y"), "/")

    def test_rejects_login_loop_and_non_paths(self):
        self.assertEqual(sanitize_employee_login_next("/employee/login"), "/")
        self.assertEqual(sanitize_employee_login_next("/employee/login?next=/"), "/")
        self.assertEqual(sanitize_employee_login_next("sims"), "/")

    def test_rejects_newlines(self):
        self.assertEqual(sanitize_employee_login_next("/ok\nLocation:"), "/")


class TestEmployeeLoginApi(unittest.TestCase):
    @patch("controller.api_routes.get_employee_password_optional", return_value="secretpw")
    def test_login_returns_redirect(self, _p):
        c = TestClient(app)
        r = c.post("/api/employee/login", json={"password": "secretpw", "next": "/garage?tab=1"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("redirect"), "/garage?tab=1")

    @patch("controller.api_routes.get_employee_password_optional", return_value="secretpw")
    def test_login_sanitizes_next(self, _p):
        c = TestClient(app)
        r = c.post("/api/employee/login", json={"password": "secretpw", "next": "//evil"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("redirect"), "/")


class TestEmployeeSessionFlags(unittest.TestCase):
    @patch("controller.api_routes.get_employee_password_optional", return_value="x")
    def test_login_required_when_password_and_no_cookie(self, _p):
        c = TestClient(app)
        r = c.get("/api/employee/session")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d.get("employee_login_enabled"))
        self.assertFalse(d.get("logged_in"))
        self.assertTrue(d.get("login_required_for_control"))

    @patch("controller.api_routes.get_employee_password_optional", return_value="x")
    def test_not_required_when_cookie_present(self, _p):
        c = TestClient(app)
        c.cookies.set(EMPLOYEE_COOKIE, "1")
        r = c.get("/api/employee/session")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d.get("logged_in"))
        self.assertFalse(d.get("login_required_for_control"))


if __name__ == "__main__":
    unittest.main()
