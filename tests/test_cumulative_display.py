from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image

from quotadeck.config import CONFIG_VERSION, load_config, save_config
from quotadeck.core.models import DisplayMode, MetricMode, Severity
from quotadeck.renderer.layout import (
    compact_cost_pair,
    compact_token_count,
    compact_token_pair,
    cumulative_period_display,
    paint_cumulative_account,
)
from quotadeck.renderer.canvas import (
    draw_split_usage_bar,
    usage_bar_boundary,
    usage_percent_label,
)
from quotadeck.renderer.scenes import _order_cumulative, render_cumulative_playlist
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import (
    DailyUsage,
    CostCurrency,
    ModelUsage,
    PeriodUsageComparison,
    TokenUsage,
    UsageComparison,
    UsageIntensity,
    UsagePeriod,
    UsageReport,
)


def _snapshot(
    intensity: UsageIntensity,
    ratio: float | None,
    *,
    account: str = "one",
    today_tokens: int = 1_500,
    average_tokens: int | None = None,
    this_cost_usd: Decimal | None = None,
    average_cost_usd: Decimal | None = None,
    period: UsagePeriod = UsagePeriod.DAILY,
    model_name: str = "gpt-5.6-sol",
    status: str = "ok",
) -> CumulativeSnapshot:
    end = date(2026, 9, 11)
    start = end - timedelta(days=9)
    today_usage = TokenUsage(input_tokens=today_tokens)
    model = ModelUsage("codex", model_name, today_usage)
    if average_tokens is None and ratio is not None and ratio > 0:
        average_tokens = int(today_tokens / ratio + 0.5)
    history_periods = 0 if ratio is None else (9 if period is UsagePeriod.DAILY else 2)
    history_usage = TokenUsage(
        input_tokens=(average_tokens or 0) * history_periods
    )
    history_model = ModelUsage("codex", model_name, history_usage)
    period_comparison = PeriodUsageComparison(
        period=period,
        current_start=end if period is UsagePeriod.DAILY else end.replace(day=1),
        current_end=end,
        current_usage=today_usage,
        current_models=(model,),
        history_usage=history_usage,
        history_models=(history_model,),
        history_periods=history_periods,
        minimum_history_periods=7 if period is UsagePeriod.DAILY else 1,
        ratio=ratio,
        intensity=intensity,
    )
    comparison = UsageComparison(
        today_tokens=today_tokens,
        prior_daily_average=(None if ratio is None else today_tokens / max(ratio, 0.01)),
        ratio=ratio,
        intensity=intensity,
        history_days=0 if ratio is None else 9,
        minimum_history_days=7,
    )
    report = UsageReport(
        start_day=start,
        end_day=end,
        requested_days=365,
        daily=(DailyUsage(end, today_usage, (model,)),),
        model_totals=(model,),
        tokens=TokenUsage(input_tokens=today_tokens * 4),
        today_comparison=comparison,
        period_comparisons=(period_comparison,),
    )
    return CumulativeSnapshot(
        provider="codex",
        account_id=account,
        display_name=account.upper(),
        plan="api",
        report=report,
        status=status,  # type: ignore[arg-type]
        source_label="THIS DEVICE",
        period=period,
        this_cost_usd=this_cost_usd,
        average_cost_usd=average_cost_usd,
    )


def test_metric_mode_is_independent_of_account_order_mode() -> None:
    assert {item.value for item in MetricMode} == {"quota", "cumulative"}
    assert {item.value for item in DisplayMode} == {"fixed", "smart"}
    assert MetricMode.CUMULATIVE.value not in {item.value for item in DisplayMode}


def test_legacy_config_migrates_to_quota_and_v5(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps({"config_version": 3, "display_mode": "fixed"}),
        encoding="utf-8",
    )
    config = load_config(source)
    assert config.display_mode is DisplayMode.FIXED
    assert config.metric_mode is MetricMode.QUOTA
    assert config.cumulative_period is UsagePeriod.MONTHLY
    assert config.cost_currency is CostCurrency.KRW
    assert config.usd_to_krw_rate == 1400

    save_config(config, source)
    saved = json.loads(source.read_text(encoding="utf-8"))
    assert saved["config_version"] == CONFIG_VERSION == 5
    assert saved["metric_mode"] == "quota"
    assert saved["cumulative_period"] == "monthly"
    assert saved["cost_currency"] == "krw"
    assert saved["usd_to_krw_rate"] == 1400


