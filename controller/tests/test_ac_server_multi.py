"""
Minimal sanity checks for multi-server AC server start behavior:
- Starting same server_id twice returns "Already running" and does not spawn a second process.
- Missing server_cfg.ini or [SERVER] section returns expected HTTP error.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from controller import api_server_config_routes as server_cfg_routes


class TestReadPortsFromPreset(unittest.TestCase):
    """_read_ports_from_preset must fail fast when server_cfg.ini missing or [SERVER] missing."""

    def test_missing_server_cfg_ini_raises_404(self):
        from controller.api_routes import _read_ports_from_preset

        with tempfile.TemporaryDirectory() as tmp:
            preset_dir = Path(tmp)
            # No server_cfg.ini in preset_dir
            with self.assertRaises(HTTPException) as ctx:
                _read_ports_from_preset(preset_dir)
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("server_cfg.ini not found", ctx.exception.detail)

    def test_missing_server_section_raises_400(self):
        from controller.api_routes import _read_ports_from_preset

        with tempfile.TemporaryDirectory() as tmp:
            preset_dir = Path(tmp)
            sc_path = preset_dir / "server_cfg.ini"
            sc_path.write_text("[OTHER]\nKEY=1\n", encoding="utf-8")
            with self.assertRaises(HTTPException) as ctx:
                _read_ports_from_preset(preset_dir)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("[SERVER] section missing", ctx.exception.detail)


class TestAcServerStartAlreadyRunning(unittest.TestCase):
    """Starting same server_id when already running returns Already running and does not spawn second process."""

    def test_already_running_returns_message_and_does_not_call_popen(self):
        from controller import api_routes

        server_id = "TEST_ALREADY_RUNNING"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345
        mock_instance = MagicMock()
        mock_instance.proc = mock_proc
        mock_instance.pid = 12345
        mock_instance.udp_port = 9600
        mock_instance.tcp_port = 9600
        mock_instance.http_port = 8081
        mock_instance.preset_path = Path(".")
        mock_instance.started_at = 1000.0

        with tempfile.TemporaryDirectory() as tmp:
            preset_dir = Path(tmp)
            (preset_dir / "server_cfg.ini").write_text(
                "[SERVER]\nUDP_PORT=9600\nTCP_PORT=9600\nHTTP_PORT=8081\n",
                encoding="utf-8",
            )
            server_root = Path(tmp) / "root"
            server_root.mkdir()
            (server_root / "acServer.exe").write_text("", encoding="utf-8")

            with api_routes._running_servers_lock:
                api_routes._running_servers[server_id] = mock_instance
            try:
                with patch.object(server_cfg_routes, "_get_server_preset_dir_safe", return_value=preset_dir), \
                     patch.object(server_cfg_routes, "_server_root_for_ac_server", return_value=server_root), \
                     patch("controller.api_server_config_routes.subprocess.Popen") as mock_popen:
                    result = api_routes._ac_server_start(server_id)
                    self.assertEqual(result.get("message"), "Already running")
                    self.assertEqual(result.get("pid"), 12345)
                    mock_popen.assert_not_called()
            finally:
                with api_routes._running_servers_lock:
                    api_routes._running_servers.pop(server_id, None)


class TestAcServerStartPortCollision(unittest.TestCase):
    """Starting server B with same ports as running server A returns 400 and does not call Popen."""

    def test_same_ports_as_running_server_returns_400_and_does_not_call_popen(self):
        from controller import api_routes

        server_a = "SERVER_A"
        server_b = "SERVER_B"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_instance = MagicMock()
        mock_instance.proc = mock_proc
        mock_instance.udp_port = 9600
        mock_instance.tcp_port = 9600
        mock_instance.http_port = 8081
        mock_instance.preset_path = Path(".")
        mock_instance.started_at = 1000.0

        with tempfile.TemporaryDirectory() as tmp:
            preset_dir = Path(tmp)
            (preset_dir / "server_cfg.ini").write_text(
                "[SERVER]\nUDP_PORT=9600\nTCP_PORT=9600\nHTTP_PORT=8081\n",
                encoding="utf-8",
            )
            server_root = Path(tmp) / "root"
            server_root.mkdir()
            (server_root / "acServer.exe").write_text("", encoding="utf-8")

            with api_routes._running_servers_lock:
                api_routes._running_servers[server_a] = mock_instance
            try:
                with patch.object(server_cfg_routes, "_get_server_preset_dir_safe", return_value=preset_dir), \
                     patch.object(server_cfg_routes, "_server_root_for_ac_server", return_value=server_root), \
                     patch("controller.api_server_config_routes.subprocess.Popen") as mock_popen:
                    with self.assertRaises(HTTPException) as ctx:
                        api_routes._ac_server_start(server_b)
                    self.assertEqual(ctx.exception.status_code, 400)
                    self.assertIn("already in use", ctx.exception.detail)
                    self.assertIn(server_a, ctx.exception.detail)
                    mock_popen.assert_not_called()
            finally:
                with api_routes._running_servers_lock:
                    api_routes._running_servers.pop(server_a, None)


if __name__ == "__main__":
    unittest.main()
