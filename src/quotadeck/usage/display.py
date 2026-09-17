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

NO_AVG_LABEL = "NO AVG"


@dataclass(frozen=True, slots=True)
class AverageGapReason:
    """Why THIS can be shown while AVG must stay N/A/--."""

    period: UsagePeriod
    history_periods: int
    minimum_history_periods: int
    current_complete: bool
    history_estimated: bool = False
    estimated_history_periods: int = 0

    @property
    def completed_history_periods(self) -> int:
        """Completed past periods only. Estimated partial months are excluded."""

        return self.history_periods


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
        return self.status in {"ok", "partial", "stale"} and self.report is not None

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
        if not self.available:
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
        if not self.available:
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
        if not self.available:
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

    def _has_reliable_reaction(self) -> bool:
        if self.intensity is None or self.intensity is UsageIntensity.INSUFFICIENT_HISTORY:
            return False
        ratio = self.ratio
        return ratio is not None and ratio == ratio and ratio >= 0

    @property
    def sprite_state(self) -> str:
        if self.status == "stale" or not self.available:
            return "stale"
        if self.status == "partial" and not self._has_reliable_reaction():
            return "stale"
        assert self.intensity is not None
        return intensity_sprite_state(self.intensity)

    def _gap_from_period(self, selected: PeriodUsageComparison) -> AverageGapReason:
        estimated = int(bool(selected.history_estimated))
        completed = max(0, selected.history_periods - estimated)
        return AverageGapReason(
            period=selected.period,
            history_periods=completed,
            minimum_history_periods=selected.minimum_history_periods,
            current_complete=selected.current_complete,
            history_estimated=selected.history_estimated,
            estimated_history_periods=estimated,
        )

    def _gap_from_today_comparison(self) -> AverageGapReason | None:
        if self.period is not UsagePeriod.DAILY or self.report is None:
            return None
        today = self.report.today_comparison
        return AverageGapReason(
            period=UsagePeriod.DAILY,
            history_periods=today.history_days,
            minimum_history_periods=today.minimum_history_days,
            current_complete=True,
        )

    @property
    def average_gap_reason(self) -> AverageGapReason | None:
        """Structured reason when AVG cannot be formed from completed history."""

        if not self.available or self.average_tokens is not None:
            return None
        selected = self.period_comparison
        if selected is not None:
            return self._gap_from_period(selected)
        return self._gap_from_today_comparison()

    def average_gap_i18n_parts(self) -> tuple[tuple[str, dict[str, object]], ...]:
        """i18n key/kwargs pairs explaining a missing AVG."""

        reason = self.average_gap_reason
        if reason is None:
            return ()
        if reason.period is UsagePeriod.DAILY:
            return (
                (
                    "avg_missing_daily",
                    {
                        "have": reason.history_periods,
                        "need": reason.minimum_history_periods,
                    },
                ),
            )
        complete_key = (
            "avg_missing_current_complete"
            if reason.current_complete
            else "avg_missing_current_incomplete"
        )
        parts: list[tuple[str, dict[str, object]]] = [
            (
                "avg_missing_monthly",
                {
                    "have": reason.history_periods,
                    "need": reason.minimum_history_periods,
                },
            )
        ]
        if reason.history_estimated and reason.estimated_history_periods:
            parts.append(
                (
                    "avg_missing_estimated_month",
                    {"estimated": reason.estimated_history_periods},
                )
            )
        parts.append((complete_key, {}))
        return tuple(parts)

    @property
    def comparison_display(self) -> str:
        if not self.available:
            return "N/A"
        if self.intensity is UsageIntensity.INSUFFICIENT_HISTORY:
            return "PARTIAL" if self.status == "partial" else NO_AVG_LABEL
        ratio = self.ratio
        if ratio is None or ratio != ratio or ratio < 0:
            return "PARTIAL" if self.status == "partial" else NO_AVG_LABEL
        if not isfinite(ratio):
            text = "3X+"
        elif ratio >= 10:
            text = "9.9X+"
        elif 0 < ratio < 0.1:
            text = "<0.1X"
        else:
            # Truncation avoids crossing a character-state boundary (1.46 -> 1.4).
            shown = floor(ratio * 10) / 10
            text = f"{shown:.1f}X"
        selected = self.period_comparison
        if self.status == "partial" or (
            selected is not None and selected.history_estimated
        ):
            return f"{text} EST"
        return text

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


def visible_on_cumulative_lcd(snapshot: CumulativeSnapshot) -> bool:
    """Unconnected Cursor stays in settings but is omitted from LCD rotation."""

    return not (
        str(snapshot.provider).casefold() == "cursor"
        and snapshot.status == "unsupported"
    )