def test_invalid_saved_metric_does_not_prevent_startup(tmp_path: Path) -> None:
    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps({"display_mode": "bogus", "metric_mode": "bogus"}),
        encoding="utf-8",
    )
    config = load_config(source)
    assert config.display_mode is DisplayMode.SMART
    assert config.metric_mode is MetricMode.QUOTA


def test_compact_tokens_roll_over_at_visible_rounding_boundary() -> None:
    assert compact_token_count(0) == "0"
    assert compact_token_count(999) == "999"
    assert compact_token_count(1_000) == "1.0K"
    assert compact_token_count(999_500) == "1.0M"
    assert compact_token_count(1_240_000) == "1.2M"
    assert compact_token_count(10**400) == "999T+"


def test_comparison_tokens_share_one_visible_unit() -> None:
    assert compact_token_pair(600_000_000, 1_200_000_000) == ("0.6B", "1.2B")
    assert compact_token_pair(400, 800) == ("400", "800")
    assert compact_token_pair(999_500, 999_500) == ("1M", "1M")
    assert compact_token_pair(None, 1_200_000_000) == ("--", "1.2B")


def test_comparison_costs_share_currency_specific_units() -> None:
    assert compact_cost_pair(Decimal(400), Decimal(800)) == ("0.4K", "0.8K")
    assert compact_cost_pair(Decimal(40), Decimal(80)) == ("40", "80")
    assert compact_cost_pair(
        Decimal(1),
        Decimal(2),
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    ) == ("0.1", "0.2")
    assert compact_cost_pair(
        Decimal(10_000),
        Decimal(20_000),
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    ) == ("1K", "2K")
    assert compact_cost_pair(
        Decimal(10_000_000),
        Decimal(20_000_000),
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    ) == ("1M", "2M")
    assert compact_cost_pair(
        Decimal(10_000_000_000),
        Decimal(20_000_000_000),
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    ) == ("1B", "2B")
    assert compact_cost_pair(None, None) == ("--", "--")


def test_usage_bar_keeps_over_average_label_while_fill_saturates() -> None:
    assert usage_percent_label(99.9) == "99%"
    assert usage_percent_label(100) == "100%"
    assert usage_percent_label(150) == "150%"
    assert usage_percent_label(200) == "200%"
    assert usage_percent_label(300) == "300%+"
    assert usage_percent_label(float("inf")) == "300%+"

    image = Image.new("RGB", (240, 135), "black")
    half = draw_split_usage_bar(
        image,
        (97, 21, 236, 74),
        50,
        "M AVG",
        (0, 200, 100),
    )
    full = draw_split_usage_bar(
        image,
        (97, 21, 236, 74),
        150,
        "M AVG",
        (0, 200, 100),
    )
    assert half == 167
    assert full == 235


def test_usage_bar_boundary_uses_the_same_pixel_rounding_as_the_renderer() -> None:
    box = (97, 21, 236, 74)
    assert usage_bar_boundary(box, 50.0) == 167
    assert usage_bar_boundary(box, 50.5) == 168
    assert usage_bar_boundary(box, 300.0) == 235


def test_cumulative_period_does_not_repeat_365d() -> None:
    from dataclasses import replace

    partial_year = _snapshot(UsageIntensity.SIMILAR, 1.0)
    assert cumulative_period_display(partial_year) == "SINCE 10D"
    assert partial_year.report is not None
    full_year = replace(
        partial_year,
        report=replace(
            partial_year.report,
            start_day=partial_year.report.end_day - timedelta(days=364),
        ),
    )
    assert cumulative_period_display(full_year) == "365D"


def test_five_usage_intensities_select_five_reaction_states() -> None:
    cases = (
        (UsageIntensity.BELOW_AVERAGE, 0.8, "usage_below", Severity.HEALTHY),
        (UsageIntensity.SIMILAR, 1.0, "usage_similar", Severity.BUSY),
        (UsageIntensity.ABOVE_1_5X, 1.5, "usage_150", Severity.CAUTION),
        (UsageIntensity.ABOVE_2X, 2.0, "usage_200", Severity.CRITICAL),
        (UsageIntensity.COLLAPSED_3X, 3.0, "usage_300", Severity.EXHAUSTED),
    )
    for intensity, ratio, state, severity in cases:
        snapshot = _snapshot(intensity, ratio)
        assert snapshot.sprite_state == state
        assert snapshot.severity is severity


