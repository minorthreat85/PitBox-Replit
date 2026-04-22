"""
PitBox Live Timing — automated track-map generator (pure library).

Converts an Assetto Corsa `map.png` (transparent track-surface raster) into
the JSON shape the existing Live Timing frontend already consumes:

    {
      "viewBox":      "0 0 WIDTH HEIGHT",
      "svg_path":     "M x y L x y ... Z",
      "start_offset": 0.0,
      "direction":    1,
      "scale":        1.0
    }

The frontend places cars along this path with `getPointAtLength()`, so all
that matters geometrically is that the path traces the centerline of the
racetrack as one closed loop, in *pixel* coordinates of the source map.png.

This module is BUILD-TIME ONLY. It is invoked by scripts/generate_track_maps.py
on the developer / main-PC side and produces JSON files that ship as static
assets. It is never imported by the controller or agent runtime, and its
heavy deps (numpy, scikit-image) live in requirements-tools.txt — they are
intentionally NOT in requirements.txt and NOT in any PyInstaller .spec.

Pipeline:
  1. Load map.png → RGBA numpy array.
  2. Build a binary "track surface" mask (alpha > T, or luma > T for opaque PNGs).
  3. Pick the largest connected component (drops tiny detached artifacts).
  4. Skeletonize → one-pixel-wide centerline.
  5. Iteratively prune dead-end branches shorter than `branch_min_frac` of the
     total skeleton length. This removes pit lane, escape roads, and any
     small noise spurs, leaving (in the typical case) a single closed cycle.
  6. Walk the cycle in pixel order, starting from any pixel.
  7. Simplify the polyline with Ramer–Douglas–Peucker (`epsilon` px tolerance).
  8. Emit an SVG path string `M x y L x y ... Z`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError as _e:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "numpy is required for track_map_generator. "
        "Install build-time deps: pip install -r requirements-tools.txt"
    ) from _e

try:
    from PIL import Image
except ImportError as _e:  # pragma: no cover
    raise RuntimeError("Pillow is required for track_map_generator.") from _e

try:
    from skimage.morphology import skeletonize
    from skimage.measure import label
except ImportError as _e:  # pragma: no cover
    raise RuntimeError(
        "scikit-image is required for track_map_generator. "
        "Install build-time deps: pip install -r requirements-tools.txt"
    ) from _e


# ----------------------------- public types ---------------------------------


@dataclass(frozen=True)
class GeneratorOptions:
    """Tunables for the generator. Defaults work for the vast majority of
    AC map.png assets. Override per-track only when needed.
    """
    # Alpha threshold (0..255) for transparent PNGs. Pixels with alpha >= this
    # are considered track surface.
    alpha_threshold: int = 16
    # Luminance threshold for fully-opaque PNGs (rare). Used only when the
    # source has no usable alpha channel.
    luma_threshold: int = 32
    # After skeletonizing, prune any dead-end branch whose length (in skeleton
    # pixels) is below this fraction of the total skeleton length. Pit lanes
    # are typically 5–15% of the main loop and get cleanly removed at 0.05.
    branch_min_frac: float = 0.05
    # Ramer–Douglas–Peucker tolerance in pixels. ~1.5 produces a smooth path
    # with ~100–250 vertices for typical 1024–2048 px AC maps.
    simplify_epsilon: float = 1.5
    # Hard cap on output vertex count after simplification. If exceeded, the
    # epsilon is raised iteratively. Keeps SVG path strings small.
    max_vertices: int = 400
    # Defaults written into the JSON for the frontend (it already supports
    # overriding these via per-track overrides if a human edits the file).
    start_offset: float = 0.0
    direction: int = 1
    scale: float = 1.0


@dataclass
class GenerationResult:
    """Successful generator output, ready to be written to JSON."""
    viewBox: str
    svg_path: str
    start_offset: float
    direction: int
    scale: float
    # Diagnostic-only metadata (also written to JSON so future edits know the
    # file is generated, not hand-traced):
    generated_by: str
    width: int
    height: int
    vertex_count: int
    skeleton_pixels: int
    pruned_pixels: int

    def to_json_dict(self) -> dict:
        return {
            "viewBox": self.viewBox,
            "svg_path": self.svg_path,
            "start_offset": self.start_offset,
            "direction": self.direction,
            "scale": self.scale,
            "generated_by": self.generated_by,
            "width": self.width,
            "height": self.height,
            "vertex_count": self.vertex_count,
            "skeleton_pixels": self.skeleton_pixels,
            "pruned_pixels": self.pruned_pixels,
        }


class TrackMapGenerationError(RuntimeError):
    """Raised when the generator cannot extract a usable centerline from the
    given map.png (missing/empty mask, disconnected geometry, etc.).
    """


# ----------------------------- helpers --------------------------------------


def _load_mask(png_path: Path, opts: GeneratorOptions) -> Tuple[np.ndarray, int, int]:
    """Load a PNG and return (binary_mask HxW bool, width, height).

    Prefers alpha channel when present and not fully opaque; falls back to
    luminance for opaque sources.
    """
    img = Image.open(png_path)
    img = img.convert("RGBA")
    arr = np.asarray(img)  # H x W x 4, uint8
    h, w = arr.shape[:2]
    alpha = arr[..., 3]
    if alpha.max() > 0 and alpha.min() < 255:
        mask = alpha >= opts.alpha_threshold
    else:
        # No usable alpha — treat as luminance mask.
        rgb = arr[..., :3].astype(np.uint16)
        luma = (rgb[..., 0] * 299 + rgb[..., 1] * 587 + rgb[..., 2] * 114) // 1000
        mask = luma >= opts.luma_threshold
    return mask.astype(bool), w, h


def _largest_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the largest 8-connected component of `mask`."""
    lbl = label(mask, connectivity=2)
    if lbl.max() == 0:
        return mask
    sizes = np.bincount(lbl.ravel())
    sizes[0] = 0  # ignore background
    keep = int(sizes.argmax())
    return lbl == keep


