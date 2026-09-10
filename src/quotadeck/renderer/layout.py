from __future__ import annotations

from datetime import timezone

from PIL import Image, ImageDraw

from quotadeck.core.models import (
    Severity,
    UsageSnapshot,
    UsageWindow,
    display_windows,
    normalize_remaining,
)
from quotadeck.core.severity import band_for_remaining
from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.renderer.canvas import (
    BORDER,
    MUTED,
    PANEL,
    TEXT,
    draw_corner_brackets,
    draw_panel,
    draw_pixel_text,
    draw_split_quota_bar,
    new_canvas,
    pixel_identifier,
    pixel_text_width,
    severity_color,
    truncate_pixel_middle,
    truncate_pixel_text,
)

# Inclusive pixel boxes. A single account always owns the full LCD.
HEADER = (3, 3, 236, 18)
CHAR_BAY = (3, 21, 92, 132)
RATE_BAY = (97, 21, 236, 132)
SPRITE_SLOT = (4, 23, 91, 130)
RATE_TOP = (97, 21, 236, 74)
RATE_BOTTOM = (97, 79, 236, 132)
RATE_SINGLE = RATE_BAY
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

METER_COLORS = {
    Severity.HEALTHY: (8, 93, 74),
    Severity.BUSY: (25, 93, 123),
    Severity.CAUTION: (115, 81, 0),
    Severity.CRITICAL: (132, 40, 58),
    Severity.EXHAUSTED: (132, 40, 58),
    Severity.OFFLINE: (66, 73, 74),
    Severity.STALE: (107, 81, 33),
    Severity.ERROR: (132, 40, 58),
    Severity.RESET: (8, 93, 74),
}


