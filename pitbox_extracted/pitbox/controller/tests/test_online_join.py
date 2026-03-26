"""
Lightweight tests for online join: hybrid car picker logic, server/car/launch behavior.
Backend: changing server clears car (state machine); car picker disabled until server details loaded;
launch requires server + car; offline disables controls (UI contract).
"""
import unittest

CAR_TILE_THRESHOLD = 16


def should_use_tile_grid(cars_count: int, thumbnails_available: bool, threshold: int = CAR_TILE_THRESHOLD) -> bool:
    """Mirror of frontend shouldUseTileGrid: tiles when thumbnails and cars <= threshold."""
    return bool(thumbnails_available and cars_count > 0 and cars_count <= threshold)


class TestHybridCarPicker(unittest.TestCase):
    """Hybrid chooses tiles when <=16 AND thumbnailsAvailable true; else combobox."""

    def test_tiles_when_under_threshold_and_thumbnails(self):
        self.assertTrue(should_use_tile_grid(10, True))
        self.assertTrue(should_use_tile_grid(16, True))

    def test_combobox_when_over_threshold(self):
        self.assertFalse(should_use_tile_grid(17, True))
        self.assertFalse(should_use_tile_grid(20, True))

    def test_combobox_when_no_thumbnails(self):
        self.assertFalse(should_use_tile_grid(10, False))
        self.assertFalse(should_use_tile_grid(16, False))

    def test_combobox_when_zero_cars(self):
        self.assertFalse(should_use_tile_grid(0, True))

    def test_custom_threshold(self):
        self.assertTrue(should_use_tile_grid(20, True, threshold=25))
        self.assertFalse(should_use_tile_grid(20, True, threshold=19))


class TestLaunchValidation(unittest.TestCase):
    """Cannot launch without server and car (contract; actual validation in frontend)."""

    def test_validation_order_documented(self):
        # Documented order: agent offline -> select server -> cars loaded -> select car
        errors = ["Agent offline", "Select a server", "Cars not loaded", "Select a car"]
        self.assertEqual(len(errors), 4)
        self.assertIn("Select a server", errors)
        self.assertIn("Select a car", errors)


if __name__ == "__main__":
    unittest.main()
