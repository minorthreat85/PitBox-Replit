"""Tests for pitbox_common.safe_inputs preset name validation."""
import unittest

from pitbox_common.safe_inputs import (
    MAX_PRESET_BASE_NAME_LENGTH,
    validate_ac_server_preset_folder_name,
    validate_steering_shifting_preset_basename,
)


class TestSteeringShiftingPresetNames(unittest.TestCase):
    def test_accepts_simple_names(self):
        self.assertEqual(validate_steering_shifting_preset_basename("1 Race"), "1 Race")
        self.assertEqual(validate_steering_shifting_preset_basename("H-Pattern"), "H-Pattern")

    def test_accepts_space_dash_underscore_dot(self):
        self.assertEqual(validate_steering_shifting_preset_basename("My_Preset-1"), "My_Preset-1")
        self.assertEqual(validate_steering_shifting_preset_basename("Drift 2.0"), "Drift 2.0")
        self.assertEqual(validate_steering_shifting_preset_basename("a_b-c.d"), "a_b-c.d")

    def test_rejects_traversal(self):
        for bad in ("..", "../foo", "..\\foo", "foo..bar", "..x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_steering_shifting_preset_basename(bad)

    def test_rejects_dot_dot_substring(self):
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("a/../b")

    def test_rejects_slashes(self):
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("a/b")
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("x\\y")

    def test_rejects_reserved_chars_individually(self):
        for ch in (":", "*", "?", '"', "<", ">", "|"):
            name = f"bad{ch}name"
            with self.subTest(ch=ch):
                with self.assertRaises(ValueError):
                    validate_steering_shifting_preset_basename(name)

    def test_rejects_empty_and_whitespace(self):
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("")
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("   ")

    def test_rejects_overlength(self):
        base = "a" * (MAX_PRESET_BASE_NAME_LENGTH + 1)
        with self.assertRaises(ValueError) as ctx:
            validate_steering_shifting_preset_basename(base)
        self.assertIn("long", str(ctx.exception).lower())

    def test_rejects_colon_in_steering_name(self):
        with self.assertRaises(ValueError):
            validate_steering_shifting_preset_basename("bad:name")

    def test_server_folder_rejects_colon(self):
        with self.assertRaises(ValueError):
            validate_ac_server_preset_folder_name("192.168.1.1:9600")


if __name__ == "__main__":
    unittest.main()
