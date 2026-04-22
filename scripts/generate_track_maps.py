"""
PitBox Live Timing — track-map JSON generator CLI.

Walks an Assetto Corsa content tree, finds every `map.png`, runs the
track_map_generator pipeline, and writes JSON files to
`controller/static/track_maps/<key>.json` where <key> is the same slug the
Live Timing frontend builds from `session.track_name` / `session.track_config`:

    slug(s) = lower-case s, non-alphanum -> "_", strip leading/trailing "_"
    key     = slug(track) + "__" + slug(layout)   when a layout is present
    key     = slug(track)                          when there is no layout

Usage
-----

    # Walk the whole AC install (default Steam path)
    python -m scripts.generate_track_maps \\
        --ac-root "C:\\Program Files (x86)\\Steam\\steamapps\\common\\assettocorsa"

    # Only one track / layout
    python -m scripts.generate_track_maps \\
        --ac-root "..." --track ks_nordschleife --layout endurance

    # Re-generate even if the file already exists (default: skip existing
    # hand-edited files, overwrite ones we generated previously)
    python -m scripts.generate_track_maps --ac-root "..." --force

This is a BUILD-TIME tool (see requirements-tools.txt). It is not invoked
by the controller or agent at runtime.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from scripts.track_map_generator import (
    GeneratorOptions,
    TrackMapGenerationError,
    generate_from_png,
)


# Output directory, relative to repo root.
DEFAULT_OUT_DIR = Path("controller") / "static" / "track_maps"

# Marker we stamp into generated JSON so we know it's safe to overwrite.
GENERATED_MARKER_KEY = "generated_by"


def slugify(s: str) -> str:
    """Mirror of the JS slugify in controller/static/live_timing.js.

        function slugify(s) {
            return String(s || '').toLowerCase()
                .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
        }
    """
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"^_+|_+$", "", s)
    return s


def map_key(track_id: str, layout: Optional[str]) -> str:
    t = slugify(track_id)
    c = slugify(layout or "")
    return f"{t}__{c}" if c else t


def _iter_track_dirs(ac_root: Path) -> Iterator[Path]:
    tracks_dir = ac_root / "content" / "tracks"
    if not tracks_dir.is_dir():
        return iter(())
    return (p for p in tracks_dir.iterdir() if p.is_dir())


def _iter_layouts_for_track(track_dir: Path) -> List[Tuple[Optional[str], Path]]:
    """Yield (layout_name_or_None, map_png_path) for every map this track
    can produce. Mirrors the resolution priority in api_routes.py:

        ui/<layout>/map.png   per-layout
        ui/map.png            base (no layout) — also emitted as the
                              `<track>` fallback key the JS frontend tries
                              when a `<track>__<layout>` JSON is missing
        map.png               track root (only as a last resort)

    A track may legitimately produce BOTH per-layout maps AND a bare
    `<track>` map. The frontend's fallback chain depends on the bare key
    existing whenever there is a shared `ui/map.png`.
    """
    out: List[Tuple[Optional[str], Path]] = []
    ui = track_dir / "ui"
    if ui.is_dir():
        # Per-layout maps live in ui/<layout>/map.png. Other entries (like
        # ui_track.json files at ui/ root) are ignored.
        for sub in sorted(ui.iterdir(), key=lambda p: p.name):
            if sub.is_dir():
                p = sub / "map.png"
                if p.is_file():
                    out.append((sub.name, p))
        # Always include the shared base ui/map.png as the bare-track key
        # too, even if per-layout maps exist — supports the frontend's
        # `<track>__<layout>` -> `<track>` fallback.
        base = ui / "map.png"
        if base.is_file():
            out.append((None, base))
    if not out:
        # Last resort — track root (some legacy tracks).
        p = track_dir / "map.png"
        if p.is_file():
            out.append((None, p))
    return out


def _safe_to_overwrite(existing_path: Path) -> bool:
    """We never clobber a hand-edited JSON. We DO overwrite files that we
    generated previously (identified by the `generated_by` marker)."""
    try:
        with existing_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        # Corrupt or unreadable — treat as ours; overwriting can only help.
        return True
    return GENERATED_MARKER_KEY in data


def _process_one(
    track_id: str,
    layout: Optional[str],
    png_path: Path,
    out_dir: Path,
    opts: GeneratorOptions,
    force: bool,
) -> Tuple[str, str]:
    """Generate one JSON file. Returns (key, status_str) for reporting."""
    key = map_key(track_id, layout)
    out_path = out_dir / f"{key}.json"
    if out_path.exists() and not force:
        if not _safe_to_overwrite(out_path):
            return (key, "SKIP (hand-edited)")
    try:
        result = generate_from_png(png_path, opts)
    except TrackMapGenerationError as e:
        return (key, f"FAIL ({e})")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = result.to_json_dict()
    # Stamp the source for traceability — lets a future operator regenerate
    # this exact file by pointing the CLI at the same png.
    payload["source_png"] = str(png_path)
    payload["track"] = track_id
    if layout:
        payload["layout"] = layout
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return (key, f"OK ({result.vertex_count} verts, pruned {result.pruned_pixels}px)")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ac-root", required=True, type=Path,
                   help=r'Path to the Assetto Corsa install (e.g. "C:\Program Files (x86)\Steam\steamapps\common\assettocorsa")')
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"Output directory (default: {DEFAULT_OUT_DIR})")
    p.add_argument("--track", default=None,
                   help="Only process this track_id (folder name under content/tracks/).")
    p.add_argument("--layout", default=None,
                   help="With --track, only process this layout. Use 'default' for tracks with no layouts.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing JSON files even if they were hand-edited.")
    p.add_argument("--alpha-threshold", type=int, default=GeneratorOptions.alpha_threshold)
    p.add_argument("--branch-min-frac", type=float, default=GeneratorOptions.branch_min_frac)
    p.add_argument("--simplify-epsilon", type=float, default=GeneratorOptions.simplify_epsilon)
    p.add_argument("--max-vertices", type=int, default=GeneratorOptions.max_vertices)
    args = p.parse_args(argv)

    if not args.ac_root.is_dir():
        print(f"ERROR: --ac-root does not exist: {args.ac_root}", file=sys.stderr)
        return 2

    opts = GeneratorOptions(
        alpha_threshold=args.alpha_threshold,
        branch_min_frac=args.branch_min_frac,
        simplify_epsilon=args.simplify_epsilon,
        max_vertices=args.max_vertices,
    )

    # Collect work units.
    work: List[Tuple[str, Optional[str], Path]] = []
    if args.track:
        track_dir = args.ac_root / "content" / "tracks" / args.track
        if not track_dir.is_dir():
            print(f"ERROR: track not found: {track_dir}", file=sys.stderr)
            return 2
        layouts = _iter_layouts_for_track(track_dir)
        if args.layout:
            wanted = None if args.layout.lower() == "default" else args.layout
            layouts = [(ln, pp) for (ln, pp) in layouts if ln == wanted]
            if not layouts:
                print(f"ERROR: no map.png found for layout {args.layout!r} of track {args.track!r}",
                      file=sys.stderr)
                return 2
        for layout, png in layouts:
            work.append((args.track, layout, png))
    else:
        for td in _iter_track_dirs(args.ac_root):
            for layout, png in _iter_layouts_for_track(td):
                work.append((td.name, layout, png))

    if not work:
        print("No map.png files found.", file=sys.stderr)
        return 1

    # Process and report.
    print(f"Generating {len(work)} track map JSON file(s) → {args.out_dir.resolve()}")
    n_ok = 0
    n_skip = 0
    n_fail = 0
    for track_id, layout, png in work:
        key, status = _process_one(track_id, layout, png, args.out_dir, opts, args.force)
        label = f"{track_id}" + (f" / {layout}" if layout else "")
        print(f"  [{key:48s}]  {label:50s}  {status}")
        if status.startswith("OK"):
            n_ok += 1
        elif status.startswith("SKIP"):
            n_skip += 1
        else:
            n_fail += 1
    print(f"\nDone. ok={n_ok}  skip={n_skip}  fail={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
