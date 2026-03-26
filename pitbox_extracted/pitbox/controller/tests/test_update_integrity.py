"""Update integrity: SHA-256 parsing, file verify, controller apply gate."""
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from controller.updater import apply_controller_update, run_unified_installer_update
from pitbox_common.update_integrity import (
    parse_release_sha256_annotations,
    verify_file_sha256,
)


class TestParseReleaseSha256Annotations(unittest.TestCase):
    def test_parses_comment(self):
        body = "Notes\n<!-- pitbox_sha256:MyZip.zip:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789 -->\n"
        d = parse_release_sha256_annotations(body)
        self.assertEqual(d["MyZip.zip"], "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789")

    def test_empty_body(self):
        self.assertEqual(parse_release_sha256_annotations(""), {})

    def test_invalid_hex_length_ignored(self):
        body = "<!-- pitbox_sha256:x.zip:abcd -->"
        self.assertEqual(parse_release_sha256_annotations(body), {})


class TestVerifyFileSha256(unittest.TestCase):
    def test_matching_checksum(self):
        data = b"hello pitbox"
        import hashlib

        hx = hashlib.sha256(data).hexdigest()
        with NamedTemporaryFile(delete=False) as f:
            f.write(data)
            p = Path(f.name)
        try:
            ok, err = verify_file_sha256(p, hx)
            self.assertTrue(ok)
            self.assertEqual(err, "")
        finally:
            p.unlink(missing_ok=True)

    def test_mismatch(self):
        data = b"a"
        with NamedTemporaryFile(delete=False) as f:
            f.write(data)
            p = Path(f.name)
        try:
            ok, err = verify_file_sha256(p, "0" * 64)
            self.assertFalse(ok)
            self.assertIn("does not match", err)
        finally:
            p.unlink(missing_ok=True)

    def test_invalid_expected_length(self):
        with NamedTemporaryFile(delete=False) as f:
            f.write(b"x")
            p = Path(f.name)
        try:
            ok, err = verify_file_sha256(p, "not64")
            self.assertFalse(ok)
            self.assertIn("64", err)
        finally:
            p.unlink(missing_ok=True)


class TestStandaloneZipUpdaterVerifyHelper(unittest.TestCase):
    """Ensure bundled pitbox_updater.py refuses mismatched SHA (no install path executed)."""

    def test_verify_mismatch_without_running_main(self):
        import importlib.util

        root = Path(__file__).resolve().parents[2]
        mod_path = root / "updater" / "pitbox_updater.py"
        spec = importlib.util.spec_from_file_location("pitbox_updater_standalone", mod_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        with NamedTemporaryFile(delete=False) as f:
            f.write(b"payload-bytes")
            p = Path(f.name)
        try:
            ok, msg = mod._verify_download_sha256(p, "0" * 64)
            self.assertFalse(ok)
            self.assertIn("does not match", msg.lower())
        finally:
            p.unlink(missing_ok=True)


class TestRunUnifiedInstallerRequiresSha(unittest.TestCase):
    @patch("controller.updater.get_update_status")
    def test_rejects_installer_without_sha256_metadata(self, mock_status):
        mock_status.return_value = {
            "error": None,
            "update_available": True,
            "unified_installer": {
                "name": "PitBoxInstaller.exe",
                "url": "https://example.com/i.exe",
                "api_url": "https://api.github.com/assets/2",
            },
        }
        ok, msg = run_unified_installer_update()
        self.assertFalse(ok)
        self.assertIn("SHA-256", msg)


class TestApplyControllerUpdateRequiresSha(unittest.TestCase):
    @patch("controller.updater.get_update_status")
    def test_rejects_zip_without_sha256_metadata(self, mock_status):
        mock_status.return_value = {
            "error": None,
            "controller_zip": {
                "name": "PitBox.zip",
                "url": "https://example.com/z.zip",
                "api_url": "https://api.github.com/assets/1",
            },
        }
        ok, msg = apply_controller_update()
        self.assertFalse(ok)
        self.assertIn("SHA-256", msg)
        self.assertIn("pitbox_sha256", msg)


if __name__ == "__main__":
    unittest.main()
