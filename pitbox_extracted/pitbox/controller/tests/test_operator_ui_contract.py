"""Lightweight contract checks: main UI includes operator LAN workflow hooks (no JS runtime)."""
from __future__ import annotations

import unittest
from pathlib import Path


class TestOperatorUiContract(unittest.TestCase):
    def test_app_js_includes_operator_helpers(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("redirectToEmployeeLogin", text)
        self.assertIn("ensureOperatorOrRedirect", text)
        self.assertIn("operatorControlBlocked", text)
        self.assertIn("Operator login required to control sims.", text)
        self.assertIn("/employee/login?next=", text)
        self.assertIn("pitboxFetchBare", text)
        self.assertIn("data.poll_interval_sec", text)

    def test_employee_login_posts_next(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "static" / "employee-login.html").read_text(encoding="utf-8")
        self.assertIn("next:", text)
        self.assertIn("body.redirect", text)
        self.assertIn("credentials: 'same-origin'", text)


if __name__ == "__main__":
    unittest.main()
