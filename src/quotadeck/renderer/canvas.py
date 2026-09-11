from __future__ import annotations

import hashlib
import unicodedata

from PIL import Image, ImageChops, ImageDraw, ImageFont

from quotadeck.core.models import normalize_remaining
from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH

BG = (8, 16, 25)
PANEL = (16, 28, 41)
PANEL_2 = (15, 31, 41)
BORDER = (41, 65, 74)
TEXT = (247, 251, 247)
MUTED = (142, 168, 168)
BAR_BG = (222, 231, 222)
BAR_TEXT_ON_FILL = (247, 251, 247)
BAR_TEXT_ON_EMPTY = (8, 16, 16)
INK = TEXT

# Five-by-seven glyphs make the tiny LCD deterministic on every OS.  PIL's
# default font is deliberately not used for quota data because its metrics
# differ between the source build, CI and the frozen Windows application.
PIXEL_TEXT_GLYPHS = {
    " ": ("00000",) * 7,
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01110"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "<": ("00001", "00010", "00100", "01000", "00100", "00010", "00001"),
}
# Compact 3x5 numeric glyphs, scaled to a 25px cap-height.
PIXEL_GLYPHS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("110", "010", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "%": ("10001", "00010", "00100", "01000", "10001"),
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
}

def new_canvas(color: tuple[int, int, int] = BG) -> Image.Image:
    return Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), color)


def font(size: int = 10) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("consola.ttf", size)
    except OSError:
        return ImageFont.load_default()

def draw_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    fill: tuple[int, int, int] = TEXT,
    size: int = 10,
) -> None:
    draw = ImageDraw.Draw(image)
    draw.text(xy, text, fill=fill, font=font(size))


def pixel_text_width(text: str, scale: int = 1) -> int:
    """Return the rendered width of a 5x7 string."""
    return max(0, len(text) * 6 * scale - scale)


def draw_pixel_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    fill: tuple[int, int, int] | int = TEXT,
    scale: int = 1,
) -> None:
    draw = ImageDraw.Draw(image)
    x, y = xy
    for char in text.upper():
        glyph = PIXEL_TEXT_GLYPHS.get(char, PIXEL_TEXT_GLYPHS["?"])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "1":
                    draw.rectangle(
                        (
                            x + gx * scale,
                            y + gy * scale,
                            x + (gx + 1) * scale - 1,
                            y + (gy + 1) * scale - 1,
                        ),
                        fill=fill,
                    )
        x += 6 * scale


def truncate_pixel_text(text: str, max_width: int, scale: int = 1) -> str:
    """Ellipsize by rendered pixels instead of slicing by character count."""
    value = text.upper()
    if pixel_text_width(value, scale) <= max_width:
        return value
    suffix = ".."
    while value and pixel_text_width(value + suffix, scale) > max_width:
        value = value[:-1]
    return value + suffix if value else ""