def _neighbor_offsets() -> List[Tuple[int, int]]:
    return [(-1, -1), (-1, 0), (-1, 1),
            ( 0, -1),          ( 0, 1),
            ( 1, -1), ( 1, 0), ( 1, 1)]


def _degree_map(skel: np.ndarray) -> np.ndarray:
    """Per-pixel count of 8-connected skeleton neighbors. 0 outside skeleton."""
    s = skel.astype(np.uint8)
    # Sum of 8 shifted copies; subtract self (the center pixel itself is included
    # because we sum over a 3x3 minus center). We avoid scipy by doing it by hand.
    h, w = s.shape
    deg = np.zeros((h, w), dtype=np.int16)
    for dy, dx in _neighbor_offsets():
        y0, y1 = max(0, dy), h + min(0, dy)
        x0, x1 = max(0, dx), w + min(0, dx)
        sy0, sy1 = max(0, -dy), h + min(0, -dy)
        sx0, sx1 = max(0, -dx), w + min(0, -dx)
        deg[y0:y1, x0:x1] += s[sy0:sy1, sx0:sx1]
    deg[~skel] = 0
    return deg


def _prune_branches(skel: np.ndarray, branch_min_frac: float) -> Tuple[np.ndarray, int]:
    """Iteratively remove dead-end branches shorter than `branch_min_frac` of
    the current total skeleton length. Returns (pruned_skeleton, pixels_removed).
    """
    skel = skel.copy()
    total_pruned = 0
    offsets = _neighbor_offsets()
    h, w = skel.shape
    while True:
        total = int(skel.sum())
        if total == 0:
            break
        threshold_len = max(2, int(total * branch_min_frac))
        deg = _degree_map(skel)
        endpoints = np.argwhere((deg == 1) & skel)
        if endpoints.size == 0:
            break  # No more dead ends → done

        removed_this_pass = 0
        # Walk each endpoint inward until we hit a junction (deg>=3) or another
        # endpoint, accumulating the branch's pixel coordinates. If short enough,
        # delete it.
        for ey, ex in endpoints:
            if not skel[ey, ex]:
                continue  # already removed by an earlier branch in this pass
            branch: List[Tuple[int, int]] = [(int(ey), int(ex))]
            prev = (-1, -1)
            cy, cx = int(ey), int(ex)
            while True:
                nxt: Optional[Tuple[int, int]] = None
                for dy, dx in offsets:
                    ny, nx_ = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx_ < w and skel[ny, nx_] and (ny, nx_) != prev:
                        if nxt is None:
                            nxt = (ny, nx_)
                        else:
                            # More than one forward neighbor → we've reached a
                            # junction. Stop BEFORE consuming it.
                            nxt = None
                            break
                if nxt is None:
                    break
                # If the next pixel is itself a junction (deg>=3) we stop just
                # before it so we don't tear the main loop apart.
                ny, nx_ = nxt
                if deg[ny, nx_] >= 3:
                    break
                branch.append((ny, nx_))
                prev = (cy, cx)
                cy, cx = ny, nx_
                # Safety: bail if branch is already longer than threshold; we
                # won't prune it anyway.
                if len(branch) > threshold_len:
                    break
            if len(branch) <= threshold_len:
                for by, bx in branch:
                    skel[by, bx] = False
                removed_this_pass += len(branch)

        if removed_this_pass == 0:
            break
        total_pruned += removed_this_pass
    return skel, total_pruned


