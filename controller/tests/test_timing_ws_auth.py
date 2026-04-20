"""Phase 10: timing WebSocket honors the same auth policy as timing HTTP.

Policy (from ``operator_auth.require_operator_if_password_configured``):
- ``employee_password`` unset  -> open to all LAN clients
- ``employee_password`` set    -> requires ``pitbox_employee=1`` cookie

These tests verify HTTP/WS parity: a client allowed on /api/timing/snapshot
must be allowed on /ws/timing, and a client denied on the HTTP route must be
denied on the WS handshake too.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from controller.main import app


HTTP_PATH = "/api/timing/snapshot"
WS_PATH = "/ws/timing"


class TestTimingWsAuthParity(unittest.TestCase):
    # ---- password NOT configured: both HTTP and WS open ---------------------

    @patch("controller.operator_auth.get_employee_password_optional", return_value=None)
    def test_http_open_when_no_password(self, _pw):
        client = TestClient(app)
        resp = client.get(HTTP_PATH)
        self.assertEqual(resp.status_code, 200, resp.text)

    @patch("controller.operator_auth.get_employee_password_optional", return_value=None)
    def test_ws_open_when_no_password(self, _pw):
        client = TestClient(app)
        with client.websocket_connect(WS_PATH) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "snapshot")
            self.assertIn("data", msg)

    # ---- password configured, NO cookie: both HTTP and WS denied ------------

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_http_denied_when_password_set_and_no_cookie(self, _pw):
        client = TestClient(app)
        resp = client.get(HTTP_PATH)
        self.assertEqual(resp.status_code, 401, resp.text)

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_ws_denied_when_password_set_and_no_cookie(self, _pw):
        client = TestClient(app)
        # Starlette closes the WS before accept -> the connect raises
        # WebSocketDisconnect with the policy-violation code.
        with self.assertRaises(WebSocketDisconnect) as cm:
            with client.websocket_connect(WS_PATH):
                pass
        self.assertEqual(cm.exception.code, 1008)

    # ---- password configured WITH valid cookie: both HTTP and WS allowed ----

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_http_allowed_when_password_set_and_cookie_present(self, _pw):
        client = TestClient(app, cookies={"pitbox_employee": "1"})
        resp = client.get(HTTP_PATH)
        self.assertEqual(resp.status_code, 200, resp.text)

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_ws_allowed_when_password_set_and_cookie_present(self, _pw):
        client = TestClient(app, cookies={"pitbox_employee": "1"})
        with client.websocket_connect(WS_PATH) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "snapshot")

    # ---- password configured with WRONG cookie value: denied ----------------

    @patch("controller.operator_auth.get_employee_password_optional", return_value="pw")
    def test_ws_denied_when_cookie_value_wrong(self, _pw):
        client = TestClient(app, cookies={"pitbox_employee": "0"})
        with self.assertRaises(WebSocketDisconnect) as cm:
            with client.websocket_connect(WS_PATH):
                pass
        self.assertEqual(cm.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()
