"""Operator auth: TestClient is non-loopback; without password remote mutations are forbidden."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import MSG_OPERATOR_REMOTE_DISABLED, MSG_OPERATOR_SIGN_IN


class TestOperatorAuthHttp(unittest.TestCase):
    @patch("controller.operator_auth.get_employee_password_optional", return_value=None)
    def test_stop_returns_403_when_no_employee_password_and_not_localhost(self, _mock_pw):
        client = TestClient(app)
        resp = client.post("/api/stop", json={"all": True})
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json().get("detail"), MSG_OPERATOR_REMOTE_DISABLED)

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_stop_returns_401_with_short_message_when_no_cookie(self, _mock_pw):
        client = TestClient(app)
        resp = client.post("/api/stop", json={"all": True})
        self.assertEqual(resp.status_code, 401, resp.text)
        self.assertEqual(resp.json().get("detail"), MSG_OPERATOR_SIGN_IN)

    def test_employee_session_shape(self):
        client = TestClient(app)
        resp = client.get("/api/employee/session")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("employee_login_enabled", data)
        self.assertIn("logged_in", data)


if __name__ == "__main__":
    unittest.main()
