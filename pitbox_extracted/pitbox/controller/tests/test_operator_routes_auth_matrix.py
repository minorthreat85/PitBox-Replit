"""
Operator auth matrix for dangerous POST routes: unauthenticated callers must be denied.

TestClient uses a non-loopback client host, so require_operator denies when
employee_password is unset (403) or set without cookie (401).
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.operator_auth import EMPLOYEE_COOKIE


def _client_with_operator_cookie() -> TestClient:
    c = TestClient(app)
    c.cookies.set(EMPLOYEE_COOKIE, "1")
    return c


class TestOperatorRoutesAuthMatrix(unittest.TestCase):
    """Each route: no password -> 403; password no cookie -> 401; password + cookie -> not 401/403."""

    def _deny_no_password(self, method: str, path: str, **kw):
        with patch("controller.operator_auth.get_employee_password_optional", return_value=None):
            c = TestClient(app)
            r = c.request(method, path, **kw)
            self.assertEqual(r.status_code, 403, f"{path}: expected 403, got {r.status_code} {r.text}")

    def _deny_password_no_cookie(self, method: str, path: str, **kw):
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            c = TestClient(app)
            r = c.request(method, path, **kw)
            self.assertEqual(r.status_code, 401, f"{path}: expected 401, got {r.status_code} {r.text}")

    def test_start_matrix(self):
        body = {"all": True}
        self._deny_no_password("POST", "/api/start", json=body)
        self._deny_password_no_cookie("POST", "/api/start", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.send_agent_command", new_callable=AsyncMock, return_value={"success": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/start", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_stop_matrix(self):
        body = {"all": True}
        self._deny_no_password("POST", "/api/stop", json=body)
        self._deny_password_no_cookie("POST", "/api/stop", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.send_agent_command", new_callable=AsyncMock, return_value={"success": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/stop", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_apply_steering_matrix(self):
        body = {"sim_id": "Sim1", "preset_name": "Race"}
        self._deny_no_password("POST", "/api/apply-steering", json=body)
        self._deny_password_no_cookie("POST", "/api/apply-steering", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.send_agent_command", new_callable=AsyncMock, return_value={"success": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/apply-steering", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_apply_shifting_matrix(self):
        body = {"sim_id": "Sim1", "preset_name": "H-Pattern"}
        self._deny_no_password("POST", "/api/apply-shifting", json=body)
        self._deny_password_no_cookie("POST", "/api/apply-shifting", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.send_agent_command", new_callable=AsyncMock, return_value={"success": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/apply-shifting", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_launch_online_matrix(self):
        body = {"car_id": "some_car"}
        self._deny_no_password("POST", "/api/agents/Sim1/launch_online", json=body)
        self._deny_password_no_cookie("POST", "/api/agents/Sim1/launch_online", json=body)

    def test_reset_rig_matrix(self):
        body = {"sim_id": "Sim1"}
        self._deny_no_password("POST", "/api/reset-rig", json=body)
        self._deny_password_no_cookie("POST", "/api/reset-rig", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.send_agent_command", new_callable=AsyncMock, return_value={"success": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/reset-rig", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_enrollment_matrix(self):
        body = {"enabled": False}
        self._deny_no_password("POST", "/api/enrollment", json=body)
        self._deny_password_no_cookie("POST", "/api/enrollment", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.stop_enrollment"):
                c = _client_with_operator_cookie()
                r = c.post("/api/enrollment", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_update_apply_matrix(self):
        self._deny_no_password("POST", "/api/update/apply", json={})
        self._deny_password_no_cookie("POST", "/api/update/apply", json={})
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.apply_controller_update", return_value=(True, "ok")):
                c = _client_with_operator_cookie()
                r = c.post("/api/update/apply", json={})
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_update_run_installer_matrix(self):
        self._deny_no_password("POST", "/api/update/run-installer")
        self._deny_password_no_cookie("POST", "/api/update/run-installer")
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.run_unified_installer_update", return_value=(True, "ok")):
                c = _client_with_operator_cookie()
                r = c.post("/api/update/run-installer")
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_server_config_load_preset_matrix(self):
        body = {"server_id": "default", "preset_name": "SERVER_01"}
        self._deny_no_password("POST", "/api/server-config/load-preset", json=body)
        self._deny_password_no_cookie("POST", "/api/server-config/load-preset", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            from pathlib import Path
            with patch("controller.api_routes.get_ac_server_presets_root", return_value=Path("/nonexistent_presets_root")):
                c = _client_with_operator_cookie()
                r = c.post("/api/server-config/load-preset", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_server_create_matrix(self):
        body = {"track": "t", "car": "c", "players": 2}
        self._deny_no_password("POST", "/api/server/create", json=body)
        self._deny_password_no_cookie("POST", "/api/server/create", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.pool_manager.create_server", return_value={"ok": True}):
                c = _client_with_operator_cookie()
                r = c.post("/api/server/create", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)

    def test_server_release_matrix(self):
        body = {"server_number": 1}
        self._deny_no_password("POST", "/api/server/release", json=body)
        self._deny_password_no_cookie("POST", "/api/server/release", json=body)
        with patch("controller.operator_auth.get_employee_password_optional", return_value="goodpass12"):
            with patch("controller.api_routes.pool_manager.release_server"):
                c = _client_with_operator_cookie()
                r = c.post("/api/server/release", json=body)
                self.assertNotIn(r.status_code, (401, 403), r.text)


if __name__ == "__main__":
    unittest.main()
