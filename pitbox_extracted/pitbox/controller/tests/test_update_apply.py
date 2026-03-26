"""
Test POST /api/update/apply accepts no body and empty JSON body.
Run: python -m pytest controller/tests/test_update_apply.py -v
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.tests.operator_testutil import (
    clear_operator_auth_override,
    install_operator_auth_override,
)


class TestUpdateApply(unittest.TestCase):
    """POST /api/update/apply must accept no body and {} without validation error."""

    def setUp(self):
        install_operator_auth_override()

    def tearDown(self):
        clear_operator_auth_override()

    @patch("controller.api_routes.apply_controller_update", return_value=(True, "Installer launched"))
    def test_post_update_apply_no_body_returns_200(self, mock_apply):
        """POST with no body returns 200 and triggers controller update."""
        client = TestClient(app)
        resp = client.post("/api/update/apply")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("message", data)
        mock_apply.assert_called_once()

    @patch("controller.api_routes.apply_controller_update", return_value=(True, "Installer launched"))
    def test_post_update_apply_empty_json_returns_200(self, mock_apply):
        """POST with empty JSON body {} returns 200 and triggers controller update."""
        client = TestClient(app)
        resp = client.post("/api/update/apply", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        mock_apply.assert_called_once()

    @patch("controller.api_routes.apply_controller_update", return_value=(True, "Installer launched"))
    def test_post_update_apply_with_target_controller_returns_200(self, mock_apply):
        """POST with {\"target\": \"controller\"} returns 200 (backward compatible)."""
        client = TestClient(app)
        resp = client.post("/api/update/apply", json={"target": "controller"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        mock_apply.assert_called_once()

    @patch("controller.api_routes.apply_controller_update", return_value=(True, "Installer launched"))
    def test_post_update_apply_wrong_target_returns_400(self, mock_apply):
        """POST with target other than controller returns 400."""
        client = TestClient(app)
        resp = client.post("/api/update/apply", json={"target": "agent"})
        self.assertEqual(resp.status_code, 400)
        mock_apply.assert_not_called()

    @patch("controller.api_routes.apply_controller_update", return_value=(False, "No SHA-256 in release"))
    def test_post_update_apply_surfaces_updater_error_detail(self, mock_apply):
        """POST returns 400 with updater message when apply fails (e.g. missing checksum metadata)."""
        client = TestClient(app)
        resp = client.post("/api/update/apply", json={})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("detail", data)
        self.assertIn("SHA-256", data["detail"])