def test_cumulative_smart_order_is_highest_multiple_first() -> None:
    low = _snapshot(UsageIntensity.BELOW_AVERAGE, 0.5, account="low")
    similar = _snapshot(UsageIntensity.SIMILAR, 1.1, account="similar")
    collapsed = _snapshot(UsageIntensity.COLLAPSED_3X, 3.2, account="collapsed")
    assert [item.account_id for item in _order_cumulative(
        [low, similar, collapsed], DisplayMode.SMART
    )] == ["collapsed", "similar", "low"]
    assert _order_cumulative([low, similar], DisplayMode.FIXED) == [low, similar]


def test_cumulative_render_hash_matches_smart_order_and_frame_budget() -> None:
    from quotadeck.core.scheduler import cumulative_render_hash

    low = _snapshot(UsageIntensity.BELOW_AVERAGE, 0.5, account="low")
    high = _snapshot(UsageIntensity.ABOVE_2X, 2.2, account="high")
    options = {
        "theme": "crew",
        "mode": DisplayMode.SMART.value,
        "hold_seconds": 5,
        "frame_budget": 8,
    }
    assert cumulative_render_hash([low, high], **options) == cumulative_render_hash(
        [high, low], **options
    )
    assert cumulative_render_hash([low, high], **options) != cumulative_render_hash(
        [low, high], **{**options, "frame_budget": 10}
    )


def test_cumulative_hash_tracks_a_visible_one_pixel_bar_change() -> None:
    from quotadeck.core.scheduler import cumulative_render_hash

    half = _snapshot(
        UsageIntensity.BELOW_AVERAGE,
        0.5,
        today_tokens=600_000_000,
        average_tokens=1_200_000_000,
    )
    half_plus = _snapshot(
        UsageIntensity.BELOW_AVERAGE,
        0.505,
        today_tokens=606_000_000,
        average_tokens=1_200_000_000,
    )
    assert compact_token_pair(half.this_tokens, half.average_tokens) == (
        "0.6B",
        "1.2B",
    )
    assert compact_token_pair(half_plus.this_tokens, half_plus.average_tokens) == (
        "0.6B",
        "1.2B",
    )
    assert usage_percent_label(half.ratio * 100) == "50%"
    assert usage_percent_label(half_plus.ratio * 100) == "50%"
    options = {"theme": "crew", "mode": "fixed"}
    assert cumulative_render_hash([half], **options) != cumulative_render_hash(
        [half_plus], **options
    )


def test_cumulative_hash_ignores_model_span_and_source_removed_from_lcd() -> None:
    from dataclasses import replace

    from quotadeck.core.scheduler import cumulative_render_hash

    short = _snapshot(
        UsageIntensity.SIMILAR,
        1.1,
        this_cost_usd=Decimal("1"),
        average_cost_usd=Decimal("2"),
    )
    assert short.report is not None and short.period_comparison is not None
    renamed_current = ModelUsage(
        "codex", "renamed-model", short.period_comparison.current_usage
    )
    renamed_history = ModelUsage(
        "codex", "renamed-model", short.period_comparison.history_usage
    )
    renamed_comparison = replace(
        short.period_comparison,
        current_models=(renamed_current,),
        history_models=(renamed_history,),
    )
    hidden_values_changed = replace(
        short,
        report=replace(
            short.report,
            start_day=short.report.start_day - timedelta(days=30),
            model_totals=(renamed_current,),
            period_comparisons=(renamed_comparison,),
        ),
        source_label="A DIFFERENT SOURCE",
    )
    options = {"theme": "crew", "mode": "fixed"}
    assert cumulative_render_hash([short], **options) == cumulative_render_hash(
        [hidden_values_changed], **options
    )


