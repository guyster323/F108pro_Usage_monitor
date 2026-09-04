from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH

BG = (7, 12, 20)
PANEL = (11, 23, 32)
PANEL_2 = (15, 31, 41)
BORDER = (24, 74, 77)
TEXT = (228, 249, 246)
MUTED = (92, 132, 136)
BAR_BG = (26, 47, 54)
INK = (228, 249, 246)

# GPT Sol 3x5 pixel glyphs, scaled to 25px cap-height.
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