def _hex(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def reset_parts(snapshot: UsageSnapshot) -> tuple[str, str] | None:
    """Retained for API compatibility; reset text is no longer put on the LCD."""
    times = [window.resets_at for window in snapshot.windows if window.resets_at]
    if not times:
        return None
    soonest = min(times)
    if soonest.tzinfo is None:
        soonest = soonest.replace(tzinfo=timezone.utc)
    local = soonest.astimezone()
    return WEEKDAYS[local.weekday()], local.strftime("%H:%M")


def _meter_color(remaining: float | None) -> tuple[int, int, int]:
    return METER_COLORS[band_for_remaining(normalize_remaining(remaining))]


def _compact_window_label(label: str) -> str:
    raw = " ".join(label.upper().replace("_", " ").split())
    dense = raw.replace(" ", "")
    if dense in {"5H", "5HR", "5HRS"} or ("5" in dense and "HOUR" in dense):
        return "5H"
    if "WEEK" in dense:
        return "WEEKLY"
    if "OTHER" in dense:
        return "OTHER"
    if "AUTO" in dense or "CURSOR" in dense:
        return "AUTO"
    return truncate_pixel_text(raw or "QUOTA", 104, scale=2)


def _fit_sprite(
    sprite: Image.Image,
    slot: tuple[int, int, int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    """Fit oversized art without cropping it, then bottom-center the character."""
    x0, y0, x1, y1 = slot
    max_w = x1 - x0 + 1
    max_h = y1 - y0 + 1
    fitted = sprite.convert("RGBA")
    if fitted.width > max_w or fitted.height > max_h:
        alpha_box = fitted.getchannel("A").getbbox()
        if alpha_box:
            fitted = fitted.crop(alpha_box)
        scale = min(max_w / max(1, fitted.width), max_h / max(1, fitted.height))
        size = (
            max(1, int(round(fitted.width * scale))),
            max(1, int(round(fitted.height * scale))),
        )
        fitted = fitted.resize(size, Image.Resampling.NEAREST)
    sx = x0 + max(0, (max_w - fitted.width) // 2)
    sy = y1 - fitted.height + 1
    return fitted, (sx, max(y0, sy))


def _draw_header(
    image: Image.Image,
    snapshot: UsageSnapshot,
    severity: Severity,
    accent: tuple[int, int, int],
    position: tuple[int, int] | None,
) -> None:
    draw = ImageDraw.Draw(image)
    draw.rectangle(HEADER, fill=PANEL, outline=BORDER)
    provider = truncate_pixel_text(snapshot.provider, 70, scale=2)
    draw_pixel_text(image, (6, 4), provider, fill=accent, scale=2)
    alias_x = 6 + pixel_text_width(provider, scale=2) + 6
    index_x: int | None = None
    if position:
        index = f"{position[0]}/{position[1]}"
        index_x = 220 - pixel_text_width(index)
    right_edge = index_x - 6 if index_x is not None else 220
    alias = truncate_pixel_middle(
        pixel_identifier(snapshot.display_name),
        max(0, right_edge - alias_x),
        scale=2,
    )
    if alias:
        draw_pixel_text(image, (alias_x, 4), alias, fill=MUTED, scale=2)
    if position:
        assert index_x is not None
        draw_pixel_text(
            image,
            (index_x, 7),
            index,
            fill=TEXT,
        )
    _draw_status_icon(draw, severity, severity_color(severity.value))


def _draw_status_icon(
    draw: ImageDraw.ImageDraw,
    severity: Severity,
    color: tuple[int, int, int],
) -> None:
    """Draw a tiny shape cue so status never depends on colour alone."""
    if severity == Severity.HEALTHY:
        draw.polygon(((231, 5), (235, 9), (231, 14), (227, 9)), fill=color)
    elif severity == Severity.BUSY:
        draw.line(((227, 6), (230, 9), (227, 12)), fill=color, width=2)
        draw.line(((231, 6), (234, 9), (231, 12)), fill=color, width=2)
    elif severity == Severity.CAUTION:
        draw.line(((231, 5), (235, 14), (227, 14), (231, 5)), fill=color)
        draw.point((231, 11), fill=color)
    elif severity == Severity.CRITICAL:
        draw.rectangle((230, 5, 232, 10), fill=color)
        draw.rectangle((230, 13, 232, 14), fill=color)
    elif severity == Severity.EXHAUSTED:
        draw.line(((227, 5), (235, 14)), fill=color, width=2)
        draw.line(((235, 5), (227, 14)), fill=color, width=2)
    elif severity == Severity.ERROR:
        draw.rectangle((227, 5, 235, 14), outline=color)
        draw.line(((229, 7), (231, 6), (233, 7), (231, 10)), fill=color)
        draw.point((231, 12), fill=color)
    elif severity == Severity.OFFLINE:
        draw.rectangle((227, 8, 235, 11), fill=color)
        draw.point(((227, 6), (235, 13)), fill=color)
    elif severity == Severity.STALE:
        draw.rectangle((228, 6, 234, 13), outline=color)
        draw.line(((231, 7), (231, 10), (233, 11)), fill=color)
    else:  # reset
        draw.ellipse((227, 5, 235, 14), outline=color)
        draw.polygon(((233, 5), (236, 5), (235, 8)), fill=color)


def paint_account(
    snapshot: UsageSnapshot,
    severity: Severity,
    sprite: Image.Image,
    accent: str,
    *,
    position: tuple[int, int] | None = None,
) -> Image.Image:
    image = new_canvas()
    draw = ImageDraw.Draw(image)
    accent_rgb = _hex(accent)
    draw.rectangle((0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1), outline=BORDER)
    _draw_header(image, snapshot, severity, accent_rgb, position)

    draw_panel(image, CHAR_BAY, fill=PANEL, outline=BORDER)
    draw_corner_brackets(image, CHAR_BAY, color=accent_rgb, arm=6)
    fitted, origin = _fit_sprite(sprite, SPRITE_SLOT)
    image.paste(fitted, origin, fitted)

    windows = display_windows(snapshot)
    if not windows and snapshot.critical_remaining is not None:
        windows = [
            UsageWindow(
                id="remaining",
                label="QUOTA",
                used_percent=100 - snapshot.critical_remaining,
                remaining_percent=snapshot.critical_remaining,
            )
        ]

    if len(windows) >= 2:
        for box, window in zip((RATE_TOP, RATE_BOTTOM), windows, strict=False):
            draw_split_quota_bar(
                image,
                box,
                window.remaining_percent,
                _compact_window_label(window.label),
                _meter_color(window.remaining_percent),
                outline=BORDER,
            )
    elif windows:
        window = windows[0]
        draw_split_quota_bar(
            image,
            RATE_SINGLE,
            window.remaining_percent,
            _compact_window_label(window.label),
            _meter_color(window.remaining_percent),
            outline=BORDER,
        )
    else:
        draw_split_quota_bar(
            image,
            RATE_SINGLE,
            None,
            "NO DATA",
            METER_COLORS.get(severity, METER_COLORS[Severity.STALE]),
            outline=BORDER,
        )
    return image


def paint_empty() -> Image.Image:
    image = new_canvas()
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1), outline=BORDER)
    draw_panel(image, HEADER, fill=PANEL, outline=BORDER)
    draw_pixel_text(image, (6, 4), "QUOTADECK", fill=(25, 215, 156), scale=2)
    draw_panel(image, CHAR_BAY, fill=PANEL, outline=BORDER)
    draw_corner_brackets(image, CHAR_BAY, color=BORDER, arm=6)
    draw_pixel_text(image, (19, 62), "NO", fill=MUTED, scale=2)
    draw_pixel_text(image, (8, 81), "CREW", fill=MUTED, scale=2)
    draw_split_quota_bar(
        image,
        RATE_SINGLE,
        None,
        "NO ACCOUNT",
        METER_COLORS[Severity.STALE],
        outline=BORDER,
    )
    return image


def paint_transition(previous: Image.Image, nxt: Image.Image, kind: str = "scan") -> Image.Image:
    if kind == "dissolve":
        return Image.blend(previous.convert("RGB"), nxt.convert("RGB"), 0.5)
    image = previous.convert("RGB").copy()
    band = nxt.crop((0, 50, LCD_WIDTH, 90))
    image.paste(band, (0, 50))
    return image
