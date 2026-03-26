"""Phase 4: GET /enrollment and sensitive catalogs gated when employee_password is set."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import EMPLOYEE_COOKIE, MSG_OPERATOR_SIGN_IN


class TestEnrollmentGetConditional(unittest.TestCase):
    def test_enrollment_public_when_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            r = TestClient(app).get("/api/enrollment")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertIn("enabled", data)
            self.assertIn("secret", data)

    def test_enrollment_401_when_password_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            r = TestClient(app).get("/api/enrollment")
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json().get("detail"), MSG_OPERATOR_SIGN_IN)

    def test_enrollment_ok_when_password_and_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            c = TestClient(app)
            c.cookies.set(EMPLOYEE_COOKIE, "1")
            r = c.get("/api/enrollment")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("enabled", r.json())


class TestCatalogsPublicVsGated(unittest.TestCase):
    def test_cars_tracks_always_200_without_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            rc = c.get("/api/catalogs/cars")
            rt = c.get("/api/catalogs/tracks")
            self.assertEqual(rc.status_code, 200, rc.text)
            self.assertEqual(rt.status_code, 200, rt.text)
            self.assertIn("cars", rc.json())
            self.assertIn("tracks", rt.json())

    def test_assists_servers_public_when_no_password(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            ra = c.get("/api/catalogs/assists")
            rs = c.get("/api/catalogs/servers")
            self.assertEqual(ra.status_code, 200, ra.text)
            self.assertEqual(rs.status_code, 200, rs.text)
            self.assertIn("assists", ra.json())
            self.assertIn("servers", rs.json())

    def test_assists_servers_401_when_password_no_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            c = TestClient(app)
            self.assertEqual(c.get("/api/catalogs/assists").status_code, 401)
            self.assertEqual(c.get("/api/catalogs/servers").status_code, 401)

    def test_assists_servers_ok_when_password_and_cookie(self):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            c = TestClient(app)
            c.cookies.set(EMPLOYEE_COOKIE, "1")
            self.assertEqual(c.get("/api/catalogs/assists").status_code, 200)
            self.assertEqual(c.get("/api/catalogs/servers").status_code, 200)

    def test_cars_tracks_still_200_when_password_no_cookie(self):
        """Picker catalogs stay public so kiosk can load car/track grids without operator cookie."""
        with patch("controller.operator_auth.get_employee_password_optional", return_value="p"):
            c = TestClient(app)
            self.assertEqual(c.get("/api/catalogs/cars").status_code, 200)
            self.assertEqual(c.get("/api/catalogs/tracks").status_code, 200)


if __name__ == "__main__":
    unittest.main()
