from __future__ import annotations

from datetime import timezone

from PIL import Image, ImageDraw

from quotadeck.core.models import Severity, UsageSnapshot, UsageWindow
from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.renderer.canvas import (
    BORDER,
    INK,
    MUTED,
    PANEL,
    PANEL_2,
    TEXT,
    draw_corner_brackets,
    draw_holo_pad,
    draw_panel,
    draw_pixel_number,
    draw_text,
    draw_thin_bar,
    new_canvas,
    pixel_number_width,
    severity_color,
)

# Inclusive pixel boxes. Character bay and rate bay never overlap.
HEADER = (4, 3, 235, 17)
CHAR_BAY = (4, 20, 86, 131)
RATE_BAY = (93, 20, 235, 131)
SPRITE_SLOT = (7, 24, 83, 101)
STATE_BADGE = (8, 114, 82, 127)
RATE_HERO = (97, 24, 231, 67)
RATE_WEEK = (97, 71, 231, 103)
RESET_CARD = (97, 107, 231, 127)

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
STATUS_PILL = {
    "healthy": "ONLINE",
    "busy": "ONLINE",
    "caution": "LOW",
    "critical": "ALERT",
    "exhausted": "ALERT",
    "reset": "RESET",
    "offline": "OFF",
    "stale": "STALE",
    "error": "ERROR",
}
STATE_LABEL = {
    "healthy": "RUNNING",
    "busy": "RUNNING",
    "caution": "RESERVE",
    "critical": "DEPLETED",
    "exhausted": "DEPLETED",
    "reset": "RECHARGE",
    "offline": "OFFLINE",
    "stale": "STALE",
    "error": "ERROR",
}


