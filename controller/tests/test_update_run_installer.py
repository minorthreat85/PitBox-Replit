"""
Test POST /api/update/run-installer behavior.
Run: python -m pytest controller/tests/test_update_run_installer.py -v
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controller.main import app
from controller.tests.operator_testutil import (
    clear_operator_auth_override,
    install_operator_auth_override,
)


class TestUpdateRunInstaller(unittest.TestCase):
    """POST /api/update/run-installer runs the unified installer update."""

    def setUp(self):
        install_operator_auth_override()

    def tearDown(self):
        clear_operator_auth_override()

    @patch("controller.api_routes.run_unified_installer_update", return_value=(True, "Downloading update in background..."))
    def test_post_update_run_installer_returns_200(self, mock_run):
        """POST /api/update/run-installer returns 200 on success."""
        client = TestClient(app)
        resp = client.post("/api/update/run-installer")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("message", data)
        mock_run.assert_called_once()

    @patch("controller.api_routes.run_unified_installer_update", return_value=(False, "No update available"))
    def test_post_update_run_installer_failure_returns_400(self, mock_run):
        """POST /api/update/run-installer returns 400 when updater fails to start."""
        client = TestClient(app)
        resp = client.post("/api/update/run-installer")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("detail", data)
        self.assertEqual(data["detail"], "No update available")
        mock_run.assert_called_once()