def _walk_cycle(skel: np.ndarray) -> List[Tuple[int, int]]:
    """Walk a single closed-cycle skeleton in pixel order.

    REQUIRES (and validates) that the skeleton is a clean single closed cycle:
    every pixel has exactly two 8-connected skeleton neighbors, the graph is
    a single connected component, and the walk ends adjacent to where it
    started. Any violation raises `TrackMapGenerationError` so callers never
    emit a falsely-closed `Z` path.
    """
    coords = np.argwhere(skel)
    if coords.size == 0:
        raise TrackMapGenerationError("Skeleton is empty after pruning.")
    total = int(coords.shape[0])

    # Strict topology check: a single closed cycle has every node at degree 2.
    deg = _degree_map(skel)
    bad = int(((deg != 2) & skel).sum())
    if bad > 0:
        raise TrackMapGenerationError(
            f"Skeleton is not a clean single cycle after pruning: "
            f"{bad}/{total} pixels have degree != 2 (junctions or endpoints remain). "
            f"This typically means a long pit lane / escape road survived branch "
            f"pruning. Consider raising --branch-min-frac for this track."
        )

    visited = np.zeros_like(skel, dtype=bool)
    h, w = skel.shape
    offsets = _neighbor_offsets()

    start_y, start_x = int(coords[0, 0]), int(coords[0, 1])
    order: List[Tuple[int, int]] = [(start_y, start_x)]
    visited[start_y, start_x] = True
    cy, cx = start_y, start_x

    while True:
        nxt: Optional[Tuple[int, int]] = None
        for dy, dx in offsets:
            ny, nx_ = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx_ < w and skel[ny, nx_] and not visited[ny, nx_]:
                nxt = (ny, nx_)
                break
        if nxt is None:
            break
        order.append(nxt)
        visited[nxt[0], nxt[1]] = True
        cy, cx = nxt

    # Strict completeness checks: walk must consume every skeleton pixel AND
    # the last pixel must be 8-adjacent to the start (so the cycle closes).
    if len(order) != total:
        raise TrackMapGenerationError(
            f"Cycle walk only covered {len(order)}/{total} skeleton pixels; "
            f"skeleton is not a single connected component."
        )
    closes = any((cy + dy, cx + dx) == (start_y, start_x) for dy, dx in offsets)
    if not closes:
        raise TrackMapGenerationError(
            "Cycle walk did not return adjacent to start; skeleton is not a closed loop."
        )
    return order


