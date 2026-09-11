"""Display-facing contracts for cumulative token usage cards."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import floor, isfinite
from typing import Literal

from quotadeck.core.models import Severity
from quotadeck.usage.models import (
    ModelUsage,
    PeriodUsageComparison,
    UsageIntensity,
    UsagePeriod,
    UsageReport,
)


CumulativeStatus = Literal[
    "ok", "partial", "unavailable", "unsupported", "error", "stale"
]

CUMULATIVE_SPRITE_STATES: dict[UsageIntensity, str] = {
    UsageIntensity.INSUFFICIENT_HISTORY: "usage_similar",
    UsageIntensity.BELOW_AVERAGE: "usage_below",
    UsageIntensity.SIMILAR: "usage_similar",
    UsageIntensity.ABOVE_1_5X: "usage_150",
    UsageIntensity.ABOVE_2X: "usage_200",
    UsageIntensity.COLLAPSED_3X: "usage_300",
}

CUMULATIVE_SEVERITIES: dict[UsageIntensity, Severity] = {
    UsageIntensity.INSUFFICIENT_HISTORY: Severity.STALE,
    UsageIntensity.BELOW_AVERAGE: Severity.HEALTHY,
    UsageIntensity.SIMILAR: Severity.BUSY,
    UsageIntensity.ABOVE_1_5X: Severity.CAUTION,
    UsageIntensity.ABOVE_2X: Severity.CRITICAL,
    UsageIntensity.COLLAPSED_3X: Severity.EXHAUSTED,
}


def intensity_sprite_state(intensity: UsageIntensity) -> str:
    return CUMULATIVE_SPRITE_STATES[intensity]


def intensity_severity(intensity: UsageIntensity) -> Severity:
    return CUMULATIVE_SEVERITIES[intensity]


@dataclass(frozen=True, slots=True)
class CumulativeSnapshot:
    """One account's observed cumulative-usage display data.

    ``report=None`` always means unknown, never a measured zero. Partial reports
    retain their observed subtotal but use stale visual semantics and must not
    carry a monetary estimate.
    """

    provider: str
    account_id: str
    display_name: str
    plan: str | None = None
    report: UsageReport | None = None
    status: CumulativeStatus = "ok"
    source_label: str = "THIS DEVICE"
    period: UsagePeriod = UsagePeriod.DAILY
    this_cost_usd: Decimal | None = None
    average_cost_usd: Decimal | None = None
    cost_reason: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        try:
            selected = (
                self.period
                if isinstance(self.period, UsagePeriod)
                else UsagePeriod(str(self.period))
            )
        except (TypeError, ValueError):
            selected = UsagePeriod.DAILY
        object.__setattr__(self, "period", selected)

        amounts: list[Decimal | None] = []
        for raw in (self.this_cost_usd, self.average_cost_usd):
            if raw is None:
                amounts.append(None)
                continue
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError):
                amount = None
            if amount is not None and (not amount.is_finite() or amount < 0):
                amount = None
            amounts.append(amount)
        # Period costs are deliberately atomic: showing only one side would
        # make the THIS/AVG comparison look complete when it is not.
        if self.status != "ok" or any(item is None for item in amounts):
            amounts = [None, None]
        object.__setattr__(self, "this_cost_usd", amounts[0])
        object.__setattr__(self, "average_cost_usd", amounts[1])

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.account_id}"

    @property
    def available(self) -> bool:
        return self.status in {"ok", "partial"} and self.report is not None

    @property
    def period_comparison(self) -> PeriodUsageComparison | None:
        if not self.available:
            return None
        assert self.report is not None
        return self.report.comparison(self.period)

    @property
    def this_tokens(self) -> int | None:
        if not self.available:
            return None
        selected = self.period_comparison
        if selected is not None:
            return selected.current_tokens
        # Reports constructed by pre-period callers retain their original
        # daily behaviour.  A legacy report cannot safely synthesize a month.
        if self.period is UsagePeriod.DAILY:
            assert self.report is not None
            return self.report.today.tokens.total_tokens
        return None

    @property
    def today_tokens(self) -> int | None:
        """Compatibility alias for the selected period's THIS value."""

        return self.this_tokens

    @property
    def average_tokens(self) -> int | None:
        if not self.available or self.status != "ok":
            return None
        selected = self.period_comparison
        if selected is not None:
            return selected.average_tokens
        if self.period is not UsagePeriod.DAILY:
            return None
        assert self.report is not None
        average = self.report.today_comparison.prior_daily_average
        if average is None or not isfinite(average) or average < 0:
            return None
        return int(average + 0.5)

    @property
    def total_tokens(self) -> int | None:
        if not self.available:
            return None
        assert self.report is not None
        return self.report.tokens.total_tokens

    @property
    def observed_days(self) -> int | None:
        if not self.available:
            return None
        assert self.report is not None
        return max(1, (self.report.end_day - self.report.start_day).days + 1)

    @property
    def total_period_label(self) -> str:
        if not self.available:
            return "N/A"
        assert self.report is not None
        if self.report.requested_days >= 365 and self.observed_days == 365:
            return "365D"
        return "SINCE"

    @property
    def ratio(self) -> float | None:
        if not self.available or self.status != "ok":
            return None
        selected = self.period_comparison
        if selected is not None:
            return selected.ratio
        if self.period is UsagePeriod.DAILY:
            assert self.report is not None
            return self.report.today_comparison.ratio
        return None

    @property
    def intensity(self) -> UsageIntensity | None:
        if not self.available or self.status != "ok":
            return None
        selected = self.period_comparison
        if selected is not None:
            return selected.intensity
        if self.period is UsagePeriod.DAILY:
            assert self.report is not None
            return self.report.today_comparison.intensity
        return None

    @property
    def severity(self) -> Severity:
        if self.status == "error":
            return Severity.ERROR
        if self.status in {"partial", "stale"}:
            return Severity.STALE
        if not self.available:
            return Severity.OFFLINE
        assert self.intensity is not None
        return intensity_severity(self.intensity)

    @property
    def sprite_state(self) -> str:
        if self.status == "partial" or not self.available:
            return "stale"
        assert self.intensity is not None
        return intensity_sprite_state(self.intensity)

    @property
    def comparison_display(self) -> str:
        if not self.available or self.status != "ok":
            return "N/A"
        if self.intensity is UsageIntensity.INSUFFICIENT_HISTORY:
            return "BUILD"
        ratio = self.ratio
        if ratio is None or ratio != ratio or ratio < 0:
            return "BUILD"
        if not isfinite(ratio):
            return "3X+"
        if ratio >= 10:
            return "9.9X+"
        # Truncation avoids crossing a character-state boundary (1.46 -> 1.4).
        shown = floor(ratio * 10) / 10
        return f"{shown:.1f}X"

    @property
    def top_model_usage(self) -> ModelUsage | None:
        if not self.available:
            return None
        assert self.report is not None
        selected = self.period_comparison
        candidates = (
            selected.current_models
            if selected is not None and selected.current_models
            else self.report.today.models or self.report.model_totals
        )
        return candidates[0] if candidates else None

    @property
    def top_model(self) -> str | None:
        usage = self.top_model_usage
        return usage.model if usage is not None else None

    @property
    def cost_display(self) -> str | None:
        """Compatibility text for callers that still show only THIS cost."""

        if self.this_cost_usd is None:
            return None
        from quotadeck.usage.pricing import format_estimated_cost

        return format_estimated_cost(self.this_cost_usd)

    @classmethod
    def unsupported(
        cls,
        *,
        provider: str,
        account_id: str,
        display_name: str,
        plan: str | None = None,
        error: str = "Admin API required",
        source_label: str = "ADMIN API REQUIRED",
        period: UsagePeriod = UsagePeriod.DAILY,
    ) -> CumulativeSnapshot:
        return cls(
            provider=provider,
            account_id=account_id,
            display_name=display_name,
            plan=plan,
            report=None,
            status="unsupported",
            source_label=source_label,
            period=period,
            error=error,
        )
