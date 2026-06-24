"""
WORKER GROUP AVATAR GENERATOR — Final
Uses bundled hammer_icon.png (white on transparent) between initials.

Design:
  - Dark outer border ring (color-matched)
  - Unique color per group (deterministic from initials)
  - White bold initials spaced apart
  - Real hammer icon between letters

Usage:
    from avatar_generator import generate_worker_avatar
    path = generate_worker_avatar("WS", "/tmp/avatar_WS.png")

Requires:
    - Pillow
    - hammer_icon.png in same directory as this file
    - fonts-dejavu-core (apt package)
"""

import os
import hashlib
import colorsys
from PIL import Image, ImageDraw, ImageFont

_DIR = os.path.dirname(os.path.abspath(__file__))
_HAMMER_PATH = os.path.join(_DIR, "hammer_icon.png")

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
]

# ── Color palette: 16 distinct, professional construction colors ──
_COLOR_PALETTE = [
    (234, 120, 30),   # Original orange
    (41, 128, 185),   # Steel blue
    (39, 174, 96),    # Emerald green
    (192, 57, 43),    # Brick red
    (142, 68, 173),   # Amethyst purple
    (22, 160, 133),   # Teal
    (211, 84, 0),     # Burnt orange
    (44, 62, 80),     # Dark navy
    (230, 126, 34),   # Carrot orange
    (46, 134, 193),   # Cerulean
    (183, 149, 11),   # Dark gold
    (23, 165, 137),   # Mountain meadow
    (165, 105, 189),  # Soft purple
    (52, 152, 219),   # Sky blue
    (211, 84, 0),     # Pumpkin
    (26, 188, 156),   # Turquoise
]


