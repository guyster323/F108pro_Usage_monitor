from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from math import isfinite

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
    draw_split_usage_bar,
    new_canvas,
    pixel_identifier,
    pixel_text_width,
    severity_color,
    truncate_pixel_middle,
    truncate_pixel_text,
)
from quotadeck.renderer.icons import COIN_SIZE, load_cost_coin
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import CostCurrency, UsageIntensity, UsagePeriod

# Inclusive pixel boxes. A single account always owns the full LCD.
HEADER = (3, 3, 236, 18)
CHAR_BAY = (3, 21, 92, 132)
RATE_BAY = (97, 21, 236, 132)
SPRITE_SLOT = (4, 23, 91, 130)
RATE_TOP = (97, 21, 236, 74)
RATE_BOTTOM = (97, 79, 236, 132)
RATE_SINGLE = RATE_BAY
USAGE_BAR = RATE_TOP
USAGE_TABLE = RATE_BOTTOM
# 7x7 transparent coin at the left start of the THIS cost cell.
COST_COIN_ORIGIN = (99, 118)
COST_COIN_BOX = (
    COST_COIN_ORIGIN[0],
    COST_COIN_ORIGIN[1],
    COST_COIN_ORIGIN[0] + COIN_SIZE - 1,
    COST_COIN_ORIGIN[1] + COIN_SIZE - 1,
)
COST_THIS_LEFT = COST_COIN_ORIGIN[0] + COIN_SIZE + 1
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
    times: list[datetime] = []
    for window in snapshot.windows:
        reset = window.resets_at
        if not isinstance(reset, datetime):
            continue
        try:
            if reset.tzinfo is None or reset.utcoffset() is None:
                reset = reset.replace(tzinfo=timezone.utc)
            else:
                reset = reset.astimezone(timezone.utc)
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        times.append(reset)
    if not times:
        return None
    soonest = min(times)
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
    snapshot: UsageSnapshot | CumulativeSnapshot,
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


def compact_token_count(value: int) -> str:
    """Format a non-negative token count for the narrow LCD data bay."""

    try:
        count = max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        count = 0
    units = (
        (1, ""),
        (1_000, "K"),
        (1_000_000, "M"),
        (1_000_000_000, "B"),
        (1_000_000_000_000, "T"),
    )
    unit_index = 0
    for index, (divisor, _suffix) in enumerate(units):
        if count < divisor:
            break
        unit_index = index

    while True:
        divisor, suffix = units[unit_index]
        if unit_index == 0:
            return str(count)
        if count < divisor * 10:
            quotient, remainder = divmod(count * 10, divisor)
            if remainder * 2 > divisor or (
                remainder * 2 == divisor and quotient % 2
            ):
                quotient += 1
            rendered = f"{quotient // 10}.{quotient % 10}"
            rounded_below_thousand = True
        else:
            quotient, remainder = divmod(count, divisor)
            if remainder * 2 > divisor or (
                remainder * 2 == divisor and quotient % 2
            ):
                quotient += 1
            rendered = str(quotient)
            rounded_below_thousand = quotient < 1000
        if rounded_below_thousand or unit_index == len(units) - 1:
            if unit_index == len(units) - 1 and not rounded_below_thousand:
                return "999T+"
            return f"{rendered}{suffix}"
        unit_index += 1


def cumulative_period_display(snapshot: CumulativeSnapshot) -> str:
    """Render the retained span once (``365D`` or ``SINCE 42D``)."""

    if snapshot.total_period_label == "365D":
        return "365D"
    days = snapshot.observed_days
    return "SINCE" if days is None else f"SINCE {days}D"


