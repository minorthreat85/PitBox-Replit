"""
Audit and build PitBox installer assets:
- Audit existing .ico (multi-res, square 256x256)
- Generate square multi-res ICOs: pitbox.ico, controller.ico, agent.ico
- Generate wizard_left.bmp (164x314) and wizard_small.bmp (55x58)

Requires: pip install Pillow
Run from repo root: python scripts/build_installer_assets.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root = parent of scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "installer" / "assets"
REQUIRED_ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
BG_COLOR = (0x0B, 0x0B, 0x0B)  # #0B0B0B
RED_ACCENT = (0xE3, 0x06, 0x1D)  # #E3061D
WHITE = (255, 255, 255)


def audit_ico(path: Path) -> dict:
    """Return report: sizes list, all_square, has_256_square, ok."""
    try:
        from PIL import Image
        img = Image.open(path)
        if not getattr(img, "ico", None):
            return {"sizes": [], "all_square": False, "has_256_square": False, "ok": False, "error": "not ICO"}
        sizes = []
        all_square = True
        has_256_square = False
        for entry in img.ico.entry:
            w, h = getattr(entry, "width", 0), getattr(entry, "height", 0)
            if w and h:
                sizes.append((w, h))
                if w != h:
                    all_square = False
                if w == 256 and h == 256:
                    has_256_square = True
        return {
            "sizes": sizes,
            "all_square": all_square,
            "has_256_square": has_256_square,
            "ok": all_square and has_256_square and len(sizes) >= 5,
            "error": None,
        }
    except Exception as e:
        return {"sizes": [], "all_square": False, "has_256_square": False, "ok": False, "error": str(e)}


def main():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow required: pip install Pillow")
        sys.exit(1)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Audit existing ICOs ---
    icos = ["pitbox.ico", "controller.ico", "agent.ico"]
    need_rebuild = []
    for name in icos:
        path = ASSETS_DIR / name
        if path.exists():
            r = audit_ico(path)
            print(f"{name}: sizes={r['sizes']} square={r['all_square']} 256x256={r['has_256_square']} ok={r['ok']} {r.get('error') or ''}")
            if not r["ok"]:
                need_rebuild.append(name)
        else:
            print(f"{name}: missing -> will create")
            need_rebuild.append(name)

    def draw_pb_mark(draw: ImageDraw.ImageDraw, box: tuple, color: tuple, stroke: int = 2):
        """Draw a simple PB / rounded square mark in box (l,t,r,b)."""
        l, t, r, b = box
        w, h = r - l, b - t
        margin = max(2, min(w, h) // 6)
        draw.rounded_rectangle([l + margin, t + margin, r - margin, b - margin], outline=color, width=max(1, stroke), radius=max(1, min(w, h) // 8))

    def draw_screen_glyph(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple):
        """Small screen/rectangle glyph."""
        s = size // 2
        draw.rectangle([cx - s, cy - s - 1, cx + s, cy + s + 1], outline=color, width=max(1, size // 16))

    def draw_chip_glyph(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple):
        """Small chip/diamond glyph."""
        s = size // 2
        draw.polygon([(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)], outline=color, width=max(1, size // 12))

    def make_icon_frame(size: int, variant: str) -> Image.Image:
        """Variant: pitbox, controller, agent."""
        img = Image.new("RGBA", (size, size), (*BG_COLOR, 0))
        img.putalpha(255)
        draw = ImageDraw.Draw(img)
        margin = size // 6
        box = (margin, margin, size - margin, size - margin)
        draw_pb_mark(draw, box, WHITE, max(1, size // 32))
        if variant == "controller":
            draw_screen_glyph(draw, size // 2, size // 2, size // 3, RED_ACCENT)
        elif variant == "agent":
            draw_chip_glyph(draw, size // 2, size // 2, size // 3, RED_ACCENT)
        return img

    # --- Build ICOs (square, multi-res) ---
    for name in need_rebuild:
        if name == "pitbox.ico":
            variant = "pitbox"
        elif name == "controller.ico":
            variant = "controller"
        else:
            variant = "agent"
        out_path = ASSETS_DIR / name
        frames = []
        for s in REQUIRED_ICO_SIZES:
            frames.append(make_icon_frame(s, variant))
        # Save as ICO (Pillow saves largest first; we need multiple sizes)
        largest = frames[-1]  # 256
        # ICO can store multiple sizes: save each size
        largest.save(out_path, format="ICO", sizes=[(f.width, f.height) for f in frames])
        # Pillow save ICO with sizes: pass list of (w,h) and it uses the largest image to generate; we need to save with all frames
        # Actually PIL Image.save(..., format='ICO', sizes=[(16,16),(24,24),...]) generates from the image - it might only save one size. So we need to build ICO manually or use a lib. Check: Pillow 10 ICO save with sizes=
        # From docs: When saving, only the first image is saved. So we must merge all sizes into one ICO. Use PIL's capability: open each frame, then we need to save multi-size ICO. In Pillow, to save multiple sizes you typically append them. Let me try: save the 256 frame with sizes=REQUIRED_ICO_SIZES - Pillow might resize. Yes: "sizes" parameter for ICO save tells it to create those sizes from the given image.
        largest.save(out_path, format="ICO", sizes=[(s, s) for s in REQUIRED_ICO_SIZES])
        print(f"Created {out_path} (square multi-res {REQUIRED_ICO_SIZES})")

    # --- Wizard bitmaps (24-bit BMP, no alpha) ---
    # A) wizard_left.bmp 164x314
    wiz_left = Image.new("RGB", (164, 314), BG_COLOR)
    draw = ImageDraw.Draw(wiz_left)
    # Red accent bar (vertical, right edge)
    bar_w = 4
    draw.rectangle([164 - bar_w, 0, 164, 314], fill=RED_ACCENT)
    # Text: FASTEST LAP (top), PITBOX (below)
    try:
        font_big = ImageFont.truetype("arial.ttf", 18)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = font_big
    draw.text((12, 40), "FASTEST LAP", fill=WHITE, font=font_big)
    draw.text((12, 75), "PITBOX", fill=WHITE, font=font_small)
    wiz_left_path = ASSETS_DIR / "wizard_left.bmp"
    wiz_left.save(wiz_left_path, format="BMP")
    print(f"Created {wiz_left_path}")

    # B) wizard_small.bmp 55x58
    wiz_small = Image.new("RGB", (55, 58), BG_COLOR)
    draw = ImageDraw.Draw(wiz_small)
    draw_pb_mark(draw, (6, 6, 49, 52), WHITE, 2)
    draw.rectangle([50, 0, 55, 58], fill=RED_ACCENT)  # thin accent
    wiz_small_path = ASSETS_DIR / "wizard_small.bmp"
    wiz_small.save(wiz_small_path, format="BMP")
    print(f"Created {wiz_small_path}")

    print("Done. installer/assets ready for Inno Setup.")


if __name__ == "__main__":
    main()