def _get_color_for_initials(seed: str) -> tuple:
    """Generate a unique, vibrant color from any string. Deterministic — same input = same color."""
    h = hashlib.md5(seed.encode()).hexdigest()

    # Use hash bytes to pick hue (0-360), keep saturation & lightness in professional range
    hue = int(h[:3], 16) % 360 / 360.0        # full hue spectrum
    sat = 0.55 + (int(h[3:5], 16) % 20) / 100  # 0.55-0.75 (vivid but not neon)
    lit = 0.42 + (int(h[5:7], 16) % 12) / 100  # 0.42-0.54 (not too dark, not too light)

    r, g, b = colorsys.hls_to_rgb(hue, lit, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


def _get_border_color(bg_color: tuple) -> tuple:
    """Darker shade of the background color for the border ring."""
    r, g, b = bg_color
    return (max(0, int(r * 0.35)), max(0, int(g * 0.35)), max(0, int(b * 0.35)), 255)


def _get_shadow_color(bg_color: tuple) -> tuple:
    """Semi-transparent darker shade for text shadow."""
    r, g, b = bg_color
    return (max(0, int(r * 0.4)), max(0, int(g * 0.4)), max(0, int(b * 0.4)), 80)


def _get_bold_font(size: int):
    for fp in _FONT_PATHS:
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def _load_hammer():
    if os.path.exists(_HAMMER_PATH):
        return Image.open(_HAMMER_PATH).convert("RGBA")
    print(f"[avatar] ⚠️  hammer_icon.png not found at {_HAMMER_PATH}")
    return None


def generate_worker_avatar(
    initials: str,
    output_path: str = None,
    size: int = 640,
    color_seed: str = None,
) -> str:
    if not output_path:
        import tempfile
        output_path = os.path.join(tempfile.gettempdir(), f"wg_avatar_{initials}.png")

    initials = (initials or "WG").upper()
    is_single = len(initials) == 1  # Single char mode (e.g. "?")

    if is_single:
        char1 = initials[0]
        char2 = None
    else:
        initials = initials[:2]
        char1 = initials[0]
        char2 = initials[1] if len(initials) > 1 else "G"

    # ── Pick unique color ──
    seed = (color_seed or initials).upper()
    bg_color = _get_color_for_initials(seed)
    border_color = _get_border_color(bg_color)
    shadow_color = _get_shadow_color(bg_color)

    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    c = size // 2

    # ── Dark border ring (color-matched) ──
    bt = int(size * 0.058)
    oR = c - 2
    iR = oR - bt
    draw.ellipse([c - oR, c - oR, c + oR, c + oR], fill=border_color)

    # ── Main circle ──
    draw.ellipse([c - iR, c - iR, c + iR, c + iR], fill=bg_color)

    if is_single:
        # ── Single character centered (no hammer) ──
        fs = int(size * 0.38)
        font = _get_bold_font(fs)
        bb = draw.textbbox((0, 0), char1, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = c - tw // 2 - bb[0]
        y = c - th // 2 - bb[1] + int(size * 0.02)
        draw.text((x + 2, y + 2), char1, fill=shadow_color, font=font)
        draw.text((x, y), char1, fill=(255, 255, 255, 255), font=font)

        img.save(output_path, 'PNG', quality=95)
        return output_path

    # ── Two-character mode with hammer ──

    # ── Font + measure ──
    fs = int(size * 0.26)
    font = _get_bold_font(fs)

    bb1 = draw.textbbox((0, 0), char1, font=font)
    bb2 = draw.textbbox((0, 0), char2, font=font)
    w1, w2 = bb1[2] - bb1[0], bb2[2] - bb2[0]
    ch = max(bb1[3] - bb1[1], bb2[3] - bb2[1])

    # ── Hammer sizing ──
    hammer = _load_hammer()
    if hammer:
        hh = int(ch * 0.95)
        ha = hammer.width / hammer.height
        hw = int(hh * ha)
        gap = hw + int(size * 0.02)
    else:
        gap = int(size * 0.08)
        hw, hh = 0, 0

    total_w = w1 + gap + w2

    # Auto-scale down if content overflows inner circle
    max_w = int(iR * 1.6)
    if total_w > max_w:
        scale = max_w / total_w
        fs = int(fs * scale)
        font = _get_bold_font(fs)
        bb1 = draw.textbbox((0, 0), char1, font=font)
        bb2 = draw.textbbox((0, 0), char2, font=font)
        w1, w2 = bb1[2] - bb1[0], bb2[2] - bb2[0]
        ch = max(bb1[3] - bb1[1], bb2[3] - bb2[1])
        if hammer:
            hh = int(ch * 0.95)
            hw = int(hh * ha)
            gap = hw + int(size * 0.02)
        total_w = w1 + gap + w2

    total_w = w1 + gap + w2
    sx = c - total_w // 2
    ty = c - ch // 2 + int(size * 0.03)

    # ── First letter ──
    x1 = sx - bb1[0]
    y1 = ty - bb1[1]
    draw.text((x1 + 2, y1 + 2), char1, fill=shadow_color, font=font)
    draw.text((x1, y1), char1, fill=(255, 255, 255, 255), font=font)

    # ── Second letter ──
    x2 = sx + w1 + gap - bb2[0]
    y2 = ty - bb2[1]
    draw.text((x2 + 2, y2 + 2), char2, fill=shadow_color, font=font)
    draw.text((x2, y2), char2, fill=(255, 255, 255, 255), font=font)

    # ── Hammer between letters ──
    if hammer:
        hr = hammer.resize((hw, hh), Image.LANCZOS)
        hx = sx + w1 + (gap - hw) // 2
        hy = ty + (ch - hh) // 2
        img.paste(hr, (hx, hy), hr)

    img.save(output_path, 'PNG', quality=95)
    return output_path


def build_initials(worker_names: list, customer_last_name: str) -> str:
    first_char = "W"
    second_char = "S"
    if worker_names and worker_names[0]:
        first_char = worker_names[0][0].upper()
    if customer_last_name:
        second_char = customer_last_name[0].upper()
    elif len(worker_names) > 1 and worker_names[1]:
        second_char = worker_names[1][0].upper()
    return f"{first_char}{second_char}"


if __name__ == "__main__":
    import sys
    ini = sys.argv[1] if len(sys.argv) > 1 else "WS"
    out = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/avatar_{ini}.png"
    path = generate_worker_avatar(ini, out)
    print(f"✅ Generated: {path}")