"""Data contracts for cumulative, locally observed token usage.

The usage package intentionally keeps these models independent from quota-window
models. A quota percentage and an observed token count answer different
questions and may come from sources with very different coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from enum import Enum
from math import isfinite
from numbers import Integral
from typing import Iterable

from quotadeck.core.mask import safe_display_text


class UsageSourceKind(str, Enum):
    """How usage was obtained without implying billing completeness."""

    LOCAL_OBSERVED = "local_observed"
    EXTERNAL_OTEL = "external_otel"
    TOKEN_STATS = "token_stats"
    CCUSAGE = "ccusage"
    CURSOR_EXPORT = "cursor_export"


class UsagePeriod(str, Enum):
    """Calendar period selected for the cumulative-usage comparison."""

    DAILY = "daily"
    MONTHLY = "monthly"


class CostCurrency(str, Enum):
    """Presentation currency for list-price equivalents.

    Usage services always retain canonical USD values.  KRW conversion belongs
    to the presentation layer because it also needs a dated exchange rate.
    """

    USD = "usd"
    KRW = "krw"


class UsageIntensity(str, Enum):
    """Selected-period token use relative to prior completed periods."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    BELOW_AVERAGE = "below_average"
    SIMILAR = "similar"
    ABOVE_1_5X = "above_1_5x"
    ABOVE_2X = "above_2x"
    COLLAPSED_3X = "collapsed_3x"


