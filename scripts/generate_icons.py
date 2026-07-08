"""Generate app icons for the webapp (PWA) and the Android launcher.

Draws the same motif as src/server/webapp/icons/app-icon.svg (two stacked
cards on a deep-blue tile) with Pillow and writes:

    src/server/webapp/icons/app-icon-192.png
    src/server/webapp/icons/app-icon-512.png
    mobile-apk/android/app/src/main/res/mipmap-*/ic_launcher.png
    mobile-apk/android/app/src/main/res/mipmap-*/ic_launcher_round.png
    mobile-apk/android/app/src/main/res/mipmap-*/ic_launcher_foreground.png

Run after changing the icon design:

    python scripts/generate_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
WEBAPP_ICONS = REPO / "src" / "server" / "webapp" / "icons"
ANDROID_RES = REPO / "mobile-apk" / "android" / "app" / "src" / "main" / "res"

BG_TOP = (15, 53, 86)
BG_BOTTOM = (9, 26, 43)
CARD_BACK_FILL = (13, 34, 56)
CARD_FILL = (21, 54, 83)
CARD_STROKE = (207, 233, 248)
ART_FILL = (36, 79, 115)
BODY_FILL = (15, 42, 66)
COST_GOLD = (233, 182, 81)
COST_RING = (255, 238, 201)
POWER_RED = (232, 65, 65)
POWER_RING = (255, 181, 181)
CHECK_GREEN = (124, 228, 201)

# Everything is drawn on a large square canvas then downscaled.
BASE = 1024


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (size, size))
    for y in range(size):
        t = y / max(1, size - 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for_line = Image.new("RGB", (size, 1), row)
        image.paste(for_line, (0, y))
    return image


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _draw_cards(draw: ImageDraw.ImageDraw, scale: float, offset_x: float, offset_y: float) -> None:
    """The two-card motif from the SVG, in SVG viewBox units (512)."""

    def box(x: float, y: float, w: float, h: float) -> list[float]:
        return [offset_x + x * scale, offset_y + y * scale, offset_x + (x + w) * scale, offset_y + (y + h) * scale]

    stroke = max(2, int(8 * scale))
    # Back card, shifted down-right.
    draw.rounded_rectangle(box(142, 120, 226, 320), radius=20 * scale, fill=CARD_BACK_FILL, outline=(185, 217, 234), width=stroke)
    # Front card with art and body panels.
    draw.rounded_rectangle(box(108, 76, 226, 320), radius=20 * scale, fill=CARD_FILL, outline=CARD_STROKE, width=stroke)
    draw.rounded_rectangle(box(124, 92, 194, 132), radius=12 * scale, fill=ART_FILL)
    draw.rounded_rectangle(box(124, 242, 194, 138), radius=12 * scale, fill=BODY_FILL)

    # Gold cost circle (top left of the front card).
    cost_box = box(126, 94, 48, 48)
    draw.ellipse(cost_box, fill=COST_GOLD, outline=COST_RING, width=max(2, int(4 * scale)))

    # Red power hex (top right).
    cx, cy, r = 290 * scale + offset_x, 134 * scale + offset_y, 26 * scale
    hexagon = [
        (cx, cy - r),
        (cx + r * 0.87, cy - r * 0.5),
        (cx + r * 0.87, cy + r * 0.5),
        (cx, cy + r),
        (cx - r * 0.87, cy + r * 0.5),
        (cx - r * 0.87, cy - r * 0.5),
    ]
    draw.polygon(hexagon, fill=POWER_RED, outline=POWER_RING)

    # Green check mark on the body panel.
    check_width = max(3, int(18 * scale))
    draw.line(
        [
            (offset_x + 166 * scale, offset_y + 284 * scale),
            (offset_x + 206 * scale, offset_y + 326 * scale),
            (offset_x + 262 * scale, offset_y + 256 * scale),
        ],
        fill=CHECK_GREEN,
        width=check_width,
        joint="curve",
    )


def _tile_icon() -> Image.Image:
    """Full icon: gradient tile + card motif (used for PWA and legacy launcher)."""
    image = _vertical_gradient(BASE, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    scale = BASE / 512
    _draw_cards(draw, scale, 0, 0)
    return image


def _foreground_icon() -> Image.Image:
    """Adaptive-icon foreground: transparent, motif inside the ~66% safe zone."""
    image = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = (BASE / 512) * 0.52
    motif_w = 512 * scale
    offset = (BASE - motif_w) / 2
    _draw_cards(draw, scale, offset, offset)
    return image


def _save(image: Image.Image, size: int, path: Path, mask: Image.Image | None = None) -> None:
    resized = image.resize((size, size), Image.LANCZOS)
    if mask is not None:
        resized.putalpha(mask.resize((size, size), Image.LANCZOS))
    path.parent.mkdir(parents=True, exist_ok=True)
    resized.save(path)
    print(f"wrote {path.relative_to(REPO)}")


def main() -> None:
    tile = _tile_icon()
    foreground = _foreground_icon()
    rounded = _rounded_mask(BASE, int(BASE * 0.22))
    circle = Image.new("L", (BASE, BASE), 0)
    ImageDraw.Draw(circle).ellipse([0, 0, BASE - 1, BASE - 1], fill=255)

    for size in (192, 512):
        _save(tile, size, WEBAPP_ICONS / f"app-icon-{size}.png", mask=rounded)

    launcher_sizes = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    foreground_sizes = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
    for density, size in launcher_sizes.items():
        _save(tile, size, ANDROID_RES / f"mipmap-{density}" / "ic_launcher.png", mask=rounded)
        _save(tile, size, ANDROID_RES / f"mipmap-{density}" / "ic_launcher_round.png", mask=circle)
    for density, size in foreground_sizes.items():
        _save(foreground, size, ANDROID_RES / f"mipmap-{density}" / "ic_launcher_foreground.png")


if __name__ == "__main__":
    main()
