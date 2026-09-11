#!/usr/bin/env python3
"""Export deterministic demo previews for cumulative-usage mode."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.config import default_theme_dir
from quotadeck.core.models import DisplayMode
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.layout import paint_cumulative_account
from quotadeck.renderer.scenes import render_cumulative_playlist
from quotadeck.renderer.sprites import load_theme
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

PNG_TARGET = ROOT / "docs" / "cumulative-states.png"
GIF_TARGET = ROOT / "docs" / "cumulative-preview.gif"
COIN_PREVIEW = ROOT / "docs" / "cumulative-coin-preview.png"


def _tokens(total: int) -> TokenUsage:
    output = total * 3 // 10
    return TokenUsage(input_tokens=total - output, output_tokens=output)


def _snapshot(
    provider: str,
    label: str,
    *,
    ratio: float,
    intensity: UsageIntensity,
    model: str,
    average_tokens: int = 1_200_000_000,
    average_cost_krw: int = 20_000,
) -> CumulativeSnapshot:
    today = date(2026, 9, 11)
    today_count = round(average_tokens * ratio)
    total_count = average_tokens * 2 + today_count
    today_model = ModelUsage(provider, model, _tokens(today_count))
    total_model = ModelUsage(provider, model, _tokens(total_count))
    history_usage = _tokens(average_tokens * 2)
    history_model = ModelUsage(provider, model, history_usage)
    period_comparison = PeriodUsageComparison(
        period=UsagePeriod.MONTHLY,
        current_start=today.replace(day=1),
        current_end=today,
        current_usage=_tokens(today_count),
        current_models=(today_model,),
        history_usage=history_usage,
        history_models=(history_model,),
        history_periods=2,
        minimum_history_periods=1,
        ratio=ratio,
        intensity=intensity,
    )
    report = UsageReport(
        start_day=today - timedelta(days=29),
        end_day=today,
        requested_days=365,
        daily=(DailyUsage(today, _tokens(today_count), (today_model,)),),
        model_totals=(total_model,),
        tokens=_tokens(total_count),
        today_comparison=UsageComparison(
            today_tokens=today_count,
            prior_daily_average=100_000.0,
            ratio=ratio,
            intensity=intensity,
            history_days=29,
            minimum_history_days=7,
        ),
        period_comparisons=(period_comparison,),
    )
    krw_rate = Decimal(1400)
    return CumulativeSnapshot(
        provider=provider,
        account_id=f"demo-{provider}-{label}",
        display_name=label,
        plan="api",
        report=report,
        status="ok",
        source_label="DEMO DATA",
        period=UsagePeriod.MONTHLY,
        this_cost_usd=Decimal(str(average_cost_krw * ratio)) / krw_rate,
        average_cost_usd=Decimal(average_cost_krw) / krw_rate,
    )


def demo_states() -> list[CumulativeSnapshot]:
    return [
        _snapshot(
            "codex",
            "BELOW",
            ratio=0.5,
            intensity=UsageIntensity.BELOW_AVERAGE,
            model="gpt-5.6-sol",
        ),
        _snapshot(
            "codex",
            "SIMILAR",
            ratio=1.1,
            intensity=UsageIntensity.SIMILAR,
            model="gpt-5.6-sol",
        ),
        _snapshot(
            "codex",
            "ONE-FIVE",
            ratio=1.6,
            intensity=UsageIntensity.ABOVE_1_5X,
            model="gpt-5.6-sol",
        ),
        _snapshot(
            "codex",
            "DOUBLE",
            ratio=2.2,
            intensity=UsageIntensity.ABOVE_2X,
            model="gpt-5.6-sol",
        ),
        _snapshot(
            "codex",
            "TRIPLE",
            ratio=3.4,
            intensity=UsageIntensity.COLLAPSED_3X,
            model="gpt-5.6-sol",
        ),
    ]


def write_state_sheet() -> None:
    theme = load_theme(default_theme_dir())
    states = demo_states()
    cards: list[Image.Image] = []
    for index, snapshot in enumerate(states, start=1):
        sprite = theme.state_images(snapshot.provider, snapshot.sprite_state)[0]
        cards.append(
            paint_cumulative_account(
                snapshot,
                sprite,
                theme.accent(snapshot.provider),
                position=(index, len(states)),
                currency=CostCurrency.KRW,
                usd_to_krw_rate=1400,
            )
        )

    gap = 8
    label_height = 22
    columns = 3
    rows = 2
    width = columns * 240 + (columns + 1) * gap
    height = rows * (135 + label_height) + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), (8, 16, 25))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    labels = ("< 1.0x", "1.0-1.5x", "1.5-2.0x", "2.0-3.0x", ">= 3.0x")
    for index, (card, label) in enumerate(zip(cards, labels, strict=True)):
        row, column = divmod(index, columns)
        x = gap + column * (240 + gap)
        y = gap + row * (135 + label_height + gap)
        draw.text((x + 2, y + 3), label, fill=(247, 251, 247), font=font)
        sheet.paste(card.convert("RGB"), (x, y + label_height))
    draw.text(
        (gap + 2 * (240 + gap) + 2, gap + 135 + label_height + gap + 5),
        "DEMO DATA - reaction thresholds",
        fill=(142, 168, 168),
        font=font,
    )
    PNG_TARGET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(PNG_TARGET, format="PNG", optimize=True)


def write_coin_preview() -> None:
    """Write one 240x135 cumulative card used to inspect coin placement."""

    theme = load_theme(default_theme_dir())
    snapshot = demo_states()[0]
    sprite = theme.state_images(snapshot.provider, snapshot.sprite_state)[0]
    card = paint_cumulative_account(
        snapshot,
        sprite,
        theme.accent(snapshot.provider),
        position=(1, 1),
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400,
    )
    assert card.size == (240, 135)
    COIN_PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    card.save(COIN_PREVIEW, format="PNG", optimize=True)


def write_provider_gif() -> None:
    theme = load_theme(default_theme_dir())
    snapshots = [
        _snapshot(
            "codex",
            "CODEX DEMO",
            ratio=0.8,
            intensity=UsageIntensity.BELOW_AVERAGE,
            model="gpt-5.6-sol",
        ),
        _snapshot(
            "claude",
            "CLAUDE DEMO",
            ratio=1.6,
            intensity=UsageIntensity.ABOVE_1_5X,
            model="claude-sonnet-5",
        ),
        _snapshot(
            "cursor",
            "CURSOR DEMO",
            ratio=2.2,
            intensity=UsageIntensity.ABOVE_2X,
            model="grok-4.6",
        ),
        _snapshot(
            "grok",
            "GROK DEMO",
            ratio=3.2,
            intensity=UsageIntensity.COLLAPSED_3X,
            model="grok-4.6",
        ),
    ]
    frames = render_cumulative_playlist(
        snapshots,
        theme,
        mode=DisplayMode.FIXED,
        frame_budget=32,
        hold_ms=2000,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400,
    )
    write_gif(frames, GIF_TARGET)


def main() -> int:
    write_state_sheet()
    write_provider_gif()
    write_coin_preview()
    print(f"wrote {PNG_TARGET}")
    print(f"wrote {GIF_TARGET}")
    print(f"wrote {COIN_PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
