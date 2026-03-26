"""
Tests for .cmpreset -> assists.ini conversion.
Source of truth: preset values must map to correct INI keys and values (no inversion, 0 vs 2 preserved).
All 6 presets (Automatic, Drifting, H-Pattern No Assist, H-Pattern, Sequential No Assist, Sequential)
must produce assists.ini that matches the preset exactly.
"""

import unittest

from agent.utils.cmpreset import (
    ASSISTS_INI_KEY_ORDER,
    CMPRESET_TO_ASSISTS,
    cmpreset_to_assists_ini,
    parse_assists_ini,
    validate_assists_ini_content,
    verify_assists_ini_after_write,
)

# Source-of-truth presets (exact values from user)
AUTOMATIC = {
    "IdealLine": True,
    "AutoBlip": True,
    "StabilityControl": 100.0,
    "AutoBrake": False,
    "AutoShifter": True,
    "SlipSteam": 1.0,
    "AutoClutch": True,
    "Abs": 2,
    "TractionControl": 2,
    "VisualDamage": False,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": True,
}

DRIFTING = {
    "IdealLine": False,
    "AutoBlip": True,
    "StabilityControl": 0.0,
    "AutoBrake": False,
    "AutoShifter": False,
    "SlipSteam": 1.0,
    "AutoClutch": False,
    "Abs": 0,
    "TractionControl": 0,
    "VisualDamage": False,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": False,
}

H_PATTERN_NO_ASSIST = {
    "IdealLine": False,
    "AutoBlip": False,
    "StabilityControl": 0.0,
    "AutoBrake": False,
    "AutoShifter": False,
    "SlipSteam": 1.0,
    "AutoClutch": False,
    "Abs": 0,
    "TractionControl": 0,
    "VisualDamage": False,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": False,
}

H_PATTERN = {
    "IdealLine": True,
    "AutoBlip": False,
    "StabilityControl": 100.0,
    "AutoBrake": False,
    "AutoShifter": False,
    "SlipSteam": 1.0,
    "AutoClutch": False,
    "Abs": 2,
    "TractionControl": 2,
    "VisualDamage": False,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": True,
}

SEQUENTIAL_NO_ASSIST = {
    "IdealLine": False,
    "AutoBlip": False,
    "StabilityControl": 0.0,
    "AutoBrake": False,
    "AutoShifter": False,
    "SlipSteam": 1.0,
    "AutoClutch": True,
    "Abs": 0,
    "TractionControl": 0,
    "VisualDamage": True,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": False,
}

SEQUENTIAL = {
    "IdealLine": True,
    "AutoBlip": False,
    "StabilityControl": 100.0,
    "AutoBrake": False,
    "AutoShifter": False,
    "SlipSteam": 1.0,
    "AutoClutch": True,
    "Abs": 2,
    "TractionControl": 2,
    "VisualDamage": True,
    "Damage": 0.0,
    "TyreWear": 0.0,
    "FuelConsumption": 0.0,
    "TyreBlankets": True,
}

ALL_PRESETS = [
    ("Automatic", AUTOMATIC),
    ("Drifting", DRIFTING),
    ("H-Pattern No Assist", H_PATTERN_NO_ASSIST),
    ("H-Pattern", H_PATTERN),
    ("Sequential No Assist", SEQUENTIAL_NO_ASSIST),
    ("Sequential", SEQUENTIAL),
]


