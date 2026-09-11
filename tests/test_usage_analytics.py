from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import floor
from zoneinfo import ZoneInfo

import pytest

from quotadeck.usage.analytics import (
    build_usage_report,
    classify_usage_ratio,
    compare_today_to_average,
)
from quotadeck.usage.models import (
    TokenUsage,
    UsageDataset,
    UsageIntensity,
    UsageObservation,
    UsagePeriod,
)


def _observation(when: datetime, tokens: int, event: str, model: str = "model") -> UsageObservation:
    return UsageObservation(
        provider="codex",
        model=model,
        observed_at=when,
        tokens=TokenUsage(input_tokens=tokens),
        session_id="session",
        event_id=event,
    )


def test_token_usage_keeps_large_integer_exact_and_rejects_bool() -> None:
    very_large = 10**400
    usage = TokenUsage(input_tokens=very_large, output_tokens=True)
    assert usage.input_tokens == very_large
    assert usage.output_tokens == 0
    assert usage.total_tokens == very_large

    hostile_text = TokenUsage(input_tokens="9" * 5_000)
    assert hostile_text.input_tokens == 0
    assert hostile_text.total_tokens == 0


def test_usage_model_labels_strip_terminal_bidi_and_rich_text_controls() -> None:
    observation = _observation(
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        1,
        "safe-event",
        model="\x1b]0;spoof\x07<b>gpt</b>\u202e",
    )

    assert "\x1b" not in observation.model
    assert "\x07" not in observation.model
    assert "\u202e" not in observation.model
    assert "<" not in observation.model
    assert ">" not in observation.model
    assert "gpt" in observation.model
    assert TokenUsage(input_tokens=1e308).input_tokens == 0


def test_dataset_merge_deduplicates_exact_event_and_rejects_conflict() -> None:
    at = datetime(2026, 9, 11, tzinfo=timezone.utc)
    first = _observation(at, 10, "same")
    copied = UsageObservation(
        provider=first.provider,
        model=first.model,
        observed_at=first.observed_at,
        tokens=first.tokens,
        session_id="copied-container",
        event_id=first.event_id,
    )
    merged = UsageDataset.merge(
        UsageDataset((first,), ()), UsageDataset((copied,), ())
    )
    assert merged.observations == (first,)

    conflicting = _observation(at, 11, "same")
    with pytest.raises(ValueError, match="conflicting usage observation"):
        UsageDataset.merge(
            UsageDataset((first,), ()), UsageDataset((conflicting,), ())
        )


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.0, UsageIntensity.BELOW_AVERAGE),
        (0.999, UsageIntensity.BELOW_AVERAGE),
        (1.0, UsageIntensity.SIMILAR),
        (1.499, UsageIntensity.SIMILAR),
        (1.5, UsageIntensity.ABOVE_1_5X),
        (2.0, UsageIntensity.ABOVE_2X),
        (3.0, UsageIntensity.COLLAPSED_3X),
        (float("inf"), UsageIntensity.COLLAPSED_3X),
    ],
)
def test_intensity_boundaries(ratio: float, expected: UsageIntensity) -> None:
    assert classify_usage_ratio(ratio) is expected


def test_average_uses_completed_calendar_days_and_missing_days_as_zero() -> None:
    today = date(2026, 9, 11)
    totals = {
        date(2026, 9, 4): 70,
        date(2026, 9, 10): 70,
        today: 30,
    }
    comparison = compare_today_to_average(
        totals, today=today, history_start=date(2026, 9, 4)
    )
    assert comparison.history_days == 7
    assert comparison.prior_daily_average == 20
    assert comparison.ratio == 1.5
    assert comparison.intensity is UsageIntensity.ABOVE_1_5X


def test_average_requires_seven_retained_history_days() -> None:
    comparison = compare_today_to_average(
        {date(2026, 9, 10): 10, date(2026, 9, 11): 100},
        today=date(2026, 9, 11),
    )
    assert comparison.ratio is None
    assert comparison.intensity is UsageIntensity.INSUFFICIENT_HISTORY


def test_equal_zero_usage_is_zero_percent_with_neutral_reaction() -> None:
    today = date(2026, 9, 11)
    comparison = compare_today_to_average(
        {today - timedelta(days=7): 0, today: 0},
        today=today,
        history_start=today - timedelta(days=7),
    )
    assert comparison.ratio == 0
    assert comparison.intensity is UsageIntensity.SIMILAR


def test_average_and_ratio_tolerate_arbitrarily_large_integer_counts() -> None:
    today = date(2026, 9, 11)
    huge = 10**400
    comparison = compare_today_to_average(
        {date(2026, 9, 4): huge, today: huge},
        today=today,
        history_start=date(2026, 9, 4),
    )
    assert comparison.prior_daily_average == float("inf")
    assert comparison.ratio == 7
    assert comparison.intensity is UsageIntensity.COLLAPSED_3X

    balanced = compare_today_to_average(
        {date(2026, 9, 4): huge * 7, today: huge},
        today=today,
        history_start=date(2026, 9, 4),
    )
    assert balanced.ratio == 1
    assert balanced.intensity is UsageIntensity.SIMILAR


def test_display_ratio_never_rounds_up_across_an_exact_threshold() -> None:
    today = date(2026, 9, 11)
    unit = 10**16
    comparison = compare_today_to_average(
        {date(2026, 9, 4): 14 * unit, today: 3 * unit - 1},
        today=today,
        history_start=date(2026, 9, 4),
    )
    assert comparison.intensity is UsageIntensity.SIMILAR
    assert comparison.ratio is not None
    assert comparison.ratio < 1.5
    assert floor(comparison.ratio * 10) / 10 == 1.4


