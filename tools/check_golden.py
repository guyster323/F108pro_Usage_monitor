#!/usr/bin/env python3
"""Render a 240x135 cumulative card and confirm coin placement stays in bounds."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.renderer.layout import COST_COIN_BOX, paint_cumulative_account, paint_empty
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import (
    DailyUsage,
    ModelUsage,
    PeriodUsageComparison,
    TokenUsage,
    UsageComparison,
    UsageIntensity,
    UsagePeriod,
    UsageReport,
)


def main() -> int:
    blank = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    empty = paint_empty()
    if empty.size != (LCD_WIDTH, LCD_HEIGHT):
        print("empty card is not 240x135", file=sys.stderr)
        return 1

    end = date(2026, 9, 11)
    usage = TokenUsage(input_tokens=600_000_000)
    model = ModelUsage("codex", "gpt-5.6-sol", usage)
    history = TokenUsage(input_tokens=2_400_000_000)
    snapshot = CumulativeSnapshot(
        provider="codex",
        account_id="one",
        display_name="ONE",
        plan="api",
        report=UsageReport(
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
            period_comparisons=(
                PeriodUsageComparison(
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
                ),
            ),
        ),
        status="ok",
        period=UsagePeriod.MONTHLY,
        this_cost_usd=Decimal(400),
        average_cost_usd=Decimal(800),
    )
    card = paint_cumulative_account(snapshot, blank, "#19D79C")
    if card.size != (LCD_WIDTH, LCD_HEIGHT):
        print("cumulative card is not 240x135", file=sys.stderr)
        return 1
    x0, y0, x1, y1 = COST_COIN_BOX
    if not (0 <= x0 <= x1 < LCD_WIDTH and 0 <= y0 <= y1 < LCD_HEIGHT):
        print("coin box leaves the LCD", file=sys.stderr)
        return 1
    if x1 >= 167:
        print("coin overlaps the THIS/AVG divider", file=sys.stderr)
        return 1
    print("golden layout ok: 240x135 quota and cumulative cards, coin in-bounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