def truncate_pixel_middle(text: str, max_width: int, scale: int = 1) -> str:
    """Keep both ends of an identifier so account suffixes stay distinguishable."""
    value = text.upper()
    if pixel_text_width(value, scale) <= max_width:
        return value
    capacity = max(0, (max_width + scale) // (6 * scale))
    if capacity <= 2:
        return "." * capacity
    content = capacity - 2
    right = min(3, max(1, content // 2))
    left = content - right
    return value[:left] + ".." + value[-right:]


def pixel_identifier(text: object) -> str:
    """Map arbitrary aliases to a stable, collision-resistant LCD identifier."""
    value = unicodedata.normalize("NFKC", str(text)).upper()
    if all(char in PIXEL_TEXT_GLYPHS for char in value):
        return value
    prefix = "".join(
        char for char in value if char in PIXEL_TEXT_GLYPHS and char.isalnum()
    )[:2]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:5].upper()
    return f"{prefix or 'ID'}-{digest}"


def pixel_number_width(text: str, scale: int = 5) -> int:
    widths = []
    for char in text:
        glyph = PIXEL_GLYPHS.get(char, PIXEL_GLYPHS["-"])
        widths.append((len(glyph[0]) + 1) * scale)
    return max(0, sum(widths) - scale)

def draw_pixel_number(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    fill: tuple[int, int, int] = INK,
    scale: int = 5,
) -> None:
    draw = ImageDraw.Draw(image)
    x, y = xy
    for char in text:
        glyph = PIXEL_GLYPHS.get(char, PIXEL_GLYPHS["-"])
        width = len(glyph[0])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "1":
                    draw.rectangle(
                        (x + gx * scale, y + gy * scale, x + (gx + 1) * scale - 1, y + (gy + 1) * scale - 1),
                        fill=fill,
                    )
        x += (width + 1) * scale

def draw_thin_bar(
    image: Image.Image,
    xy: tuple[int, int],
    remaining: float,
    color: tuple[int, int, int],
    *,
    width: int = 100,
    height: int = 4,
) -> None:
    draw = ImageDraw.Draw(image)
    x, y = xy
    draw.rectangle([x, y, x + width - 1, y + height - 1], fill=BAR_BG)
    fill_w = max(0, min(width, int(round((remaining / 100.0) * width))))
    if remaining > 0:
        fill_w = max(1, fill_w)
    if fill_w:
        draw.rectangle([x, y, x + fill_w - 1, y + height - 1], fill=color)


def quota_bar_label(label: str, remaining: float | None) -> str:
    normalized = normalize_remaining(remaining)
    value = "--" if normalized is None else str(int(round(normalized)))
    return f"{label.upper()} {value}%"


def draw_split_quota_bar(
    image: Image.Image,
    box: tuple[int, int, int, int],
    remaining: float | None,
    label: str,
    color: tuple[int, int, int],
    *,
    outline: tuple[int, int, int] = BORDER,
) -> int:
    """Draw a full-height remaining quota bar with boundary-aware text.

    Text pixels over the dark remaining section are white; the same mask is
    clipped to black over the light depleted section.  The returned x value is
    the first pixel of the empty section and is useful for pixel-level tests.
    """
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=PANEL_2, outline=outline)
    ix0, iy0, ix1, iy1 = x0 + 2, y0 + 2, x1 - 2, y1 - 2
    draw.rectangle((ix0, iy0, ix1, iy1), fill=BAR_BG)
    width = ix1 - ix0 + 1
    normalized = normalize_remaining(remaining)
    value = 0.0 if normalized is None else normalized
    fill_w = max(0, min(width, int(round(value * width / 100.0))))
    boundary = ix0 + fill_w
    if fill_w:
        draw.rectangle((ix0, iy0, boundary - 1, iy1), fill=color)

    text_mask = Image.new("L", image.size, 0)
    label_text = truncate_pixel_text(label, width - 8, scale=2)
    compact = (iy1 - iy0 + 1) <= 54
    label_y = iy0 + 3 if compact else iy0 + 23
    draw_pixel_text(text_mask, (ix0 + 4, label_y), label_text, fill=255, scale=2)
    percent = _quota_percent(remaining)
    percent_w = pixel_number_width(percent, scale=5)
    percent_y = iy1 - 25 - 3 if compact else iy0 + 51
    draw_pixel_number(
        text_mask,
        (max(ix0 + 4, ix1 - percent_w - 4), percent_y),
        percent,
        fill=255,
        scale=5,
    )

    fill_region = Image.new("L", image.size, 0)
    if fill_w:
        ImageDraw.Draw(fill_region).rectangle((ix0, iy0, boundary - 1, iy1), fill=255)
    empty_region = Image.new("L", image.size, 0)
    if boundary <= ix1:
        ImageDraw.Draw(empty_region).rectangle((boundary, iy0, ix1, iy1), fill=255)
    image.paste(BAR_TEXT_ON_FILL, mask=ImageChops.multiply(text_mask, fill_region))
    image.paste(BAR_TEXT_ON_EMPTY, mask=ImageChops.multiply(text_mask, empty_region))
    return boundary


def _normalized_usage_percent(percent: object) -> float | None:
    if percent is None or isinstance(percent, bool):
        return None
    try:
        parsed = float(percent)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 and parsed == parsed else None


def usage_bar_boundary(
    box: tuple[int, int, int, int],
    percent: object,
) -> int:
    """Return the first empty-track pixel using the renderer's exact math."""

    x0, _y0, x1, _y1 = box
    ix0, ix1 = x0 + 2, x1 - 2
    width = ix1 - ix0 + 1
    raw = _normalized_usage_percent(percent)
    fill_percent = 0.0 if raw is None else min(100.0, raw)
    fill_w = max(0, min(width, int(round(fill_percent * width / 100.0))))
    return ix0 + fill_w


def draw_split_usage_bar(
    image: Image.Image,
    box: tuple[int, int, int, int],
    percent: float | None,
    label: str,
    color: tuple[int, int, int],
    *,
    outline: tuple[int, int, int] = BORDER,
) -> int:
    """Draw current-period usage against its completed-period average.

    The visual track deliberately fills at 100%: that is the point where the
    selected period has consumed one historical average.  The numeric label is
    *not* clamped, so 150% and 200% remain visible; the highest reaction band is
    rendered as ``300%+``.  This is separate from ``draw_split_quota_bar``
    because quota percentages are bounded while comparison ratios are not.
    """

    raw = _normalized_usage_percent(percent)

    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=PANEL_2, outline=outline)
    ix0, iy0, ix1, iy1 = x0 + 2, y0 + 2, x1 - 2, y1 - 2
    draw.rectangle((ix0, iy0, ix1, iy1), fill=BAR_BG)
    width = ix1 - ix0 + 1
    boundary = usage_bar_boundary(box, raw)
    fill_w = boundary - ix0
    if fill_w:
        draw.rectangle((ix0, iy0, boundary - 1, iy1), fill=color)

    text_mask = Image.new("L", image.size, 0)
    label_text = truncate_pixel_text(label, width - 8, scale=2)
    draw_pixel_text(text_mask, (ix0 + 4, iy0 + 3), label_text, fill=255, scale=2)
    shown_percent = usage_percent_label(raw)
    percent_w = pixel_number_width(shown_percent, scale=5)
    draw_pixel_number(
        text_mask,
        (max(ix0 + 4, ix1 - percent_w - 4), iy1 - 28),
        shown_percent,
        fill=255,
        scale=5,
    )

    fill_region = Image.new("L", image.size, 0)
    if fill_w:
        ImageDraw.Draw(fill_region).rectangle((ix0, iy0, boundary - 1, iy1), fill=255)
    empty_region = Image.new("L", image.size, 0)
    if boundary <= ix1:
        ImageDraw.Draw(empty_region).rectangle((boundary, iy0, ix1, iy1), fill=255)
    image.paste(BAR_TEXT_ON_FILL, mask=ImageChops.multiply(text_mask, fill_region))
    image.paste(BAR_TEXT_ON_EMPTY, mask=ImageChops.multiply(text_mask, empty_region))
    return boundary