def _rdp_simplify(points: Sequence[Tuple[float, float]], epsilon: float) -> List[Tuple[float, float]]:
    """Iterative Ramer–Douglas–Peucker. Input may be open or closed.

    For a closed loop the caller should NOT include the closing duplicate;
    the SVG `Z` command provides closure.
    """
    if len(points) < 3:
        return list(points)
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[-1] = True
    # Iterative stack-based RDP to avoid recursion depth issues.
    stack: List[Tuple[int, int]] = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        a = pts[i0]
        b = pts[i1]
        ab = b - a
        ab_len2 = float(ab[0] * ab[0] + ab[1] * ab[1])
        if ab_len2 == 0.0:
            # Degenerate segment — fall back to point distances.
            d = np.linalg.norm(pts[i0 + 1:i1] - a, axis=1)
        else:
            ap = pts[i0 + 1:i1] - a
            cross = ap[:, 0] * ab[1] - ap[:, 1] * ab[0]
            d = np.abs(cross) / float(np.sqrt(ab_len2))
        if d.size == 0:
            continue
        k = int(d.argmax())
        if d[k] > epsilon:
            mid = i0 + 1 + k
            keep[mid] = True
            stack.append((i0, mid))
            stack.append((mid, i1))
    return [(float(pts[i, 0]), float(pts[i, 1])) for i in range(n) if keep[i]]


def _polyline_to_svg_path(points: Sequence[Tuple[float, float]], closed: bool = True) -> str:
    """Format a polyline (list of (x, y)) as an SVG `d` string."""
    if not points:
        return ""
    parts = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.2f} {y:.2f}")
    if closed:
        parts.append("Z")
    return " ".join(parts)


# ----------------------------- public API -----------------------------------


def generate_from_png(
    png_path: Path,
    opts: Optional[GeneratorOptions] = None,
) -> GenerationResult:
    """Generate a track-map JSON dict from a single map.png file.

    Raises TrackMapGenerationError on any unrecoverable issue (empty mask,
    no skeleton, disconnected geometry, etc.).
    """
    opts = opts or GeneratorOptions()

    mask, width, height = _load_mask(png_path, opts)
    if not mask.any():
        raise TrackMapGenerationError(
            f"map.png produced an empty mask (alpha_threshold={opts.alpha_threshold}). "
            f"Source: {png_path}"
        )

    mask = _largest_component(mask)
    skel = skeletonize(mask).astype(bool)
    skel_pixels_initial = int(skel.sum())
    if skel_pixels_initial == 0:
        raise TrackMapGenerationError(f"Skeletonization produced no pixels for {png_path}")

    skel, pruned = _prune_branches(skel, opts.branch_min_frac)
    if not skel.any():
        raise TrackMapGenerationError(
            f"All skeleton pixels were pruned (branch_min_frac={opts.branch_min_frac}). "
            f"Source: {png_path}"
        )

    walked_yx = _walk_cycle(skel)
    # Convert (row, col) → (x=col, y=row) for SVG.
    polyline = [(float(x), float(y)) for (y, x) in walked_yx]

    # Simplify with epsilon escalation if the polyline is too long.
    eps = opts.simplify_epsilon
    simplified = _rdp_simplify(polyline, epsilon=eps)
    while len(simplified) > opts.max_vertices and eps < 50.0:
        eps *= 1.5
        simplified = _rdp_simplify(polyline, epsilon=eps)

    svg_path = _polyline_to_svg_path(simplified, closed=True)
    if not svg_path:
        raise TrackMapGenerationError(f"SVG path generation produced empty string for {png_path}")

    return GenerationResult(
        viewBox=f"0 0 {width} {height}",
        svg_path=svg_path,
        start_offset=opts.start_offset,
        direction=opts.direction,
        scale=opts.scale,
        generated_by="scripts/generate_track_maps.py",
        width=width,
        height=height,
        vertex_count=len(simplified),
        skeleton_pixels=skel_pixels_initial,
        pruned_pixels=pruned,
    )
