"""
Unit tests for server_cfg.ini → race.ini sync (JR F1 preset sample).
"""
import tempfile
import unittest
from pathlib import Path

from agent.server_cfg_sync import (
    sync_race_ini_from_server_cfg,
    patch_race_ini_for_online_join,
    _normalize_track,
    _validate_selected_car,
)


# Sample server_cfg.ini (JR F1 style - from user's race.ini context)
SAMPLE_SERVER_CFG = {
    "SERVER": {
        "NAME": "Fastest Lap JR F1",
        "PASSWORD": "FastestLap",
        "TRACK": "csp/3749/../H/../ks_red_bull_ring",
        "CONFIG_TRACK": "layout_national",
        "CARS": "tatuusfa1,ks_mazda_mx5_cup",
        "TCP_PORT": "9616",
        "UDP_PORT": "9616",
        "HTTP_PORT": "8098",
        "SUN_ANGLE": "-16.00",
        "TIME_OF_DAY_MULT": "1.0",
    },
    "DYNAMIC_TRACK": {
        "SESSION_START": "100",
        "RANDOMNESS": "0",
        "LAP_GAIN": "1",
        "SESSION_TRANSFER": "100",
    },
    "WEATHER_0": {
        "GRAPHICS": "sol_05_broken_clouds",
        "BASE_TEMPERATURE_AMBIENT": "26",
        "BASE_TEMPERATURE_ROAD": "27",
        "WIND_SPEED": "0",
        "WIND_DIRECTION": "159",
    },
}

# Sample existing race.ini (from different server - should be overwritten for join target)
SAMPLE_RACE_INI = """
[RACE]
AI_LEVEL=100
CARS=1
CONFIG_TRACK=classic_circuit
MODEL=ks_pagani_huayra_bc
TRACK=ks_vallelunga

[REMOTE]
ACTIVE=1
SERVER_IP=10.0.0.99
SERVER_PORT=9600
GUID=76561199806773536
NAME=Caitlin
PASSWORD=OldPassword
REQUESTED_CAR=ks_pagani_huayra_bc
SERVER_NAME=Fastest Lap Monthly Challenge

[OPTIONS]
USE_MPH=0

[CAR_0]
MODEL=ks_pagani_huayra_bc
SKIN=blue_carbon_silver
DRIVER_NAME=Caitlin
"""


class TestNormalizeTrack(unittest.TestCase):
    def test_csp_path(self):
        self.assertEqual(_normalize_track("csp/3749/../H/../ks_red_bull_ring"), "ks_red_bull_ring")

    def test_simple_track(self):
        self.assertEqual(_normalize_track("ks_vallelunga"), "ks_vallelunga")

    def test_empty(self):
        self.assertEqual(_normalize_track(""), "unknown")


class TestValidateSelectedCar(unittest.TestCase):
    def test_selected_in_list(self):
        self.assertEqual(_validate_selected_car("tatuusfa1", "tatuusfa1,ks_mazda_mx5_cup"), "tatuusfa1")

    def test_selected_not_in_list_returns_first(self):
        self.assertEqual(_validate_selected_car("unknown_car", "tatuusfa1,ks_mazda_mx5_cup"), "tatuusfa1")

    def test_empty_selected_returns_first(self):
        self.assertEqual(_validate_selected_car("", "tatuusfa1,ks_mazda_mx5_cup"), "tatuusfa1")