def _clean_count(value: object) -> int:
    """Return a non-negative integer for an untrusted local counter."""

    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, Integral):
        return max(0, int(value))
    if isinstance(value, float):
        return (
            int(value)
            if isfinite(value)
            and 0 <= value <= (2**53) - 1
            and value.is_integer()
            else 0
        )
    if isinstance(value, str):
        text = value.strip()
        if len(text) > 128 or not text.isascii() or not text.isdigit():
            return 0
        try:
            return int(text)
        except (OverflowError, ValueError):
            return 0
    try:
        number = int(value)
        if value != number:
            return 0
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized, mutually exclusive billable token categories.

    ``input_tokens`` excludes cache reads and cache writes. Cache writes are an
    aggregate with optional 5-minute/1-hour detail. Reasoning tokens are a
    subset of output and therefore are never added twice. When a cumulative
    log changes category shape, ``unclassified_input_tokens`` preserves the
    exact input delta while deliberately preventing a guessed price.
    """

    input_tokens: int = 0
    unclassified_input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "unclassified_input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "cache_write_5m_tokens",
            "cache_write_1h_tokens",
            "reasoning_output_tokens",
        ):
            object.__setattr__(self, name, _clean_count(getattr(self, name)))

        if self.reasoning_output_tokens > self.output_tokens:
            object.__setattr__(self, "reasoning_output_tokens", self.output_tokens)
        cache_detail = self.cache_write_5m_tokens + self.cache_write_1h_tokens
        if cache_detail > self.cache_write_tokens:
            object.__setattr__(self, "cache_write_tokens", cache_detail)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.unclassified_input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
            + self.output_tokens
        )

    @property
    def cache_creation_input_tokens(self) -> int:
        return self.cache_write_tokens

    @property
    def cache_write_input_tokens(self) -> int:
        return self.cache_write_tokens

    @property
    def uncached_input_tokens(self) -> int:
        return self.input_tokens

    @property
    def unclassified_cache_write_tokens(self) -> int:
        return max(
            0,
            self.cache_write_tokens
            - self.cache_write_5m_tokens
            - self.cache_write_1h_tokens,
        )

    @property
    def is_zero(self) -> bool:
        return self.total_tokens == 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            unclassified_input_tokens=(
                self.unclassified_input_tokens + other.unclassified_input_tokens
            ),
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_write_5m_tokens=(
                self.cache_write_5m_tokens + other.cache_write_5m_tokens
            ),
            cache_write_1h_tokens=(
                self.cache_write_1h_tokens + other.cache_write_1h_tokens
            ),
            reasoning_output_tokens=(
                self.reasoning_output_tokens + other.reasoning_output_tokens
            ),
        )

    def delta_from(self, earlier: TokenUsage) -> TokenUsage | None:
        """Return a cumulative-counter increment, or ``None`` after a reset.

        Category fields have changed between CLI versions. Aggregate input and
        output establish monotonicity. If categories move backwards while the
        aggregate grows, the exact increment is retained as unclassified rather
        than independently clamping fields and over-counting it.
        """

        current_input = (
            self.input_tokens
            + self.unclassified_input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
        )
        earlier_input = (
            earlier.input_tokens
            + earlier.unclassified_input_tokens
            + earlier.cached_input_tokens
            + earlier.cache_write_tokens
        )
        if current_input < earlier_input or self.output_tokens < earlier.output_tokens:
            return None

        input_total_delta = current_input - earlier_input
        category_deltas = (
            self.input_tokens - earlier.input_tokens,
            self.unclassified_input_tokens - earlier.unclassified_input_tokens,
            self.cached_input_tokens - earlier.cached_input_tokens,
            self.cache_write_tokens - earlier.cache_write_tokens,
        )
        if all(value >= 0 for value in category_deltas):
            (
                input_delta,
                unclassified_delta,
                cached_delta,
                cache_write_delta,
            ) = category_deltas
        else:
            input_delta = cached_delta = cache_write_delta = 0
            unclassified_delta = input_total_delta

        cache_write_5m_delta = max(
            0, self.cache_write_5m_tokens - earlier.cache_write_5m_tokens
        )
        cache_write_1h_delta = max(
            0, self.cache_write_1h_tokens - earlier.cache_write_1h_tokens
        )
        if cache_write_5m_delta + cache_write_1h_delta > cache_write_delta:
            # Newly appearing detail is part of the aggregate, not new usage.
            cache_write_5m_delta = 0
            cache_write_1h_delta = 0

        return TokenUsage(
            input_tokens=input_delta,
            unclassified_input_tokens=unclassified_delta,
            output_tokens=self.output_tokens - earlier.output_tokens,
            cached_input_tokens=cached_delta,
            cache_write_tokens=cache_write_delta,
            cache_write_5m_tokens=cache_write_5m_delta,
            cache_write_1h_tokens=cache_write_1h_delta,
            reasoning_output_tokens=max(
                0, self.reasoning_output_tokens - earlier.reasoning_output_tokens
            ),
        )

    @classmethod
    def sum(cls, usages: Iterable[TokenUsage]) -> TokenUsage:
        total = cls()
        for usage in usages:
            total = total + usage
        return total


@dataclass(frozen=True, slots=True)
class UsageObservation:
    """One deduplicated token increment attributed to a provider/model."""

    provider: str
    model: str
    observed_at: datetime
    tokens: TokenUsage
    session_id: str
    event_id: str
    source_kind: UsageSourceKind = UsageSourceKind.LOCAL_OBSERVED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            safe_display_text(self.provider, max_length=32),
        )
        object.__setattr__(
            self,
            "model",
            safe_display_text(self.model, max_length=160),
        )
        if self.observed_at.tzinfo is None:
            object.__setattr__(
                self, "observed_at", self.observed_at.replace(tzinfo=timezone.utc)
            )


@dataclass(frozen=True, slots=True)
class UsageCoverage:
    """Provenance and limits of a scan, without conversation contents."""

    provider: str
    source_kind: UsageSourceKind
    source_label: str
    location_hint: str
    scanned_at: datetime
    files_discovered: int = 0
    files_read: int = 0
    read_errors: int = 0
    usage_events_seen: int = 0
    observations_emitted: int = 0
    malformed_usage_events: int = 0
    duplicate_events_removed: int = 0
    counter_resets_seen: int = 0
    bytes_read: int = 0
    records_examined: int = 0
    scan_truncated: bool = False
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def observed_calendar_days(self) -> int:
        if self.observation_start is None or self.observation_end is None:
            return 0
        return (self.observation_end.date() - self.observation_start.date()).days + 1

    def has_365_day_history(self, as_of: date | None = None) -> bool:
        """Whether visible retained observations reach a 365-day window."""

        if self.observation_start is None:
            return False
        reference = as_of or self.scanned_at.date()
        return self.observation_start.date() <= reference - timedelta(days=364)

    @property
    def is_partial(self) -> bool:
        return bool(
            self.read_errors
            or self.malformed_usage_events
            or self.files_read < self.files_discovered
            or self.scan_truncated
        )

    @property
    def has_observations(self) -> bool:
        return self.observations_emitted > 0


@dataclass(frozen=True, slots=True)
class ModelUsage:
    provider: str
    model: str
    tokens: TokenUsage

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            safe_display_text(self.provider, max_length=32),
        )
        object.__setattr__(
            self,
            "model",
            safe_display_text(self.model, max_length=160),
        )

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class DailyUsage:
    day: date
    tokens: TokenUsage
    models: tuple[ModelUsage, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PeriodUsageComparison:
    """One current calendar period compared with completed prior periods.

    ``history_usage`` and ``history_models`` are totals across every eligible
    comparison period.  Keeping the exact totals and period count avoids
    rounding before ratio classification and lets pricing average the real
    historical model mix rather than applying today's blended rate.
    """

    period: UsagePeriod
    current_start: date
    current_end: date
    current_usage: TokenUsage
    current_models: tuple[ModelUsage, ...]
    history_usage: TokenUsage
    history_models: tuple[ModelUsage, ...]
    history_periods: int
    minimum_history_periods: int
    ratio: float | None
    intensity: UsageIntensity
    current_complete: bool = True

    def __post_init__(self) -> None:
        if self.current_end < self.current_start:
            raise ValueError("current_end cannot be before current_start")
        if self.history_periods < 0:
            raise ValueError("history_periods cannot be negative")
        if self.minimum_history_periods < 1:
            raise ValueError("minimum_history_periods must be at least 1")

    @property
    def current_tokens(self) -> int:
        return self.current_usage.total_tokens

    @property
    def history_total_tokens(self) -> int:
        return self.history_usage.total_tokens

    @property
    def has_sufficient_history(self) -> bool:
        return self.history_periods >= self.minimum_history_periods

    @property
    def average_tokens(self) -> int | None:
        """Completed-period average rounded half-up to a whole token."""

        if (
            not self.current_complete
            or not self.has_sufficient_history
            or self.history_periods == 0
        ):
            return None
        total = self.history_total_tokens
        periods = self.history_periods
        quotient, remainder = divmod(total, periods)
        return quotient + int(remainder * 2 >= periods)


@dataclass(frozen=True, slots=True)
class UsageComparison:
    today_tokens: int
    prior_daily_average: float | None
    ratio: float | None
    intensity: UsageIntensity
    history_days: int
    minimum_history_days: int

    @property
    def has_sufficient_history(self) -> bool:
        return self.history_days >= self.minimum_history_days


@dataclass(frozen=True, slots=True)
class UsageReport:
    """Cumulative totals and today's comparison for one display window."""

    start_day: date
    end_day: date
    requested_days: int
    daily: tuple[DailyUsage, ...]
    model_totals: tuple[ModelUsage, ...]
    tokens: TokenUsage
    today_comparison: UsageComparison
    coverages: tuple[UsageCoverage, ...] = field(default_factory=tuple)
    period_comparisons: tuple[PeriodUsageComparison, ...] = field(
        default_factory=tuple
    )

    @property
    def today(self) -> DailyUsage:
        for item in reversed(self.daily):
            if item.day == self.end_day:
                return item
        return DailyUsage(self.end_day, TokenUsage())

    def model(self, provider: str, model: str) -> ModelUsage | None:
        for item in self.model_totals:
            if item.provider == provider and item.model == model:
                return item
        return None

    def comparison(
        self, period: UsagePeriod | str
    ) -> PeriodUsageComparison | None:
        """Return a period-neutral comparison when produced by analytics."""

        try:
            selected = period if isinstance(period, UsagePeriod) else UsagePeriod(period)
        except (TypeError, ValueError):
            return None
        for item in self.period_comparisons:
            if item.period is selected:
                return item
        return None

    @property
    def observed_days(self) -> int:
        return max(1, (self.end_day - self.start_day).days + 1)

    @property
    def has_full_requested_window(self) -> bool:
        return self.observed_days >= self.requested_days

    @property
    def has_365_day_history(self) -> bool:
        return self.requested_days >= 365 and self.observed_days >= 365


