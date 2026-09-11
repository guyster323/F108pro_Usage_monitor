"""Aggregation and pace classification for cumulative token usage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo
from math import isfinite, nextafter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quotadeck.usage.models import (
    DailyUsage,
    ModelUsage,
    PeriodUsageComparison,
    TokenUsage,
    UsageComparison,
    UsageDataset,
    UsageIntensity,
    UsageObservation,
    UsagePeriod,
    UsageReport,
)


def classify_usage_ratio(ratio: float) -> UsageIntensity:
    """Map an average multiple to the five requested character bands."""

    try:
        value = float(ratio)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("ratio must be a non-negative number") from exc
    if value < 0 or value != value:
        raise ValueError("ratio must be a non-negative number")
    if value < 1.0:
        return UsageIntensity.BELOW_AVERAGE
    if value < 1.5:
        return UsageIntensity.SIMILAR
    if value < 2.0:
        return UsageIntensity.ABOVE_1_5X
    if value < 3.0:
        return UsageIntensity.ABOVE_2X
    return UsageIntensity.COLLAPSED_3X


def _ratio_as_float(numerator: int, denominator: int) -> float:
    """Convert a non-negative integer ratio without failing on huge counters."""

    try:
        value = numerator / denominator
    except OverflowError:
        return float("inf")
    # A rounded-up float can cross a displayed tenth or even a character-state
    # threshold. Keep this presentation value on or below the exact rational;
    # classification itself remains exact integer arithmetic below.
    if isfinite(value):
        float_numerator, float_denominator = value.as_integer_ratio()
        if float_numerator * denominator > numerator * float_denominator:
            value = nextafter(value, float("-inf"))
    return value


def _classify_integer_ratio(
    numerator: int, denominator: int
) -> UsageIntensity:
    """Classify ``numerator / denominator`` without float rounding/overflow."""

    if numerator < denominator:
        return UsageIntensity.BELOW_AVERAGE
    if numerator * 2 < denominator * 3:
        return UsageIntensity.SIMILAR
    if numerator < denominator * 2:
        return UsageIntensity.ABOVE_1_5X
    if numerator < denominator * 3:
        return UsageIntensity.ABOVE_2X
    return UsageIntensity.COLLAPSED_3X


def _ratio_and_intensity(
    current_tokens: int,
    history_total_tokens: int,
    history_periods: int,
    minimum_history_periods: int,
    *,
    current_complete: bool = True,
) -> tuple[float | None, UsageIntensity]:
    """Compare exact totals while keeping the public ratio display-friendly."""

    if not current_complete or history_periods < minimum_history_periods:
        return None, UsageIntensity.INSUFFICIENT_HISTORY
    if history_total_tokens == 0:
        if current_tokens == 0:
            # The requested bar is empty, but equal zero-use periods should use
            # the neutral reaction rather than the below-average reaction.
            return 0.0, UsageIntensity.SIMILAR
        return float("inf"), UsageIntensity.COLLAPSED_3X
    numerator = current_tokens * history_periods
    return (
        _ratio_as_float(numerator, history_total_tokens),
        _classify_integer_ratio(numerator, history_total_tokens),
    )


def compare_today_to_average(
    daily_totals: Mapping[date, TokenUsage | int],
    *,
    today: date,
    minimum_history_days: int = 7,
    history_start: date | None = None,
    comparison_days: int | None = None,
) -> UsageComparison:
    """Compare today with prior completed calendar days.

    Missing dates inside the known retained range count as zero-use days. Dates
    before the first visible record are never invented, which keeps a new
    installation from looking like hundreds of zero-use days.
    """

    if minimum_history_days < 1:
        raise ValueError("minimum_history_days must be at least 1")
    if comparison_days is not None and comparison_days < 1:
        raise ValueError("comparison_days must be at least 1 when provided")

    def total_for(day: date) -> int:
        value = daily_totals.get(day, 0)
        if isinstance(value, TokenUsage):
            return value.total_tokens
        return max(0, int(value))

    today_tokens = total_for(today)
    known_days = [day for day in daily_totals if day < today]
    if history_start is None:
        history_start = min(known_days) if known_days else today
    history_end = today - timedelta(days=1)
    if comparison_days is not None:
        history_start = max(history_start, today - timedelta(days=comparison_days))

    history_days = max(0, (history_end - history_start).days + 1)
    if history_days < minimum_history_days:
        return UsageComparison(
            today_tokens=today_tokens,
            prior_daily_average=None,
            ratio=None,
            intensity=UsageIntensity.INSUFFICIENT_HISTORY,
            history_days=history_days,
            minimum_history_days=minimum_history_days,
        )

    previous_total = sum(
        total_for(history_start + timedelta(days=offset))
        for offset in range(history_days)
    )
    average = _ratio_as_float(previous_total, history_days)
    ratio, intensity = _ratio_and_intensity(
        today_tokens,
        previous_total,
        history_days,
        minimum_history_days,
    )
    return UsageComparison(
        today_tokens=today_tokens,
        prior_daily_average=average,
        ratio=ratio,
        intensity=intensity,
        history_days=history_days,
        minimum_history_days=minimum_history_days,
    )


def _resolve_timezone(value: str | tzinfo | None) -> tzinfo | None:
    if value is None or value == "local":
        # A tzinfo captured from ``now().astimezone()`` is often a fixed offset.
        # Keeping ``None`` makes astimezone resolve the system rules separately
        # for every historical timestamp, including DST boundaries.
        return None
    if isinstance(value, tzinfo):
        return value
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"unknown timezone: {value!r}") from exc


def _local_day(observed_at: datetime, zone: tzinfo | None) -> date:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    if zone is None:
        return observed_at.astimezone().date()
    return observed_at.astimezone(zone).date()


def _next_month_start(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _aggregate_models(days: Iterable[DailyUsage]) -> tuple[ModelUsage, ...]:
    totals: dict[tuple[str, str], TokenUsage] = {}
    for day in days:
        for item in day.models:
            key = (item.provider, item.model)
            totals[key] = totals.get(key, TokenUsage()) + item.tokens
    return tuple(
        ModelUsage(provider, model, tokens)
        for (provider, model), tokens in sorted(
            totals.items(),
            key=lambda pair: (-pair[1].total_tokens, pair[0]),
        )
    )


def _make_period_comparison(
    *,
    period: UsagePeriod,
    current_start: date,
    current_end: date,
    current_days: Iterable[DailyUsage],
    history_days: Iterable[DailyUsage],
    history_periods: int,
    minimum_history_periods: int,
    current_complete: bool = True,
) -> PeriodUsageComparison:
    current_rows = tuple(current_days)
    history_rows = tuple(history_days)
    current_models = _aggregate_models(current_rows)
    history_models = _aggregate_models(history_rows)
    current_usage = TokenUsage.sum(item.tokens for item in current_models)
    history_usage = TokenUsage.sum(item.tokens for item in history_models)
    ratio, intensity = _ratio_and_intensity(
        current_usage.total_tokens,
        history_usage.total_tokens,
        history_periods,
        minimum_history_periods,
        current_complete=current_complete,
    )
    return PeriodUsageComparison(
        period=period,
        current_start=current_start,
        current_end=current_end,
        current_usage=current_usage,
        current_models=current_models,
        history_usage=history_usage,
        history_models=history_models,
        history_periods=history_periods,
        minimum_history_periods=minimum_history_periods,
        ratio=ratio,
        intensity=intensity,
        current_complete=current_complete,
    )


def build_usage_report(
    source: UsageDataset | Iterable[UsageObservation],
    *,
    today: date | None = None,
    timezone_name: str | tzinfo | None = None,
    lookback_days: int = 365,
    minimum_history_days: int = 7,
    minimum_history_months: int = 1,
    comparison_days: int | None = None,
) -> UsageReport:
    """Build retained totals plus daily and monthly calendar comparisons."""

    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    if minimum_history_months < 1:
        raise ValueError("minimum_history_months must be at least 1")
    zone = _resolve_timezone(timezone_name)
    if today is not None:
        end_day = today
    elif zone is None:
        end_day = datetime.now().astimezone().date()
    else:
        end_day = datetime.now(zone).date()
    requested_start = end_day - timedelta(days=lookback_days - 1)

    if isinstance(source, UsageDataset):
        observations = source.observations
        coverages = source.coverages
    else:
        observations = tuple(source)
        coverages = ()

    dated: list[tuple[date, UsageObservation]] = []
    earliest_visible: date | None = None
    for coverage in coverages:
        if coverage.observation_start is None:
            continue
        coverage_day = _local_day(coverage.observation_start, zone)
        if coverage_day <= end_day:
            earliest_visible = (
                coverage_day
                if earliest_visible is None
                else min(earliest_visible, coverage_day)
            )
    for item in observations:
        day = _local_day(item.observed_at, zone)
        if day <= end_day:
            earliest_visible = day if earliest_visible is None else min(earliest_visible, day)
        if requested_start <= day <= end_day and not item.tokens.is_zero:
            dated.append((day, item))

    effective_start = (
        end_day
        if earliest_visible is None
        else max(requested_start, earliest_visible)
    )
    daily_models: dict[date, dict[tuple[str, str], TokenUsage]] = defaultdict(dict)
    model_totals_map: dict[tuple[str, str], TokenUsage] = {}
    for day, item in dated:
        key = (item.provider, item.model)
        daily_models[day][key] = (
            daily_models[day].get(key, TokenUsage()) + item.tokens
        )
        model_totals_map[key] = model_totals_map.get(key, TokenUsage()) + item.tokens

    daily: list[DailyUsage] = []
    daily_total_map: dict[date, TokenUsage] = {}
    for offset in range((end_day - effective_start).days + 1):
        day = effective_start + timedelta(days=offset)
        model_items = tuple(
            ModelUsage(provider, model, tokens)
            for (provider, model), tokens in sorted(
                daily_models.get(day, {}).items(),
                key=lambda pair: (-pair[1].total_tokens, pair[0]),
            )
        )
        tokens = TokenUsage.sum(item.tokens for item in model_items)
        daily_total_map[day] = tokens
        daily.append(DailyUsage(day=day, tokens=tokens, models=model_items))

    comparison = compare_today_to_average(
        daily_total_map,
        today=end_day,
        minimum_history_days=minimum_history_days,
        history_start=effective_start,
        comparison_days=comparison_days,
    )
    daily_history_start = effective_start
    if comparison_days is not None:
        daily_history_start = max(
            daily_history_start,
            end_day - timedelta(days=comparison_days),
        )
    daily_history = tuple(
        item for item in daily if daily_history_start <= item.day < end_day
    )
    current_daily = tuple(item for item in daily if item.day == end_day)
    daily_period = _make_period_comparison(
        period=UsagePeriod.DAILY,
        current_start=end_day,
        current_end=end_day,
        current_days=current_daily,
        history_days=daily_history,
        history_periods=max(0, (end_day - daily_history_start).days),
        minimum_history_periods=minimum_history_days,
    )

    current_month_start = end_day.replace(day=1)
    first_complete_month = effective_start.replace(day=1)
    if effective_start > first_complete_month:
        first_complete_month = _next_month_start(first_complete_month)
    complete_month_starts: list[date] = []
    month = first_complete_month
    while month < current_month_start:
        complete_month_starts.append(month)
        month = _next_month_start(month)
    monthly_history_start = (
        complete_month_starts[0]
        if complete_month_starts
        else current_month_start
    )
    monthly_history = tuple(
        item
        for item in daily
        if monthly_history_start <= item.day < current_month_start
    )
    current_month = tuple(
        item for item in daily if current_month_start <= item.day <= end_day
    )
    monthly_period = _make_period_comparison(
        period=UsagePeriod.MONTHLY,
        current_start=current_month_start,
        current_end=end_day,
        current_days=current_month,
        history_days=monthly_history,
        history_periods=len(complete_month_starts),
        minimum_history_periods=minimum_history_months,
        current_complete=effective_start <= current_month_start,
    )
    model_totals = tuple(
        ModelUsage(provider, model, tokens)
        for (provider, model), tokens in sorted(
            model_totals_map.items(),
            key=lambda pair: (-pair[1].total_tokens, pair[0]),
        )
    )
    total = TokenUsage.sum(item.tokens for item in model_totals)
    return UsageReport(
        start_day=effective_start,
        end_day=end_day,
        requested_days=lookback_days,
        daily=tuple(daily),
        model_totals=model_totals,
        tokens=total,
        today_comparison=comparison,
        coverages=tuple(coverages),
        period_comparisons=(daily_period, monthly_period),
    )