def test_cumulative_hash_includes_visible_period_cost_currency_and_rate() -> None:
    from dataclasses import replace

    from quotadeck.core.scheduler import cumulative_render_hash

    daily = _snapshot(
        UsageIntensity.SIMILAR,
        1.1,
        this_cost_usd=Decimal("1"),
        average_cost_usd=Decimal("2"),
    )
    monthly = _snapshot(
        UsageIntensity.SIMILAR,
        1.1,
        this_cost_usd=Decimal("1"),
        average_cost_usd=Decimal("2"),
        period=UsagePeriod.MONTHLY,
    )
    changed_cost = replace(
        daily,
        this_cost_usd=Decimal("4"),
        average_cost_usd=Decimal("8"),
    )
    options = {"theme": "crew", "mode": "fixed"}
    base = cumulative_render_hash([daily], **options)
    assert base != cumulative_render_hash([monthly], **options)
    assert base != cumulative_render_hash([changed_cost], **options)
    assert base != cumulative_render_hash(
        [daily], **options, currency=CostCurrency.KRW
    )
    assert cumulative_render_hash(
        [daily],
        **options,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1300,
    ) != cumulative_render_hash(
        [daily],
        **options,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400,
    )
    # The manual KRW rate is invisible while USD is selected, so changing it
    # must not trigger another LCD flash write.
    assert cumulative_render_hash(
        [daily],
        **options,
        currency=CostCurrency.USD,
        usd_to_krw_rate=1300,
    ) == cumulative_render_hash(
        [daily],
        **options,
        currency=CostCurrency.USD,
        usd_to_krw_rate=1400,
    )


def test_cumulative_hash_ignores_hidden_status_currency_and_rate() -> None:
    from dataclasses import replace

    from quotadeck.core.scheduler import cumulative_render_hash

    unsupported = CumulativeSnapshot.unsupported(
        provider="cursor",
        account_id="one",
        display_name="ONE",
        period=UsagePeriod.MONTHLY,
    )
    unavailable = replace(unsupported, status="unavailable")
    options = {"theme": "crew", "mode": "fixed"}
    assert cumulative_render_hash(
        [unsupported],
        **options,
        currency=CostCurrency.USD,
        usd_to_krw_rate=1300,
    ) == cumulative_render_hash(
        [unavailable],
        **options,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400,
    )

    # Error uses a different visible header icon, so that state remains part
    # of the digest even though all numeric cells are unavailable.
    assert cumulative_render_hash(
        [unavailable], **options
    ) != cumulative_render_hash(
        [replace(unavailable, status="error")], **options
    )


def test_cumulative_renderer_asks_theme_for_each_reaction(monkeypatch) -> None:
    import quotadeck.renderer.scenes as scenes

    requested: list[str] = []

    class FakeTheme:
        root = Path("unused")

        @staticmethod
        def state_images(_provider: str, state: str) -> list[Image.Image]:
            requested.append(state)
            return [Image.new("RGBA", (88, 108), (0, 0, 0, 0))]

        @staticmethod
        def accent(_provider: str) -> str:
            return "#19D79C"

    monkeypatch.setattr(scenes, "load_theme", lambda _path: FakeTheme())
    snapshots = [
        _snapshot(UsageIntensity.BELOW_AVERAGE, 0.8, account="below"),
        _snapshot(UsageIntensity.SIMILAR, 1.0, account="similar"),
        _snapshot(UsageIntensity.ABOVE_1_5X, 1.5, account="one-five"),
        _snapshot(UsageIntensity.ABOVE_2X, 2.0, account="two"),
        _snapshot(UsageIntensity.COLLAPSED_3X, 3.0, account="three"),
    ]
    frames = render_cumulative_playlist(
        snapshots,
        Path("unused"),
        mode=DisplayMode.FIXED,
        frame_budget=10,
        hold_ms=2_000,
    )
    assert requested == [
        "usage_below",
        "usage_similar",
        "usage_150",
        "usage_200",
        "usage_300",
    ]
    assert len(frames) == 10
    assert all(frame.image.size == (240, 135) for frame in frames)