@dataclass(frozen=True, slots=True)
class UsageDataset:
    """Scanner output with a direct path to display-ready analytics."""

    observations: tuple[UsageObservation, ...]
    coverages: tuple[UsageCoverage, ...]

    @property
    def records(self) -> tuple[UsageObservation, ...]:
        return self.observations

    def select(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> UsageDataset:
        selected = tuple(
            item
            for item in self.observations
            if (provider is None or item.provider == provider)
            and (model is None or item.model == model)
        )
        coverages = tuple(
            item
            for item in self.coverages
            if provider is None or item.provider == provider
        )
        return UsageDataset(selected, coverages)

    @classmethod
    def merge(cls, *datasets: UsageDataset) -> UsageDataset:
        """Merge scanner outputs while removing stable duplicate events."""

        observations: dict[tuple[str, str], UsageObservation] = {}
        coverages: list[UsageCoverage] = []
        for dataset in datasets:
            coverages.extend(dataset.coverages)
            for item in dataset.observations:
                key = (item.provider, item.event_id)
                previous = observations.get(key)
                if previous is not None:
                    same_event = (
                        previous.provider == item.provider
                        and previous.model == item.model
                        and previous.observed_at == item.observed_at
                        and previous.tokens == item.tokens
                        and previous.source_kind == item.source_kind
                    )
                    if not same_event:
                        raise ValueError(
                            f"conflicting usage observation: {item.provider}/{item.event_id}"
                        )
                    continue
                observations[key] = item
        ordered = tuple(
            sorted(
                observations.values(),
                key=lambda item: (
                    item.observed_at,
                    item.provider,
                    item.session_id,
                    item.event_id,
                ),
            )
        )
        return cls(ordered, tuple(coverages))

    def report(
        self,
        *,
        today: date | None = None,
        timezone_name: str | tzinfo | None = None,
        lookback_days: int = 365,
        minimum_history_days: int = 7,
        minimum_history_months: int = 1,
        comparison_days: int | None = None,
    ) -> UsageReport:
        from quotadeck.usage.analytics import build_usage_report

        return build_usage_report(
            self,
            today=today,
            timezone_name=timezone_name,
            lookback_days=lookback_days,
            minimum_history_days=minimum_history_days,
            minimum_history_months=minimum_history_months,
            comparison_days=comparison_days,
        )