def _nonnegative_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(value) if isinstance(value, int) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _round_decimal(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext() as context:
        digits = len(value.as_tuple().digits)
        context.prec = max(28, digits + abs(value.adjusted()) + 8)
        return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _format_shared_value(
    value: Decimal | None,
    *,
    divisor: Decimal,
    suffix: str,
    fine_base_unit: bool = False,
) -> str:
    if value is None:
        return "--"
    if value == 0:
        return "0"
    scaled = value / divisor
    if divisor != 1 and scaled < Decimal("0.05"):
        return f"<0.1{suffix}"
    tiny_base = Decimal("0.005") if fine_base_unit else Decimal("0.05")
    if divisor == 1 and scaled < tiny_base:
        return "<0.01" if fine_base_unit else "<0.1"
    if fine_base_unit and divisor == 1 and scaled < 10:
        shown = _round_decimal(scaled, Decimal("0.01"))
    elif scaled < 10:
        shown = _round_decimal(scaled, Decimal("0.1"))
    else:
        shown = _round_decimal(scaled, Decimal("1"))
    rendered = format(shown, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if not rendered:
        rendered = "0"
    return f"{rendered}{suffix}"


def _shared_unit_pair(
    first: Decimal | None,
    second: Decimal | None,
    units: tuple[tuple[Decimal, str, Decimal], ...],
    *,
    fine_base_unit: bool = False,
    cap: str,
) -> tuple[str, str]:
    present = [value for value in (first, second) if value is not None]
    if not present:
        return "--", "--"
    largest = max(present)
    unit_index = 0
    for index, (_divisor, _suffix, threshold) in enumerate(units):
        if largest >= threshold:
            unit_index = index
    divisor, suffix, _threshold = units[unit_index]
    if unit_index == len(units) - 1 and largest >= divisor * Decimal("999.5"):

        def capped(value: Decimal | None) -> str:
            if value is None:
                return "--"
            if value >= divisor * Decimal("999.5"):
                return cap
            return _format_shared_value(
                value,
                divisor=divisor,
                suffix=suffix,
                fine_base_unit=fine_base_unit,
            )

        return capped(first), capped(second)
    return (
        _format_shared_value(
            first,
            divisor=divisor,
            suffix=suffix,
            fine_base_unit=fine_base_unit,
        ),
        _format_shared_value(
            second,
            divisor=divisor,
            suffix=suffix,
            fine_base_unit=fine_base_unit,
        ),
    )


def compact_token_pair(
    this_tokens: int | None,
    average_tokens: int | None,
) -> tuple[str, str]:
    """Format THIS/AVG token values with one shared, directly comparable unit."""

    first = _nonnegative_decimal(this_tokens)
    second = _nonnegative_decimal(average_tokens)
    units = (
        (Decimal(1), "", Decimal(0)),
        (Decimal(1_000), "K", Decimal(1_000)),
        (Decimal(1_000_000), "M", Decimal(999_500)),
        (Decimal(1_000_000_000), "B", Decimal(999_500_000)),
        (Decimal(1_000_000_000_000), "T", Decimal(999_500_000_000)),
    )
    return _shared_unit_pair(first, second, units, cap="999T+")


def compact_cost_pair(
    this_cost_usd: Decimal | int | float | str | None,
    average_cost_usd: Decimal | int | float | str | None,
    *,
    currency: CostCurrency = CostCurrency.USD,
    usd_to_krw_rate: Decimal | int | float | str = 1400,
) -> tuple[str, str]:
    """Format a list-price pair in the selected currency using one shared unit.

    USD values keep their ordinary dollar scale.  KRW values use 10,000 won as
    the implicit base unit so the LCD can stay ASCII-only: ``1K`` therefore
    means 10,000,000 won, ``1M`` means 10,000,000,000 won, and ``1B`` means
    10,000,000,000,000 won.  The GUI labels that base unit for the user.
    """

    first = _nonnegative_decimal(this_cost_usd)
    second = _nonnegative_decimal(average_cost_usd)
    if first is None or second is None:
        return "--", "--"
    if currency is CostCurrency.KRW:
        rate = _nonnegative_decimal(usd_to_krw_rate)
        if rate is None or rate == 0:
            return "--", "--"
        krw_base = Decimal(10_000)
        first = first * rate / krw_base
        second = second * rate / krw_base
        units = (
            (Decimal(1), "", Decimal(0)),
            (Decimal(1_000), "K", Decimal("999.5")),
            (Decimal(1_000_000), "M", Decimal(999_500)),
            (Decimal(1_000_000_000), "B", Decimal(999_500_000)),
        )
        return _shared_unit_pair(
            first,
            second,
            units,
            fine_base_unit=True,
            cap="999B+",
        )

    # Moving to the next SI unit at one tenth keeps money values to at most
    # four visible glyphs (400/800 USD -> 0.4K/0.8K) on each 67px column.
    units = (
        (Decimal(1), "", Decimal(0)),
        (Decimal(1_000), "K", Decimal(100)),
        (Decimal(1_000_000), "M", Decimal(100_000)),
        (Decimal(1_000_000_000), "B", Decimal(100_000_000)),
        (Decimal(1_000_000_000_000), "T", Decimal(100_000_000_000)),
    )
    return _shared_unit_pair(
        first,
        second,
        units,
        fine_base_unit=True,
        cap="999T+",
    )


def _centered_pixel_x(text: str, left: int, right: int, *, scale: int = 2) -> int:
    return left + max(0, (right - left + 1 - pixel_text_width(text, scale)) // 2)


def _draw_usage_pair(
    image: Image.Image,
    left_value: str,
    right_value: str,
    *,
    y: int,
    left_fill: tuple[int, int, int],
    right_fill: tuple[int, int, int] = TEXT,
    left_bound: int = 99,
) -> None:
    draw_pixel_text(
        image,
        (_centered_pixel_x(left_value, left_bound, 166), y),
        left_value,
        fill=left_fill,
        scale=2,
    )
    draw_pixel_text(
        image,
        (_centered_pixel_x(right_value, 168, 234), y),
        right_value,
        fill=right_fill,
        scale=2,
    )


def _draw_cost_coin(image: Image.Image) -> None:
    coin = load_cost_coin()
    if coin is None:
        return
    image.paste(coin, COST_COIN_ORIGIN, coin)


def _usage_bar_percent(snapshot: CumulativeSnapshot) -> float | None:
    ratio = snapshot.ratio
    if ratio is None:
        return None
    try:
        value = float(ratio) * 100.0
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0 or value != value:
        return None
    return value


def cumulative_bar_caption(
    snapshot: CumulativeSnapshot,
    currency: CostCurrency,
) -> str:
    """Return the exact visible period/status label above the Bar.

    Currency belongs to the GUI setting, not the compact LCD Bar.  Keeping it
    out also makes the available-state caption stable when only the currency
    selection changes.
    """

    del currency  # Retain the call signature for renderer/hash compatibility.

    period = "D" if snapshot.period is UsagePeriod.DAILY else "M"
    if snapshot.status == "partial":
        return f"{period} PARTIAL"
    if not snapshot.available:
        return f"{period} N/A"
    if snapshot.intensity is UsageIntensity.INSUFFICIENT_HISTORY:
        return f"{period} BUILD"
    return f"{period} AVG"


def _paint_cumulative_available(
    image: Image.Image,
    snapshot: CumulativeSnapshot,
    metric_color: tuple[int, int, int],
    *,
    currency: CostCurrency,
    usd_to_krw_rate: Decimal | int | float | str,
) -> None:
    draw_split_usage_bar(
        image,
        USAGE_BAR,
        _usage_bar_percent(snapshot),
        cumulative_bar_caption(snapshot, currency),
        metric_color,
        outline=BORDER,
    )

    draw_panel(image, USAGE_TABLE, fill=PANEL, outline=BORDER)
    ImageDraw.Draw(image).line((167, 81, 167, 130), fill=BORDER)
    _draw_usage_pair(
        image,
        "THIS",
        "AVG",
        y=82,
        left_fill=metric_color,
        right_fill=MUTED,
    )
    token_pair = compact_token_pair(snapshot.this_tokens, snapshot.average_tokens)
    _draw_usage_pair(
        image,
        *token_pair,
        y=99,
        left_fill=metric_color,
    )
    cost_pair = compact_cost_pair(
        snapshot.this_cost_usd,
        snapshot.average_cost_usd,
        currency=currency,
        usd_to_krw_rate=usd_to_krw_rate,
    )
    _draw_cost_coin(image)
    _draw_usage_pair(
        image,
        *cost_pair,
        y=116,
        left_fill=metric_color,
        left_bound=COST_THIS_LEFT,
    )


def _paint_cumulative_unavailable(
    image: Image.Image,
    snapshot: CumulativeSnapshot,
    *,
    currency: CostCurrency,
) -> None:
    draw_split_usage_bar(
        image,
        USAGE_BAR,
        None,
        cumulative_bar_caption(snapshot, currency),
        METER_COLORS.get(snapshot.severity, METER_COLORS[Severity.STALE]),
        outline=BORDER,
    )
    draw_panel(image, USAGE_TABLE, fill=PANEL, outline=BORDER)
    ImageDraw.Draw(image).line((167, 81, 167, 130), fill=BORDER)
    _draw_usage_pair(
        image,
        "THIS",
        "AVG",
        y=82,
        left_fill=MUTED,
        right_fill=MUTED,
    )
    _draw_usage_pair(image, "N/A", "N/A", y=99, left_fill=MUTED, right_fill=MUTED)
    _draw_cost_coin(image)
    _draw_usage_pair(
        image,
        "--",
        "--",
        y=116,
        left_fill=MUTED,
        right_fill=MUTED,
        left_bound=COST_THIS_LEFT,
    )


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


def paint_cumulative_account(
    snapshot: CumulativeSnapshot,
    sprite: Image.Image,
    accent: str,
    *,
    position: tuple[int, int] | None = None,
    currency: CostCurrency = CostCurrency.USD,
    usd_to_krw_rate: Decimal | int | float | str = 1400,
) -> Image.Image:
    """Paint one cumulative-usage account on the complete 240x135 LCD."""

    image = new_canvas()
    draw = ImageDraw.Draw(image)
    accent_rgb = _hex(accent)
    severity = snapshot.severity
    draw.rectangle((0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1), outline=BORDER)
    _draw_header(image, snapshot, severity, accent_rgb, position)

    draw_panel(image, CHAR_BAY, fill=PANEL, outline=BORDER)
    draw_corner_brackets(image, CHAR_BAY, color=accent_rgb, arm=6)
    fitted, origin = _fit_sprite(sprite, SPRITE_SLOT)
    image.paste(fitted, origin, fitted)

    if snapshot.available:
        _paint_cumulative_available(
            image,
            snapshot,
            severity_color(severity.value),
            currency=currency,
            usd_to_krw_rate=usd_to_krw_rate,
        )
    else:
        _paint_cumulative_unavailable(image, snapshot, currency=currency)
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