class TestSyncRaceIni(unittest.TestCase):
    def test_sync_updates_remote_and_track(self):
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text(SAMPLE_RACE_INI, encoding="utf-8")
            sync_race_ini_from_server_cfg(
                SAMPLE_SERVER_CFG,
                join_ip="192.168.1.218",
                join_port=9616,
                selected_car="tatuusfa1",
                race_ini_path=race_path,
                preset_name="JR F1",
            )
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("SERVER_IP=192.168.1.218", text)
            self.assertIn("SERVER_PORT=9616", text)
            self.assertIn("TRACK=ks_red_bull_ring", text)
            self.assertIn("CONFIG_TRACK=layout_national", text)
            self.assertIn("REQUESTED_CAR=tatuusfa1", text)
            self.assertIn("SERVER_NAME=Fastest Lap JR F1", text)
            self.assertIn("PASSWORD=FastestLap", text)
            self.assertIn("ACTIVE=1", text)
            # Old values replaced
            self.assertNotIn("10.0.0.99", text)
            self.assertNotIn("OldPassword", text)
            self.assertNotIn("Fastest Lap Monthly Challenge", text)
            # User-local preserved
            self.assertIn("USE_MPH=0", text)
            self.assertIn("NAME=Caitlin", text)
            self.assertIn("GUID=76561199806773536", text)

    def test_sync_empty_password_writes_empty(self):
        cfg = {**SAMPLE_SERVER_CFG, "SERVER": {**SAMPLE_SERVER_CFG["SERVER"], "PASSWORD": ""}}
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text("[REMOTE]\nPASSWORD=OldPass\n", encoding="utf-8")
            sync_race_ini_from_server_cfg(
                cfg, "192.168.1.218", 9616, "tatuusfa1", race_path, preset_name="NoPass"
            )
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("PASSWORD=", text)
            self.assertNotIn("OldPass", text)

    def test_sync_config_track_blank_when_missing(self):
        """CONFIG_TRACK should be blank when not in server_cfg, not 'default'."""
        cfg = {"SERVER": {k: v for k, v in SAMPLE_SERVER_CFG["SERVER"].items() if k != "CONFIG_TRACK"}}
        cfg["SERVER"]["TRACK"] = "ks_red_bull_ring"
        cfg["SERVER"]["CARS"] = "tatuusfa1"
        cfg["DYNAMIC_TRACK"] = SAMPLE_SERVER_CFG["DYNAMIC_TRACK"]
        cfg["WEATHER_0"] = SAMPLE_SERVER_CFG["WEATHER_0"]
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text("[RACE]\nTRACK=old\nCONFIG_TRACK=old_layout\n", encoding="utf-8")
            sync_race_ini_from_server_cfg(
                cfg, "192.168.1.218", 9616, "tatuusfa1", race_path, preset_name="BlankLayout"
            )
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("CONFIG_TRACK=", text)
            # Should be blank (no value after =) or empty
            import re
            m = re.search(r"CONFIG_TRACK=(.*)", text)
            self.assertTrue(m, "CONFIG_TRACK line missing")
            self.assertEqual(m.group(1).strip(), "", "CONFIG_TRACK should be blank when missing from server_cfg")

    def test_sync_creates_race_ini_when_missing(self):
        """Sync creates race.ini from scratch when file does not exist."""
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            self.assertFalse(race_path.exists())
            sync_race_ini_from_server_cfg(
                SAMPLE_SERVER_CFG,
                join_ip="192.168.1.218",
                join_port=9616,
                selected_car="tatuusfa1",
                race_ini_path=race_path,
                preset_name="JR F1",
            )
            self.assertTrue(race_path.exists())
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("[REMOTE]", text)
            self.assertIn("[RACE]", text)
            self.assertIn("SERVER_IP=192.168.1.218", text)
            self.assertIn("TRACK=ks_red_bull_ring", text)
            self.assertIn("CONFIG_TRACK=layout_national", text)
            self.assertIn("REQUESTED_CAR=tatuusfa1", text)

    def test_sync_global_password_overrides_preset(self):
        """When global_password is provided, [REMOTE].PASSWORD uses it; preset PASSWORD is ignored."""
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text("[REMOTE]\nPASSWORD=StalePass\n", encoding="utf-8")
            sync_race_ini_from_server_cfg(
                SAMPLE_SERVER_CFG,  # preset has PASSWORD=FastestLap
                "192.168.1.218",
                9616,
                "tatuusfa1",
                race_path,
                preset_name="JR F1",
                global_password="VenueSecret",
            )
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("PASSWORD=VenueSecret", text)
            self.assertNotIn("FastestLap", text)
            self.assertNotIn("StalePass", text)

    def test_sync_global_password_empty_clears_stale(self):
        """global_password='' clears [REMOTE].PASSWORD (never leave stale)."""
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text("[REMOTE]\nPASSWORD=StalePass\n", encoding="utf-8")
            sync_race_ini_from_server_cfg(
                SAMPLE_SERVER_CFG,
                "192.168.1.218",
                9616,
                "tatuusfa1",
                race_path,
                preset_name="JR F1",
                global_password="",
            )
            text = race_path.read_text(encoding="utf-8")
            self.assertIn("PASSWORD=", text)
            self.assertNotIn("StalePass", text)

    def test_sync_patch_preserves_unknown_sections_and_keys(self):
        """PATCH preserves HEADER, OPTIONS, GUID, NAME, __CM_*, AI_LEVEL, etc."""
        race_ini_with_cm = """
[HEADER]
VERSION=1

[OPTIONS]
USE_MPH=0
SOME_KEY=1

[REMOTE]
ACTIVE=1
SERVER_IP=old
GUID=76561199806773536
NAME=DriverOne
__CM_EXTENDED=1
TEAM=MyTeam

[RACE]
TRACK=old_track
AI_LEVEL=100
PENALTIES=1
"""
        with tempfile.TemporaryDirectory() as d:
            race_path = Path(d) / "race.ini"
            race_path.write_text(race_ini_with_cm.strip(), encoding="utf-8")
            sync_race_ini_from_server_cfg(
                SAMPLE_SERVER_CFG,
                "192.168.1.218",
                9616,
                "tatuusfa1",
                race_path,
                preset_name="JR F1",
            )
            text = race_path.read_text(encoding="utf-8")
            # Controlled keys updated
            self.assertIn("SERVER_IP=192.168.1.218", text)
            self.assertIn("TRACK=ks_red_bull_ring", text)
            self.assertIn("REQUESTED_CAR=tatuusfa1", text)
            # Preserved sections and keys
            self.assertIn("[HEADER]", text)
            self.assertIn("VERSION=1", text)
            self.assertIn("[OPTIONS]", text)
            self.assertIn("USE_MPH=0", text)
            self.assertIn("SOME_KEY=1", text)
            self.assertIn("GUID=76561199806773536", text)
            self.assertIn("NAME=DriverOne", text)
            self.assertIn("__CM_EXTENDED=1", text)
            self.assertIn("TEAM=MyTeam", text)
            self.assertIn("AI_LEVEL=100", text)
            self.assertIn("PENALTIES=1", text)

    def test_patch_race_ini_returns_text(self):
        """patch_race_ini_for_online_join returns patched text without writing."""
        existing = "[REMOTE]\nACTIVE=0\nSERVER_IP=0.0.0.0\n[RACE]\nTRACK=old\n"
        patched = patch_race_ini_for_online_join(
            existing,
            SAMPLE_SERVER_CFG,
            "192.168.1.1",
            9616,
            "tatuusfa1",
            "GlobalPass",
        )
        self.assertIn("SERVER_IP=192.168.1.1", patched)
        self.assertIn("TRACK=ks_red_bull_ring", patched)
        self.assertIn("PASSWORD=GlobalPass", patched)
        self.assertIn("[REMOTE]", patched)
        self.assertIn("[RACE]", patched)


if __name__ == "__main__":
    unittest.main()
