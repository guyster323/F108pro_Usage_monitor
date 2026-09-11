from __future__ import annotations

from decimal import Decimal

from PIL import Image

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.renderer.icons import COIN_SIZE, load_cost_coin
from quotadeck.renderer.layout import (
    COST_COIN_BOX,
    COST_THIS_LEFT,
    USAGE_TABLE,
    compact_cost_pair,
    paint_cumulative_account,
    pixel_text_width,
)
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import (
    CostCurrency,
    DailyUsage,
    ModelUsage,
    PeriodUsageComparison,
    TokenUsage,
    UsageComparison,
    UsageIntensity,
    UsagePeriod,
    UsageReport,
)
from datetime import date, timedelta


def _snapshot() -> CumulativeSnapshot:
    end = date(2026, 9, 11)
    usage = TokenUsage(input_tokens=600_000_000)
    model = ModelUsage("codex", "gpt-5.6-sol", usage)
    history = TokenUsage(input_tokens=2_400_000_000)
    comparison = PeriodUsageComparison(
        period=UsagePeriod.MONTHLY,
        current_start=end.replace(day=1),
        current_end=end,
        current_usage=usage,
        current_models=(model,),
        history_usage=history,
        history_models=(ModelUsage("codex", "gpt-5.6-sol", history),),
        history_periods=2,
        minimum_history_periods=1,
        ratio=0.5,
        intensity=UsageIntensity.BELOW_AVERAGE,
    )
    report = UsageReport(
        start_day=end - timedelta(days=40),
        end_day=end,
        requested_days=365,
        daily=(DailyUsage(end, usage, (model,)),),
        model_totals=(model,),
        tokens=usage,
        today_comparison=UsageComparison(
            today_tokens=600_000_000,
            prior_daily_average=1_200_000_000,
            ratio=0.5,
            intensity=UsageIntensity.BELOW_AVERAGE,
            history_days=40,
            minimum_history_days=7,
        ),
        period_comparisons=(comparison,),
    )
    return CumulativeSnapshot(
        provider="codex",
        account_id="one",
        display_name="ONE",
        plan="api",
        report=report,
        status="ok",
        period=UsagePeriod.MONTHLY,
        this_cost_usd=Decimal(400),
        average_cost_usd=Decimal(800),
    )


def test_runtime_coin_is_small_transparent_and_letter_free() -> None:
    coin = load_cost_coin()
    assert coin is not None
    assert coin.size == (COIN_SIZE, COIN_SIZE)
    assert coin.mode == "RGBA"
    alpha = [coin.getpixel((x, y))[3] for y in range(coin.height) for x in range(coin.width)]
    assert 0 in alpha
    assert 255 in alpha
    # Corner pixels stay transparent so the LCD panel shows through.
    assert coin.getpixel((0, 0))[3] == 0
    assert coin.getpixel((6, 6))[3] == 0


def test_cost_row_coin_does_not_overlap_text_or_leave_lcd() -> None:
    snapshot = _snapshot()
    image = paint_cumulative_account(
        snapshot,
        Image.new("RGBA", (88, 108), (0, 0, 0, 0)),
        "#19D79C",
        currency=CostCurrency.USD,
    )
    assert image.size == (LCD_WIDTH, LCD_HEIGHT)
    x0, y0, x1, y1 = COST_COIN_BOX
    assert 0 <= x0 <= x1 < LCD_WIDTH
    assert 0 <= y0 <= y1 < LCD_HEIGHT
    table = USAGE_TABLE
    assert table[0] <= x0 and x1 <= table[2]
    assert table[1] <= y0 and y1 <= table[3]
    assert x1 < 167

    this_text, avg_text = compact_cost_pair(
        snapshot.this_cost_usd, snapshot.average_cost_usd
    )
    this_width = pixel_text_width(this_text, 2)
    this_x = COST_THIS_LEFT + max(0, (166 - COST_THIS_LEFT + 1 - this_width) // 2)
    assert this_x > x1

    coin = load_cost_coin()
    assert coin is not None
    for y in range(coin.height):
        for x in range(coin.width):
            if coin.getpixel((x, y))[3] == 0:
                continue
            lcd_x = x0 + x
            lcd_y = y0 + y
            # Opaque coin pixels stay left of the THIS cost glyphs.
            assert lcd_x < this_x
            assert lcd_y >= 116
    assert avg_text == "0.8K"


def test_lcd_cost_row_stays_ascii_without_currency_words() -> None:
    drawn: list[str] = []
    import quotadeck.renderer.layout as layout

    original = layout.draw_pixel_text

    def capture(image, xy, value, **kwargs):
        drawn.append(value)
        return original(image, xy, value, **kwargs)

    layout.draw_pixel_text = capture  # type: ignore[method-assign]
    try:
        paint_cumulative_account(
            _snapshot(),
            Image.new("RGBA", (88, 108), (0, 0, 0, 0)),
            "#19D79C",
            currency=CostCurrency.KRW,
            usd_to_krw_rate=1400,
        )
    finally:
        layout.draw_pixel_text = original  # type: ignore[method-assign]
    assert all(value.isascii() for value in drawn)
    joined = " ".join(drawn)
    assert "KRW" not in joined
    assert "USD" not in joined
    assert "gpt-5.6-sol" not in joined