def test_lcd_uses_large_this_avg_rows_and_never_draws_model_metadata(monkeypatch) -> None:
    import quotadeck.renderer.layout as layout

    drawn: list[tuple[str, int]] = []
    bars: list[tuple[float | None, str]] = []
    original = layout.draw_pixel_text
    original_bar = layout.draw_split_usage_bar

    def capture(image, xy, value, **kwargs):
        drawn.append((value, int(kwargs.get("scale", 1))))
        return original(image, xy, value, **kwargs)

    def capture_bar(image, box, percent, label, color, **kwargs):
        bars.append((percent, label))
        return original_bar(image, box, percent, label, color, **kwargs)

    monkeypatch.setattr(layout, "draw_pixel_text", capture)
    monkeypatch.setattr(layout, "draw_split_usage_bar", capture_bar)
    blank = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    paint_cumulative_account(
        _snapshot(
            UsageIntensity.BELOW_AVERAGE,
            0.5,
            today_tokens=600_000_000,
            average_tokens=1_200_000_000,
            this_cost_usd=Decimal(400),
            average_cost_usd=Decimal(800),
            model_name="SECRET-MODEL-NAME",
        ),
        blank,
        "#19D79C",
    )
    assert bars == [(50.0, "D AVG")]
    for expected in ("THIS", "AVG", "0.6B", "1.2B", "0.4K", "0.8K"):
        assert (expected, 2) in drawn
    lcd_text = {value for value, _scale in drawn}
    assert "SECRET-MODEL-NAME" not in lcd_text
    assert not ({"TOP", "TOTAL", "SPAN", "THIS DEVICE"} & lcd_text)


def test_krw_lcd_uses_ascii_ten_thousand_won_base(monkeypatch) -> None:
    import quotadeck.renderer.layout as layout

    drawn: list[str] = []
    original = layout.draw_pixel_text

    def capture(image, xy, value, **kwargs):
        drawn.append(value)
        return original(image, xy, value, **kwargs)

    monkeypatch.setattr(layout, "draw_pixel_text", capture)
    paint_cumulative_account(
        _snapshot(
            UsageIntensity.SIMILAR,
            1.0,
            this_cost_usd=Decimal(1),
            average_cost_usd=Decimal(2),
        ),
        Image.new("RGBA", (88, 108), (0, 0, 0, 0)),
        "#4AB6F7",
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    )
    assert "0.1" in drawn
    assert "0.2" in drawn
    assert all(value.isascii() for value in drawn)


def test_unavailable_card_is_simple_and_does_not_draw_source_metadata(monkeypatch) -> None:
    import quotadeck.renderer.layout as layout

    drawn: list[str] = []
    original = layout.draw_pixel_text

    def capture(image, xy, value, **kwargs):
        drawn.append(value)
        return original(image, xy, value, **kwargs)

    monkeypatch.setattr(layout, "draw_pixel_text", capture)
    snapshot = CumulativeSnapshot.unsupported(
        provider="grok",
        account_id="one",
        display_name="ONE",
        source_label="OTEL REQUIRED",
    )
    paint_cumulative_account(
        snapshot,
        Image.new("RGBA", (88, 108), (0, 0, 0, 0)),
        "#AD82EF",
    )
    assert "THIS" in drawn
    assert "AVG" in drawn
    assert "N/A" in drawn
    assert "OTEL REQUIRED" not in drawn
    assert "ADMIN API" not in drawn


def test_cli_exposes_cumulative_models_and_model_price_filters() -> None:
    from quotadeck.cli import build_parser

    parser = build_parser()
    usage = parser.parse_args(["usage", "--cumulative", "--models"])
    assert usage.cumulative is True
    assert usage.models is True
    prices = parser.parse_args(
        ["prices", "--provider", "openai", "--model", "gpt-5.6-sol"]
    )
    assert prices.provider == "openai"
    assert prices.model == "gpt-5.6-sol"


def test_cli_price_filter_prints_only_the_requested_model(capsys) -> None:
    from quotadeck.cli import main

    assert main(
        ["prices", "--provider", "openai", "--model", "gpt-5.6-sol"]
    ) == 0
    output = capsys.readouterr().out
    assert "gpt-5.6-sol" in output
    assert "gpt-5.6-terra" not in output
    assert "LIST only" in output


def test_cli_models_requires_cumulative_mode(capsys) -> None:
    from quotadeck.cli import main

    assert main(["usage", "--models"]) == 2
    assert "requires --cumulative" in capsys.readouterr().err