class TestCmpresetToAssistsIni(unittest.TestCase):
    def test_each_preset_generates_valid_ini_and_roundtrips(self):
        for name, preset in ALL_PRESETS:
            with self.subTest(preset=name):
                content = cmpreset_to_assists_ini(preset)
                self.assertIn("[ASSISTS]", content)
                ok, errors = validate_assists_ini_content(content, preset)
                self.assertTrue(ok, f"preset={name} validation errors: {errors}")

    def test_automatic_ini_values(self):
        content = cmpreset_to_assists_ini(AUTOMATIC)
        parsed = parse_assists_ini(content)
        self.assertEqual(parsed.get("IDEAL_LINE"), "1")
        self.assertEqual(parsed.get("AUTO_BLIP"), "1")
        self.assertEqual(parsed.get("STABILITY_CONTROL"), "100.0")
        self.assertEqual(parsed.get("AUTO_SHIFTER"), "1")
        self.assertEqual(parsed.get("ABS"), "2")
        self.assertEqual(parsed.get("TRACTION_CONTROL"), "2")
        self.assertEqual(parsed.get("TYRE_BLANKETS"), "1")

    def test_drifting_ini_values(self):
        content = cmpreset_to_assists_ini(DRIFTING)
        parsed = parse_assists_ini(content)
        self.assertEqual(parsed.get("IDEAL_LINE"), "0")
        self.assertEqual(parsed.get("STABILITY_CONTROL"), "0.0")
        self.assertEqual(parsed.get("ABS"), "0")
        self.assertEqual(parsed.get("TRACTION_CONTROL"), "0")
        self.assertEqual(parsed.get("AUTO_CLUTCH"), "0")
        self.assertEqual(parsed.get("TYRE_BLANKETS"), "0")

    def test_h_pattern_vs_h_pattern_no_assist_differ(self):
        c1 = cmpreset_to_assists_ini(H_PATTERN)
        c2 = cmpreset_to_assists_ini(H_PATTERN_NO_ASSIST)
        self.assertNotEqual(c1, c2)
        p1 = parse_assists_ini(c1)
        p2 = parse_assists_ini(c2)
        self.assertEqual(p1.get("IDEAL_LINE"), "1")
        self.assertEqual(p2.get("IDEAL_LINE"), "0")
        self.assertEqual(p1.get("ABS"), "2")
        self.assertEqual(p2.get("ABS"), "0")
        self.assertEqual(p1.get("STABILITY_CONTROL"), "100.0")
        self.assertEqual(p2.get("STABILITY_CONTROL"), "0.0")

    def test_sequential_vs_sequential_no_assist_differ(self):
        c1 = cmpreset_to_assists_ini(SEQUENTIAL)
        c2 = cmpreset_to_assists_ini(SEQUENTIAL_NO_ASSIST)
        self.assertNotEqual(c1, c2)
        p1 = parse_assists_ini(c1)
        p2 = parse_assists_ini(c2)
        self.assertEqual(p1.get("VISUALDAMAGE"), "1")
        self.assertEqual(p2.get("VISUALDAMAGE"), "1")  # both True in user spec
        self.assertEqual(p1.get("AUTO_CLUTCH"), "1")
        self.assertEqual(p2.get("AUTO_CLUTCH"), "1")  # both True
        self.assertEqual(p1.get("ABS"), "2")
        self.assertEqual(p2.get("ABS"), "0")
        self.assertEqual(p1.get("IDEAL_LINE"), "1")
        self.assertEqual(p2.get("IDEAL_LINE"), "0")

    def test_nested_assists_extraction(self):
        wrapped = {"assists": H_PATTERN}
        content = cmpreset_to_assists_ini(wrapped)
        ok, errors = validate_assists_ini_content(content, H_PATTERN)
        self.assertTrue(ok, errors)

    def test_slipstream_ini_key(self):
        content = cmpreset_to_assists_ini(H_PATTERN)
        self.assertIn("SLIPSTREAM=", content)
        parsed = parse_assists_ini(content)
        self.assertEqual(parsed.get("SLIPSTREAM"), "1.0")

    def test_fuel_consumption_maps_to_fuel_rate(self):
        content = cmpreset_to_assists_ini(AUTOMATIC)
        self.assertIn("FUEL_RATE=", content)
        parsed = parse_assists_ini(content)
        self.assertEqual(parsed.get("FUEL_RATE"), "0.0")

    def test_tyre_wear_zero_stays_zero_not_one(self):
        """TyreWear=0.0 in preset must become TYRE_WEAR=0.0 in INI, not 1."""
        for name, preset in ALL_PRESETS:
            with self.subTest(preset=name):
                self.assertEqual(preset.get("TyreWear"), 0.0, f"preset {name} has TyreWear=0.0")
                content = cmpreset_to_assists_ini(preset)
                parsed = parse_assists_ini(content)
                self.assertEqual(parsed.get("TYRE_WEAR"), "0.0", f"preset={name} TYRE_WEAR must be 0.0")

    def test_visual_damage_true_becomes_one(self):
        """VisualDamage=true in preset must become VISUALDAMAGE=1, not 0."""
        content = cmpreset_to_assists_ini(SEQUENTIAL)
        parsed = parse_assists_ini(content)
        self.assertEqual(parsed.get("VISUALDAMAGE"), "1")
        content2 = cmpreset_to_assists_ini(SEQUENTIAL_NO_ASSIST)
        parsed2 = parse_assists_ini(content2)
        self.assertEqual(parsed2.get("VISUALDAMAGE"), "1")

    def test_exact_ini_values_for_all_six_presets(self):
        """For each of the 6 presets, generated INI must match expected values exactly (no defaults, correct types)."""
        # Expected INI value (as string) for each preset name -> ini_key
        expected = {
            "Automatic": {
                "IDEAL_LINE": "1",
                "AUTO_BLIP": "1",
                "STABILITY_CONTROL": "100.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "1",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "1",
                "ABS": "2",
                "TRACTION_CONTROL": "2",
                "VISUALDAMAGE": "0",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "1",
            },
            "Drifting": {
                "IDEAL_LINE": "0",
                "AUTO_BLIP": "1",
                "STABILITY_CONTROL": "0.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "0",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "0",
                "ABS": "0",
                "TRACTION_CONTROL": "0",
                "VISUALDAMAGE": "0",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "0",
            },
            "H-Pattern No Assist": {
                "IDEAL_LINE": "0",
                "AUTO_BLIP": "0",
                "STABILITY_CONTROL": "0.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "0",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "0",
                "ABS": "0",
                "TRACTION_CONTROL": "0",
                "VISUALDAMAGE": "0",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "0",
            },
            "H-Pattern": {
                "IDEAL_LINE": "1",
                "AUTO_BLIP": "0",
                "STABILITY_CONTROL": "100.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "0",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "0",
                "ABS": "2",
                "TRACTION_CONTROL": "2",
                "VISUALDAMAGE": "0",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "1",
            },
            "Sequential No Assist": {
                "IDEAL_LINE": "0",
                "AUTO_BLIP": "0",
                "STABILITY_CONTROL": "0.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "0",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "1",
                "ABS": "0",
                "TRACTION_CONTROL": "0",
                "VISUALDAMAGE": "1",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "0",
            },
            "Sequential": {
                "IDEAL_LINE": "1",
                "AUTO_BLIP": "0",
                "STABILITY_CONTROL": "100.0",
                "AUTO_BRAKE": "0",
                "AUTO_SHIFTER": "0",
                "SLIPSTREAM": "1.0",
                "AUTO_CLUTCH": "1",
                "ABS": "2",
                "TRACTION_CONTROL": "2",
                "VISUALDAMAGE": "1",
                "DAMAGE": "0.0",
                "TYRE_WEAR": "0.0",
                "FUEL_RATE": "0.0",
                "TYRE_BLANKETS": "1",
            },
        }
        for name, preset in ALL_PRESETS:
            with self.subTest(preset=name):
                content = cmpreset_to_assists_ini(preset)
                parsed = parse_assists_ini(content)
                exp = expected[name]
                for ini_key in ASSISTS_INI_KEY_ORDER:
                    self.assertIn(ini_key, exp, f"preset={name} expected dict missing {ini_key}")
                    self.assertEqual(
                        parsed.get(ini_key),
                        exp[ini_key],
                        f"preset={name} {ini_key}: expected {exp[ini_key]!r} got {parsed.get(ini_key)!r}",
                    )
                self.assertEqual(len(parsed), len(exp), f"preset={name} unexpected extra keys in INI")

    def test_verify_assists_ini_after_write(self):
        """verify_assists_ini_after_write passes when content matches preset and fails when it does not."""
        import tempfile
        from pathlib import Path
        content = cmpreset_to_assists_ini(H_PATTERN)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = Path(f.name)
        try:
            ok, errors = verify_assists_ini_after_write(path, H_PATTERN)
            self.assertTrue(ok, errors)
            ok2, errors2 = verify_assists_ini_after_write(path, DRIFTING)
            self.assertFalse(ok2)
            self.assertTrue(len(errors2) > 0)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
