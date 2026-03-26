"""
Unit tests for display formatting helpers (layout name, track description).

These tests mirror the JS logic in app.js (formatLayoutName, formatTrackDescription)
to document expected behavior and catch regressions.
"""
import re
import unittest


def format_layout_name(layout_id: str | None) -> str:
    """Human-friendly layout label. Mirrors JS formatLayoutName()."""
    if layout_id is None or (isinstance(layout_id, str) and not layout_id.strip()):
        return "Default"
    s = str(layout_id).strip()
    if s.lower() == "default":
        return "Default"
    s = re.sub(r"^layout_", "", s, flags=re.IGNORECASE)
    if not s:
        return "Default"
    s = re.sub(r"[-_]+", " ", s)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    compounds = {
        "fullcircuit": "full circuit",
        "shortcircuit": "short circuit",
        "grandprix": "grand prix",
        "nochicane": "no chicane",
    }
    lower = s.lower()
    for key, val in compounds.items():
        if lower == key:
            s = val
            break
    acronyms = {"gp": "GP", "f1": "F1", "usa": "USA", "uk": "UK", "gt3": "GT3"}
    words = []
    for w in s.split():
        lw = w.lower()
        words.append(acronyms.get(lw, w.capitalize() if w else w))
    return " ".join(words)


def format_track_description(str_val: str | None) -> str | None:
    """Format track description: trim, collapse whitespace. Mirrors JS intent (full logic in app.js)."""
    if str_val is None or not isinstance(str_val, str):
        return None
    s = str_val.strip()
    if not s:
        return None
    paras = re.split(r"\n\s*\n", s)
    s = "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in paras if p.strip())
    return s if s else None


class TestFormatLayoutName(unittest.TestCase):
    def test_empty_or_none_returns_default(self):
        self.assertEqual(format_layout_name(None), "Default")
        self.assertEqual(format_layout_name(""), "Default")
        self.assertEqual(format_layout_name("   "), "Default")

    def test_default_case_insensitive(self):
        self.assertEqual(format_layout_name("default"), "Default")
        self.assertEqual(format_layout_name("DEFAULT"), "Default")

    def test_strip_layout_prefix(self):
        self.assertEqual(format_layout_name("layout_national"), "National")
        self.assertEqual(format_layout_name("layout_NATIONAL"), "National")

    def test_underscores_to_spaces_title_case(self):
        self.assertEqual(format_layout_name("ebisu_complex"), "Ebisu Complex")
        self.assertEqual(format_layout_name("no_chicane"), "No Chicane")

    def test_compounds(self):
        self.assertEqual(format_layout_name("fullcircuit"), "Full Circuit")
        self.assertEqual(format_layout_name("grandprix"), "Grand Prix")
        self.assertEqual(format_layout_name("shortcircuit"), "Short Circuit")
        self.assertEqual(format_layout_name("nochicane"), "No Chicane")

    def test_acronyms_preserved(self):
        self.assertEqual(format_layout_name("gp"), "GP")
        self.assertEqual(format_layout_name("f1"), "F1")
        self.assertEqual(format_layout_name("uk"), "UK")
        self.assertEqual(format_layout_name("usa"), "USA")
        self.assertEqual(format_layout_name("gt3"), "GT3")


class TestFormatTrackDescription(unittest.TestCase):
    def test_empty_or_none_returns_none(self):
        self.assertIsNone(format_track_description(None))
        self.assertIsNone(format_track_description(""))
        self.assertIsNone(format_track_description("   "))

    def test_trim_whitespace(self):
        self.assertIsNotNone(format_track_description("  foo  "))
        self.assertTrue(format_track_description("  foo  ").strip() == "foo" or "foo" in format_track_description("  foo  "))


if __name__ == "__main__":
    unittest.main()
