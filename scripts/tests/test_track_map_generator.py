"""Unit tests for scripts.track_map_generator.

These tests build synthetic map.png images on the fly so they don't depend
on Assetto Corsa being installed. They cover:

  * the happy path (clean ring → closed polyline)
  * pit-lane branch pruning
  * empty / unusable images raising TrackMapGenerationError
  * the slugify rule matching the JS frontend exactly

Skipped automatically when numpy / scikit-image / Pillow aren't installed
(those live in requirements-tools.txt, not requirements.txt).
"""
from __future__ import annotations

import io
import math
import unittest
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw
    from skimage.morphology import skeletonize  # noqa: F401  - import smoke test
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "build-time deps not installed (see requirements-tools.txt)")
class TrackMapGeneratorTests(unittest.TestCase):

    def _write_ring_png(self, tmp: Path, size: int = 256, r_outer: int = 110, r_inner: int = 70) -> Path:
        """Render a clean annulus (donut) on transparent background as a stand-in
        for an AC track-surface map.png. Skeleton should be a single closed loop.
        """
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx = cy = size // 2
        d.ellipse((cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer), fill=(255, 255, 255, 255))
        d.ellipse((cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner), fill=(0, 0, 0, 0))
        out = tmp / "ring.png"
        img.save(out)
        return out

    def test_happy_path_ring_produces_closed_path(self):
        from scripts.track_map_generator import generate_from_png, GeneratorOptions
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            png = self._write_ring_png(Path(td))
            res = generate_from_png(png, GeneratorOptions())
            self.assertEqual(res.viewBox, "0 0 256 256")
            self.assertTrue(res.svg_path.startswith("M "))
            self.assertTrue(res.svg_path.endswith(" Z"))
            # A simplified circle should land in a sane vertex range.
            self.assertGreaterEqual(res.vertex_count, 6)
            self.assertLess(res.vertex_count, 200)

    def test_pit_lane_stub_is_pruned(self):
        """Build a skeleton-like input directly: a closed loop with a short
        dead-end branch, and verify the pruner removes the branch only.
        """
        from scripts.track_map_generator import _prune_branches

        # 30x30 grid. Loop = 16x16 square outline. Branch = 4-pixel spur off
        # the right side of the loop.
        skel = np.zeros((30, 30), dtype=bool)
        # Square outline at rows/cols 5..20
        for i in range(5, 21):
            skel[5, i] = True   # top edge
            skel[20, i] = True  # bottom edge
            skel[i, 5] = True   # left edge
            skel[i, 20] = True  # right edge
        # 4-px spur sticking right out of (12, 20) → (12, 24)
        for j in range(21, 25):
            skel[12, j] = True

        total_before = int(skel.sum())
        pruned, removed = _prune_branches(skel, branch_min_frac=0.05)
        # The pruner walks dead-ends back toward the loop and stops just before
        # an 8-connectivity "junction-like" pixel. With our geometry the spur's
        # first pixel is diagonally adjacent to two loop pixels and is therefore
        # treated as part of the loop. We expect the bulk of the spur (>= 3 px)
        # to be removed and the entire loop to remain intact.
        self.assertGreaterEqual(removed, 3, "expected most of the spur to be pruned")
        # Every loop edge pixel must survive: pick a few canonical loop points.
        for y, x in [(5, 5), (5, 20), (20, 5), (20, 20), (12, 5), (12, 20)]:
            self.assertTrue(pruned[y, x], f"loop pixel ({y},{x}) was incorrectly removed")
        # Net pixel count: total - removed.
        self.assertEqual(int(pruned.sum()), total_before - removed)

    def test_empty_image_raises(self):
        from scripts.track_map_generator import (
            generate_from_png, GeneratorOptions, TrackMapGenerationError,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            blank = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            p = Path(td) / "blank.png"
            blank.save(p)
            with self.assertRaises(TrackMapGenerationError):
                generate_from_png(p, GeneratorOptions())

    def test_skeleton_with_residual_junction_raises(self):
        """If pruning leaves a junction (e.g. a long pit lane the user did
        not crank --branch-min-frac high enough to remove), the walker must
        REFUSE to emit a forced-closed path and instead raise.
        """
        from scripts.track_map_generator import (
            _walk_cycle, TrackMapGenerationError,
        )

        # Closed loop with a long spur that pruning would leave intact.
        skel = np.zeros((30, 30), dtype=bool)
        for i in range(5, 21):
            skel[5, i] = skel[20, i] = skel[i, 5] = skel[i, 20] = True
        for j in range(21, 28):  # 7-pixel spur — survives default pruning
            skel[12, j] = True
        with self.assertRaises(TrackMapGenerationError):
            _walk_cycle(skel)

    def test_disconnected_components_raise(self):
        """Two unrelated rings must be rejected — we can't trace both with
        a single SVG path.
        """
        from scripts.track_map_generator import (
            _walk_cycle, TrackMapGenerationError,
        )
        skel = np.zeros((40, 40), dtype=bool)
        # Ring A: tiny square at (5..10, 5..10)
        for i in range(5, 11):
            skel[5, i] = skel[10, i] = skel[i, 5] = skel[i, 10] = True
        # Ring B: tiny square at (25..30, 25..30) — disconnected
        for i in range(25, 31):
            skel[25, i] = skel[30, i] = skel[i, 25] = skel[i, 30] = True
        with self.assertRaises(TrackMapGenerationError):
            _walk_cycle(skel)

    def test_max_vertices_cap_escalates_epsilon(self):
        from scripts.track_map_generator import generate_from_png, GeneratorOptions
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            png = self._write_ring_png(Path(td), size=512, r_outer=240, r_inner=200)
            res = generate_from_png(png, GeneratorOptions(simplify_epsilon=0.1, max_vertices=40))
            self.assertLessEqual(res.vertex_count, 40)


@unittest.skipUnless(_HAS_DEPS, "build-time deps not installed")
class LayoutDiscoveryTests(unittest.TestCase):
    """`_iter_layouts_for_track` must include the shared `ui/map.png` as a
    bare-track key even when per-layout maps exist — the frontend relies on
    that fallback when `<track>__<layout>.json` is missing.
    """

    def test_emits_bare_track_alongside_layouts(self):
        from scripts.generate_track_maps import _iter_layouts_for_track
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            track = Path(td) / "ks_demo"
            (track / "ui" / "gp").mkdir(parents=True)
            (track / "ui" / "club").mkdir(parents=True)
            # Three map.png files — two per-layout, one shared base.
            for p in [
                track / "ui" / "gp" / "map.png",
                track / "ui" / "club" / "map.png",
                track / "ui" / "map.png",
            ]:
                Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(p)
            results = _iter_layouts_for_track(track)
            # Use posix paths for stable cross-platform comparison.
            got = sorted(
                ((layout or ""), png.relative_to(track).as_posix())
                for layout, png in results
            )
            self.assertEqual(got, [
                ("", "ui/map.png"),
                ("club", "ui/club/map.png"),
                ("gp", "ui/gp/map.png"),
            ])

    def test_root_map_only_used_as_last_resort(self):
        from scripts.generate_track_maps import _iter_layouts_for_track
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            track = Path(td) / "legacy_track"
            track.mkdir()
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(track / "map.png")
            results = _iter_layouts_for_track(track)
            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0][0])


class SlugifyParityTests(unittest.TestCase):
    """The Python slugify must produce the SAME key the JS frontend builds —
    otherwise the generated JSON will never be loaded.
    """

    def test_matches_js_rule(self):
        from scripts.generate_track_maps import slugify, map_key

        self.assertEqual(slugify("Spa-Francorchamps"), "spa_francorchamps")
        self.assertEqual(slugify("KS_Nürburgring  GP"), "ks_n_rburgring_gp")
        self.assertEqual(slugify("---weird---"), "weird")
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify(None), "")  # type: ignore[arg-type]

        self.assertEqual(map_key("ks_nordschleife", "endurance"),
                         "ks_nordschleife__endurance")
        self.assertEqual(map_key("jr_mosport_2021", None), "jr_mosport_2021")
        self.assertEqual(map_key("jr_mosport_2021", ""), "jr_mosport_2021")


if __name__ == "__main__":
    unittest.main()