def _hex(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def reset_parts(snapshot: UsageSnapshot) -> tuple[str, str] | None:
    times = [w.resets_at for w in snapshot.windows if w.resets_at]
    if not times:
        return None
    soonest = min(times)
    if soonest.tzinfo is None:
        soonest = soonest.replace(tzinfo=timezone.utc)
    local = soonest.astimezone()
    return WEEKDAYS[local.weekday()], local.strftime("%H:%M")


def _percent_text(value: float | None) -> str:
    if value is None:
        return "--%"
    return f"{max(0, min(100, int(round(value))))}%"


def _meter_color(remaining: float) -> tuple[int, int, int]:
    if remaining <= 10:
        return severity_color("critical")
    if remaining <= 20:
        return severity_color("caution")
    return (30, 226, 176)


def _right_pixel_number(
    image: Image.Image,
    right: int,
    y: int,
    text: str,
    fill: tuple[int, int, int],
    scale: int = 5,
) -> None:
    width = pixel_number_width(text, scale)
    draw_pixel_number(image, (right - width, y), text, fill=fill, scale=scale)


def _fit_sprite(sprite: Image.Image, slot: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int]]:
    x0, y0, x1, y1 = slot
    max_w = x1 - x0 + 1
    max_h = y1 - y0 + 1
    fitted = sprite.convert("RGBA")
    if fitted.width > max_w or fitted.height > max_h:
        fitted = fitted.crop((0, 0, min(fitted.width, max_w), min(fitted.height, max_h)))
    sx = x0 + max(0, (max_w - fitted.width) // 2)
    sy = y0 + max(0, (max_h - fitted.height) // 2)
    return fitted, (sx, sy)


def paint_account(
    snapshot: UsageSnapshot,
    severity: Severity,
    sprite: Image.Image,
    accent: str,
) -> Image.Image:
    image = new_canvas()
    draw = ImageDraw.Draw(image)
    color = severity_color(severity.value)
    accent_rgb = _hex(accent)

    draw.rectangle((0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1), outline=(18, 36, 44))
    draw_text(image, (6, 4), snapshot.provider.upper()[:8], fill=accent_rgb, size=10)
    alias = snapshot.display_name[:8].upper()
    draw_text(image, (64, 5), f"// {alias}", fill=MUTED, size=8)
    pill = STATUS_PILL.get(severity.value, "ONLINE")
    draw_text(image, (186 if len(pill) > 5 else 196, 4), pill, fill=color, size=9)

    draw_panel(image, CHAR_BAY, fill=PANEL, outline=BORDER)
    draw_panel(image, RATE_BAY, fill=PANEL, outline=BORDER)
    draw.rectangle((87, 20, 92, 131), fill=(7, 12, 20))
    draw_corner_brackets(image, CHAR_BAY, color=accent_rgb)
    draw_corner_brackets(image, RATE_BAY, color=BORDER)

    draw_holo_pad(image, (45, 97), accent=accent_rgb)
    fitted, origin = _fit_sprite(sprite, SPRITE_SLOT)
    image.paste(fitted, origin, fitted)

    bx0, by0, bx1, by1 = STATE_BADGE
    draw.rectangle((bx0, by0, bx1, by1), fill=PANEL_2, outline=(20, 60, 64))
    draw_text(image, (bx0 + 4, by0 + 2), STATE_LABEL.get(severity.value, "RUNNING")[:10], fill=color, size=8)

    windows = list(snapshot.windows[:2])
    if not windows and snapshot.critical_remaining is not None:
        windows = [
            UsageWindow(
                id="left",
                label="LEFT",
                used_percent=100 - snapshot.critical_remaining,
                remaining_percent=snapshot.critical_remaining,
            )
        ]

    if windows:
        hero = windows[0]
        hero_text = _percent_text(hero.remaining_percent)
        hero_color = _meter_color(hero.remaining_percent)
        draw_panel(image, RATE_HERO, fill=PANEL_2, outline=(20, 52, 58))
        draw_text(image, (101, 27), f"{hero.label[:6].upper()} LEFT", fill=MUTED, size=8)
        _right_pixel_number(image, 227, 28, hero_text, INK if hero.remaining_percent > 20 else hero_color)
        draw_thin_bar(image, (101, 58), hero.remaining_percent, hero_color, width=126)

    if len(windows) > 1:
        week = windows[1]
        week_text = _percent_text(week.remaining_percent)
        week_color = _meter_color(week.remaining_percent)
        draw_panel(image, RATE_WEEK, fill=PANEL_2, outline=(20, 52, 58))
        draw_text(image, (101, 74), f"{week.label[:6].upper()} LEFT", fill=MUTED, size=8)
        _right_pixel_number(image, 227, 76, week_text, week_color, scale=4)
        draw_thin_bar(image, (101, 98), week.remaining_percent, week_color, width=126)
    elif windows:
        draw_panel(image, RATE_WEEK, fill=PANEL_2, outline=(20, 52, 58))
        draw_text(image, (101, 80), snapshot.plan.upper()[:12] if snapshot.plan else "QUOTA", fill=MUTED, size=8)

    draw_panel(image, RESET_CARD, fill=PANEL_2, outline=(20, 60, 64))
    draw_text(image, (101, 110), "RESET", fill=MUTED, size=7)
    parts = reset_parts(snapshot)
    reset_value = f"{parts[0]} {parts[1]}" if parts else "-- --:--"
    draw_text(image, (101, 118), reset_value, fill=INK, size=9)
    draw.rectangle((214, 112, 219, 117), fill=color)
    draw.rectangle((222, 112, 227, 117), outline=color)
    return image


def paint_empty() -> Image.Image:
    image = new_canvas()
    draw = ImageDraw.Draw(image)
    draw_panel(image, CHAR_BAY, fill=PANEL, outline=BORDER)
    draw_panel(image, RATE_BAY, fill=PANEL, outline=BORDER)
    draw.rectangle((87, 20, 92, 131), fill=(7, 12, 20))
    draw_corner_brackets(image, CHAR_BAY, color=BORDER)
    draw_corner_brackets(image, RATE_BAY, color=BORDER)
    draw_text(image, (6, 4), "QUOTADECK", fill=(30, 226, 176), size=10)
    draw_text(image, (186, 4), "IDLE", fill=MUTED, size=9)
    draw_text(image, (14, 58), "NO", fill=MUTED, size=12)
    draw_text(image, (14, 76), "CREW", fill=MUTED, size=12)
    draw_panel(image, RATE_HERO, fill=PANEL_2, outline=(20, 52, 58))
    draw_text(image, (101, 32), "NO ACCOUNTS", fill=INK, size=11)
    draw_text(image, (101, 48), "OPEN APP / DETECT", fill=MUTED, size=8)
    draw_panel(image, RATE_WEEK, fill=PANEL_2, outline=(20, 52, 58))
    draw_text(image, (101, 80), "CLI OR APP LOGIN", fill=MUTED, size=8)
    draw_panel(image, RESET_CARD, fill=PANEL_2, outline=(20, 60, 64))
    draw_text(image, (101, 110), "RESET", fill=MUTED, size=7)
    draw_text(image, (101, 118), "-- --:--", fill=INK, size=9)
    return image


def paint_overview(rows: list[tuple[UsageSnapshot, Severity]], accent: str = "#3DDC97") -> Image.Image:
    image = new_canvas()
    draw_text(image, (8, 6), "AI CREW", fill=_hex(accent), size=12)
    y = 28
    for snapshot, severity in rows[:7]:
        color = severity_color(severity.value)
        label = f"{snapshot.provider[:6].upper()} {snapshot.display_name[:8]}"
        remaining = snapshot.critical_remaining
        value = "--%" if remaining is None else f"{int(round(remaining))}%"
        draw_text(image, (8, y), "●", fill=color, size=10)
        draw_text(image, (22, y), label[:16], fill=TEXT, size=10)
        _right_pixel_number(image, 228, y - 2, value, color, scale=2)
        y += 14
    return image


def paint_transition(previous: Image.Image, nxt: Image.Image, kind: str = "scan") -> Image.Image:
    if kind == "dissolve":
        return Image.blend(previous.convert("RGB"), nxt.convert("RGB"), 0.5)
    image = previous.convert("RGB").copy()
    band = nxt.crop((0, 50, LCD_WIDTH, 90))
    image.paste(band, (0, 50))
    return image