def test_daily_grouping_uses_named_timezone_across_dst_start() -> None:
    observations = (
        _observation(datetime(2026, 3, 8, 4, 30, tzinfo=timezone.utc), 10, "a"),
        _observation(datetime(2026, 3, 8, 5, 30, tzinfo=timezone.utc), 20, "b"),
        _observation(datetime(2026, 3, 9, 3, 30, tzinfo=timezone.utc), 30, "c"),
    )
    report = build_usage_report(
        observations,
        today=date(2026, 3, 8),
        timezone_name="America/New_York",
        lookback_days=2,
        minimum_history_days=1,
    )
    assert [(item.day, item.tokens.total_tokens) for item in report.daily] == [
        (date(2026, 3, 7), 10),
        (date(2026, 3, 8), 50),
    ]
    assert report.today_comparison.ratio == 5


def test_timezone_object_is_supported() -> None:
    observation = _observation(
        datetime(2026, 9, 11, 1, tzinfo=timezone.utc), 10, "event"
    )
    report = build_usage_report(
        (observation,),
        today=date(2026, 9, 10),
        timezone_name=ZoneInfo("America/Los_Angeles"),
    )
    assert report.today.tokens.total_tokens == 10


def test_report_exposes_exact_365_day_coverage() -> None:
    observations = (
        _observation(
            datetime(2025, 9, 12, 12, tzinfo=timezone.utc), 10, "old"
        ),
        _observation(
            datetime(2026, 9, 11, 12, tzinfo=timezone.utc), 20, "today"
        ),
    )
    report = UsageDataset(observations, ()).report(
        today=date(2026, 9, 11), timezone_name="UTC", lookback_days=365
    )
    assert report.start_day == date(2025, 9, 12)
    assert report.observed_days == 365
    assert report.has_full_requested_window
    assert report.has_365_day_history
    assert report.tokens.total_tokens == 30


def test_per_model_totals_are_sorted_by_token_count() -> None:
    observations = (
        _observation(datetime(2026, 9, 11, tzinfo=timezone.utc), 10, "a", "small"),
        _observation(datetime(2026, 9, 11, 1, tzinfo=timezone.utc), 30, "b", "large"),
    )
    report = build_usage_report(
        observations, today=date(2026, 9, 11), timezone_name="UTC"
    )
    assert [item.model for item in report.model_totals] == ["large", "small"]
    assert [item.model for item in report.today.models] == ["large", "small"]


def test_monthly_comparison_uses_mtd_and_only_complete_calendar_months() -> None:
    observations = (
        _observation(datetime(2026, 5, 15, tzinfo=timezone.utc), 999, "partial"),
        _observation(
            datetime(2026, 6, 5, tzinfo=timezone.utc),
            100,
            "june",
            "history-small",
        ),
        _observation(
            datetime(2026, 7, 5, tzinfo=timezone.utc),
            200,
            "july",
            "history-large",
        ),
        # August is a completely observed zero-use month and stays in the divisor.
        _observation(
            datetime(2026, 9, 5, tzinfo=timezone.utc),
            150,
            "september",
            "current",
        ),
    )
    report = build_usage_report(
        observations,
        today=date(2026, 9, 11),
        timezone_name="UTC",
    )
    comparison = report.comparison(UsagePeriod.MONTHLY)
    assert comparison is not None
    assert comparison.current_start == date(2026, 9, 1)
    assert comparison.current_end == date(2026, 9, 11)
    assert comparison.current_tokens == 150
    assert comparison.history_periods == 3
    assert comparison.history_total_tokens == 300
    assert comparison.average_tokens == 100
    assert comparison.ratio == 1.5
    assert comparison.intensity is UsageIntensity.ABOVE_1_5X
    assert [item.model for item in comparison.current_models] == ["current"]
    assert [item.model for item in comparison.history_models] == [
        "history-large",
        "history-small",
    ]


def test_monthly_comparison_includes_first_month_only_from_day_one() -> None:
    report = build_usage_report(
        (
            _observation(datetime(2026, 7, 1, tzinfo=timezone.utc), 40, "july"),
            _observation(datetime(2026, 9, 2, tzinfo=timezone.utc), 20, "now"),
        ),
        today=date(2026, 9, 11),
        timezone_name="UTC",
    )
    comparison = report.comparison("monthly")
    assert comparison is not None
    assert comparison.history_periods == 2
    assert comparison.average_tokens == 20
    assert comparison.ratio == 1


def test_monthly_new_install_exposes_subtotal_but_not_mtd_comparison() -> None:
    report = build_usage_report(
        (_observation(datetime(2026, 9, 5, tzinfo=timezone.utc), 20, "now"),),
        today=date(2026, 9, 11),
        timezone_name="UTC",
    )
    comparison = report.comparison(UsagePeriod.MONTHLY)
    assert comparison is not None
    assert comparison.current_tokens == 20
    assert not comparison.current_complete
    assert comparison.average_tokens is None
    assert comparison.ratio is None
    assert comparison.intensity is UsageIntensity.INSUFFICIENT_HISTORY


def test_month_bucket_uses_selected_timezone() -> None:
    report = build_usage_report(
        (
            _observation(
                datetime(2026, 1, 31, 15, 30, tzinfo=timezone.utc),
                50,
                "kst-february",
            ),
            _observation(
                datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
                100,
                "kst-march",
            ),
        ),
        today=date(2026, 3, 11),
        timezone_name="Asia/Seoul",
    )
    comparison = report.comparison(UsagePeriod.MONTHLY)
    assert comparison is not None
    assert comparison.history_periods == 1
    assert comparison.history_total_tokens == 50
    assert comparison.current_tokens == 100
    assert comparison.ratio == 2