def _quota_percent(remaining: float | None) -> str:
    normalized = normalize_remaining(remaining)
    if normalized is None:
        return "--%"
    return f"{int(round(normalized))}%"


def usage_percent_label(percent: float | None) -> str:
    """Format a non-negative average percentage without crossing a band."""

    if percent is None:
        return "--%"
    if not isinstance(percent, (int, float)):
        return "--%"
    if percent != percent or percent < 0:
        return "--%"
    if percent >= 300 or percent == float("inf"):
        return "300%+"
    # Truncation keeps 149.9% paired with the <1.5x character state instead of
    # rounding the label into the next band.
    return f"{int(percent)}%"

def draw_bar(
    image: Image.Image,
    xy: tuple[int, int],
    size: tuple[int, int],
    remaining: float,
    color: tuple[int, int, int],
) -> None:
    draw_thin_bar(image, xy, remaining, color, width=size[0], height=size[1])


def draw_panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int] = PANEL,
    outline: tuple[int, int, int] = BORDER,
) -> None:
    ImageDraw.Draw(image).rectangle(box, fill=fill, outline=outline)

def draw_holo_pad(
    image: Image.Image,
    center: tuple[int, int],
    *,
    accent: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(image)
    cx, cy = center
    draw.ellipse([cx - 24, cy - 4, cx + 24, cy + 4], fill=(8, 28, 24))
    draw.ellipse([cx - 22, cy - 3, cx + 22, cy + 3], outline=accent)
    draw.line([(cx - 14, cy), (cx + 14, cy)], fill=accent)

def draw_corner_brackets(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    color: tuple[int, int, int],
    arm: int = 5,
) -> None:
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    for x, y, dx, dy in (
        (x0, y0, 1, 1),
        (x1, y0, -1, 1),
        (x0, y1, 1, -1),
        (x1, y1, -1, -1),
    ):
        draw.line([(x, y), (x + dx * arm, y)], fill=color)
        draw.line([(x, y), (x, y + dy * arm)], fill=color)

def severity_color(name: str) -> tuple[int, int, int]:
    return {
        "healthy": (30, 226, 176),
        "busy": (80, 180, 255),
        "caution": (255, 196, 63),
        "critical": (255, 79, 91),
        "exhausted": (255, 64, 80),
        "reset": (180, 255, 120),
        "offline": (90, 96, 104),
        "stale": (160, 140, 80),
        "error": (255, 80, 80),
    }.get(name, (30, 226, 176))
