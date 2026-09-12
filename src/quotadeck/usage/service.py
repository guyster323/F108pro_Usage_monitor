"""Service boundary for scanning, aggregating, and optionally pricing usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, tzinfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Protocol

from quotadeck.core.models import AccountRef
from quotadeck.usage.cache import UsageDatasetCache
from quotadeck.usage.ccusage import collect_ccusage, validate_or_explain
from quotadeck.usage.collectors import (
    CollectorSettings,
    CollectorStatus,
    ValidationStatus,
    mark_dataset_partial,
)
from quotadeck.usage.cursor_export import collect_cursor_export
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.local import scan_claude_usage, scan_codex_usage
from quotadeck.usage.models import (
    PeriodUsageComparison,
    TokenUsage,
    UsageDataset,
    UsagePeriod,
    UsageReport,
)
from quotadeck.usage.token_stats import collect_token_stats


class UsageLoadStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class CostResultLike(Protocol):
    available: bool
    usd: Decimal | None
    reason: str | None


class ModelCostEstimator(Protocol):
    def __call__(
        self,
        provider: str,
        model: str,
        tokens: TokenUsage,
        *args: Any,
        **kwargs: Any,
    ) -> CostResultLike: ...


@dataclass(frozen=True, slots=True)
class UsageCostEstimate:
    usd: Decimal | None
    available: bool
    reason: str | None = None
    priced_models: int = 0

    @property
    def display(self) -> str | None:
        if not self.available or self.usd is None:
            return None
        if Decimal("0") < self.usd < Decimal("0.01"):
            return "LIST <$0.01"
        if self.usd.adjusted() > 12:
            return f"LIST ${self.usd:.2E}"
        with localcontext() as context:
            context.prec = max(28, len(self.usd.as_tuple().digits) + 3)
            shown = self.usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"LIST ${shown:,.2f}"

    @classmethod
    def unavailable(cls, reason: str) -> UsageCostEstimate:
        return cls(None, False, reason, 0)


@dataclass(frozen=True, slots=True)
class PeriodCostComparison:
    """Atomic current/average USD list-price equivalents for one period."""

    period: UsagePeriod
    this_usd: Decimal | None
    average_usd: Decimal | None
    available: bool
    reason: str | None = None
    priced_models: int = 0

    @classmethod
    def unavailable(
        cls, period: UsagePeriod, reason: str
    ) -> PeriodCostComparison:
        return cls(period, None, None, False, reason, 0)


@dataclass(frozen=True, slots=True)
class UsageLoadResult:
    provider: str
    status: UsageLoadStatus
    dataset: UsageDataset | None = None
    report: UsageReport | None = None
    cost: UsageCostEstimate | None = None
    period_costs: tuple[PeriodCostComparison, ...] = ()
    reason: str | None = None
    from_cache: bool = False

    @property
    def available(self) -> bool:
        return (
            self.status in {UsageLoadStatus.OK, UsageLoadStatus.PARTIAL}
            and self.report is not None
        )

    def period_cost(
        self, period: UsagePeriod | str
    ) -> PeriodCostComparison | None:
        try:
            selected = period if isinstance(period, UsagePeriod) else UsagePeriod(period)
        except (TypeError, ValueError):
            return None
        for item in self.period_costs:
            if item.period is selected:
                return item
        return None


def _coverage_status(dataset: UsageDataset) -> tuple[UsageLoadStatus, str | None]:
    if not dataset.observations:
        if any(
            coverage.files_discovered
            or coverage.usage_events_seen
            or coverage.is_partial
            for coverage in dataset.coverages
        ):
            return (
                UsageLoadStatus.UNAVAILABLE,
                "No valid token observations were found in retained local history.",
            )
        return UsageLoadStatus.UNAVAILABLE, "No retained local usage history was found."
    incomplete = any(coverage.is_partial for coverage in dataset.coverages)
    if incomplete:
        return (
            UsageLoadStatus.PARTIAL,
            "Some local records could not be read; the displayed token subtotal is partial.",
        )
    return UsageLoadStatus.OK, None


def _estimate_model_rows(
    model_rows: tuple[object, ...],
    estimator: ModelCostEstimator,
    *,
    billing_kind: Any = "unknown",
    empty_is_zero: bool = False,
) -> UsageCostEstimate:
    if not model_rows:
        if empty_is_zero:
            return UsageCostEstimate(Decimal("0"), True, None, 0)
        return UsageCostEstimate.unavailable("No model-attributed usage is available.")
    total = Decimal("0")
    priced = 0
    for item in model_rows:
        try:
            try:
                parameters = signature(estimator).parameters.values()
                accepts_billing = any(
                    parameter.name == "billing_kind"
                    or parameter.kind is Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                accepts_billing = True
            if accepts_billing:
                estimate = estimator(
                    item.provider,
                    item.model,
                    item.tokens,
                    billing_kind=billing_kind,
                )
            else:
                estimate = estimator(item.provider, item.model, item.tokens)
        except Exception:
            return UsageCostEstimate.unavailable(
                f"A price could not be resolved for {item.provider}/{item.model}."
            )
        if not bool(getattr(estimate, "available", False)):
            reason = getattr(estimate, "reason", None)
            return UsageCostEstimate.unavailable(
                str(reason)
                if reason
                else f"A price is unavailable for {item.provider}/{item.model}."
            )
        value = getattr(estimate, "usd", None)
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return UsageCostEstimate.unavailable(
                f"A price could not be resolved for {item.provider}/{item.model}."
            )
        if not amount.is_finite() or amount < 0:
            return UsageCostEstimate.unavailable(
                f"A price could not be resolved for {item.provider}/{item.model}."
            )
        total += amount
        priced += 1
    return UsageCostEstimate(total, True, None, priced)


def estimate_report_cost(
    report: UsageReport,
    estimator: ModelCostEstimator,
    *,
    billing_kind: Any = "unknown",
    partial: bool = False,
) -> UsageCostEstimate:
    """Price every model or return unavailable; never present a partial price."""

    if partial or any(coverage.is_partial for coverage in report.coverages):
        return UsageCostEstimate.unavailable(
            "Token coverage is partial, so a monetary estimate would be misleading."
        )
    return _estimate_model_rows(
        tuple(report.model_totals),
        estimator,
        billing_kind=billing_kind,
    )


def estimate_period_cost(
    comparison: PeriodUsageComparison,
    estimator: ModelCostEstimator,
    *,
    billing_kind: Any = "unknown",
    partial: bool = False,
    api_equivalent: bool = False,
) -> PeriodCostComparison:
    """Price THIS and AVG atomically using the actual model mix of each side."""

    if not api_equivalent:
        try:
            from quotadeck.usage.pricing import BillingKind, normalize_billing_kind

            explicit_api = normalize_billing_kind(billing_kind) is BillingKind.API
        except (ImportError, TypeError, ValueError):
            explicit_api = (
                str(getattr(billing_kind, "value", billing_kind)).casefold() == "api"
            )
        if not explicit_api:
            return PeriodCostComparison.unavailable(
                comparison.period,
                "Only explicitly API-billed history can be priced.",
            )
    if partial:
        return PeriodCostComparison.unavailable(
            comparison.period,
            "Token coverage is partial, so a monetary comparison would be misleading.",
        )
    if not comparison.current_complete:
        return PeriodCostComparison.unavailable(
            comparison.period,
            "The current calendar period is only partially observed.",
        )
    if not comparison.has_sufficient_history or comparison.history_periods == 0:
        return PeriodCostComparison.unavailable(
            comparison.period,
            "There are not enough completed periods for an average cost.",
        )

    this_estimate = _estimate_model_rows(
        tuple(comparison.current_models),
        estimator,
        billing_kind=billing_kind,
        empty_is_zero=comparison.current_usage.is_zero,
    )
    history_estimate = _estimate_model_rows(
        tuple(comparison.history_models),
        estimator,
        billing_kind=billing_kind,
        empty_is_zero=comparison.history_usage.is_zero,
    )
    if not this_estimate.available or not history_estimate.available:
        reason = this_estimate.reason or history_estimate.reason or (
            "A complete period cost could not be calculated."
        )
        return PeriodCostComparison.unavailable(comparison.period, reason)
    assert this_estimate.usd is not None and history_estimate.usd is not None
    with localcontext() as context:
        context.prec = max(
            28,
            len(history_estimate.usd.as_tuple().digits) + 16,
        )
        average = history_estimate.usd / Decimal(comparison.history_periods)
    return PeriodCostComparison(
        period=comparison.period,
        this_usd=this_estimate.usd,
        average_usd=average,
        available=True,
        priced_models=this_estimate.priced_models + history_estimate.priced_models,
    )


def _cache_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except (OSError, RuntimeError):
        return str(path.expanduser().absolute())


class UsageService:
    """Load local cumulative usage with bounded repeat-scan overhead."""

    def __init__(
        self,
        cache: UsageDatasetCache | None = None,
        collector: CollectorSettings | None = None,
    ) -> None:
        self.cache = cache if cache is not None else UsageDatasetCache()
        self.collector = collector

    def load(
        self,
        provider: str,
        source_path: Path | str,
        *,
        today: date | None = None,
        timezone_name: str | tzinfo | None = None,
        lookback_days: int = 365,
        minimum_history_days: int = 7,
        minimum_history_months: int = 1,
        comparison_days: int | None = None,
        force: bool = False,
        cost_estimator: ModelCostEstimator | None = None,
        billing_kind: Any = "unknown",
        account_id: str | None = None,
        collector: CollectorSettings | None = None,
        token_stats_runner: Any = None,
        ccusage_runner: Any = None,
    ) -> UsageLoadResult:
        normalized_provider = provider.casefold().strip()
        settings = collector if collector is not None else self.collector
        if settings is None:
            settings = CollectorSettings.from_env() if self.collector is None else self.collector
        if normalized_provider == "cursor":
            return self._finish_dataset(
                normalized_provider,
                self._load_cursor_dataset(
                    account_id=account_id or "",
                    settings=settings,
                ),
                today=today,
                timezone_name=timezone_name,
                lookback_days=lookback_days,
                minimum_history_days=minimum_history_days,
                minimum_history_months=minimum_history_months,
                comparison_days=comparison_days,
                cost_estimator=cost_estimator,
                billing_kind=billing_kind,
                from_cache=False,
            )
        if normalized_provider not in {"codex", "claude"}:
            return UsageLoadResult(
                normalized_provider,
                UsageLoadStatus.UNSUPPORTED,
                reason="No reliable local cumulative-usage scanner is available.",
            )
        path = Path(source_path).expanduser()
        cache_hint = ""
        if settings.import_path is not None:
            cache_hint = _cache_path(settings.import_path)
        key = (normalized_provider, _cache_path(path), cache_hint)
        dataset = None if force else self.cache.get(key)
        from_cache = dataset is not None
        if dataset is None:
            try:
                dataset = self._collect_preferred(
                    normalized_provider,
                    path,
                    settings=settings,
                    today=today,
                    token_stats_runner=token_stats_runner,
                    ccusage_runner=ccusage_runner,
                    account_id=account_id,
                )
            except Exception:
                return UsageLoadResult(
                    normalized_provider,
                    UsageLoadStatus.ERROR,
                    reason="Local usage history could not be scanned.",
                )
            self.cache.put(key, dataset)
        return self._finish_dataset(
            normalized_provider,
            dataset,
            today=today,
            timezone_name=timezone_name,
            lookback_days=lookback_days,
            minimum_history_days=minimum_history_days,
            minimum_history_months=minimum_history_months,
            comparison_days=comparison_days,
            cost_estimator=cost_estimator,
            billing_kind=billing_kind,
            from_cache=from_cache,
        )

    def _collect_preferred(
        self,
        provider: str,
        path: Path,
        *,
        settings: CollectorSettings,
        today: date | None,
        token_stats_runner: Any,
        ccusage_runner: Any,
        account_id: str | None,
    ) -> UsageDataset:
        preferred = collect_token_stats(
            provider,
            source_path=path,
            account=account_id,
            settings=settings,
            today=today,
            runner=token_stats_runner,
        )
        dataset: UsageDataset | None = None
        if preferred.usable:
            dataset = preferred.dataset
        else:
            native_path = path
            if provider == "codex" and (native_path.is_file() or native_path.name == "auth.json"):
                native_path = native_path.parent
            if provider == "codex":
                dataset = scan_codex_usage(native_path)
            else:
                dataset = scan_claude_usage(native_path)
            if not dataset.observations:
                fallback = collect_ccusage(
                    provider,
                    source_path=path,
                    settings=settings,
                    today=today,
                    runner=ccusage_runner,
                )
                if fallback.usable:
                    return fallback.dataset
                return dataset

        validator = collect_ccusage(
            provider,
            source_path=path,
            settings=settings,
            today=today,
            runner=ccusage_runner,
        )
        verdict = validate_or_explain(dataset, validator)
        if verdict.status is ValidationStatus.MISMATCH and dataset is not None:
            return mark_dataset_partial(
                dataset,
                verdict.reason
                or "ccusage disagreed with the preferred collector; totals were not blended.",
            )
        if (
            verdict.status is ValidationStatus.UNUSABLE
            and validator.status is not CollectorStatus.MISSING
            and dataset is not None
            and validator.reason
        ):
            # Unclear ccusage attribution never invents a blended total.
            return dataset
        assert dataset is not None
        return dataset

    def _load_cursor_dataset(
        self,
        *,
        account_id: str,
        settings: CollectorSettings,
    ) -> UsageDataset | None:
        attempt = collect_cursor_export(account=account_id, settings=settings)
        if attempt.usable:
            return attempt.dataset
        return None

    def _finish_dataset(
        self,
        normalized_provider: str,
        dataset: UsageDataset | None,
        *,
        today: date | None,
        timezone_name: str | tzinfo | None,
        lookback_days: int,
        minimum_history_days: int,
        minimum_history_months: int,
        comparison_days: int | None,
        cost_estimator: ModelCostEstimator | None,
        billing_kind: Any,
        from_cache: bool,
    ) -> UsageLoadResult:
        if dataset is None:
            if normalized_provider == "cursor":
                return UsageLoadResult(
                    normalized_provider,
                    UsageLoadStatus.UNSUPPORTED,
                    reason="Exact account-wide cumulative usage requires an admin ledger or an attributable Cursor export.",
                )
            return UsageLoadResult(
                normalized_provider,
                UsageLoadStatus.UNSUPPORTED,
                reason="No reliable local cumulative-usage scanner is available.",
            )

        status, reason = _coverage_status(dataset)
        if status is UsageLoadStatus.UNAVAILABLE:
            return UsageLoadResult(
                normalized_provider,
                status,
                dataset=dataset,
                reason=reason,
                from_cache=from_cache,
            )
        try:
            report = dataset.report(
                today=today,
                timezone_name=timezone_name,
                lookback_days=lookback_days,
                minimum_history_days=minimum_history_days,
                minimum_history_months=minimum_history_months,
                comparison_days=comparison_days,
            )
        except (OverflowError, TypeError, ValueError):
            return UsageLoadResult(
                normalized_provider,
                UsageLoadStatus.ERROR,
                dataset=dataset,
                reason="Usage analytics settings are invalid.",
                from_cache=from_cache,
            )

        # Retained records outside the requested window prove neither current
        # coverage nor a measured zero inside it. Keep the historical dataset
        # for diagnostics, but do not turn an empty analytical slice into a
        # zero-valued account card.
        if not report.model_totals:
            return UsageLoadResult(
                normalized_provider,
                UsageLoadStatus.UNAVAILABLE,
                dataset=dataset,
                report=None,
                reason="No valid token observations fall inside the requested window.",
                from_cache=from_cache,
            )

        cost = None
        period_costs: tuple[PeriodCostComparison, ...] = ()
        if cost_estimator is not None:
            cost = estimate_report_cost(
                report,
                cost_estimator,
                billing_kind=billing_kind,
                partial=status is UsageLoadStatus.PARTIAL,
            )
            period_costs = tuple(
                estimate_period_cost(
                    comparison,
                    cost_estimator,
                    billing_kind=billing_kind,
                    partial=status is UsageLoadStatus.PARTIAL,
                )
                for comparison in report.period_comparisons
            )
        return UsageLoadResult(
            normalized_provider,
            status,
            dataset=dataset,
            report=report,
            cost=cost,
            period_costs=period_costs,
            reason=reason,
            from_cache=from_cache,
        )


class CumulativeUsageService:
    """Compatibility facade that turns account references into LCD snapshots."""

    def __init__(self, cache_seconds: float = 30.0) -> None:
        self.loader = UsageService(UsageDatasetCache(cache_seconds))
        self.fx = None

    @staticmethod
    def _custom_pricing_scope(account: AccountRef) -> bool:
        return account.extra.get("pricing_scope", "").casefold() == "custom"

    def _engine_report(self, account: AccountRef):
        from quotadeck.usage.engine import collect_normalized_usage

        return collect_normalized_usage(
            (account,),
            collector=self.loader.collector,
            fx=self.fx,
            loader=self.loader,
        )

    def _grok_snapshot(
        self,
        account: AccountRef,
        period: UsagePeriod,
        engine: object,
    ) -> CumulativeSnapshot:
        scan = None
        scans = getattr(engine, "grok_scans", ())
        if scans:
            scan = scans[0]
        source_label = "GROK USAGE (SESSION ONLY)"
        if scan is not None and scan.dataset.coverages:
            source_label = scan.dataset.coverages[0].source_label
        return CumulativeSnapshot.unsupported(
            provider=account.provider,
            account_id=account.account_id,
            display_name=account.display_name,
            plan=account.plan,
            error=(
                "Daily account totals require retained external OTel v1 records. "
                "Local grok usage session totals are not an account ledger and are not summed."
            ),
            source_label=source_label,
            period=period,
        )

    @staticmethod
    def _explicit_billing_kind(account: AccountRef) -> str:
        auth_mode = account.extra.get("auth_mode", "").casefold()
        pricing_scope = account.extra.get("pricing_scope", "").casefold()
        plan = (account.plan or "").casefold()
        if auth_mode in {"ambiguous", "local_history"} or pricing_scope != "official":
            return "unknown"
        if auth_mode == "api_key" or plan == "api":
            return "api"
        if plan:
            return "subscription"
        return "unknown"

    @staticmethod
    def _default_cost_estimator() -> ModelCostEstimator | None:
        try:
            from quotadeck.usage.pricing import estimate_model_cost
        except ImportError:
            return None
        return estimate_model_cost

    def snapshot(
        self,
        account: AccountRef,
        period: UsagePeriod | str = UsagePeriod.DAILY,
    ) -> CumulativeSnapshot:
        try:
            selected_period = (
                period if isinstance(period, UsagePeriod) else UsagePeriod(period)
            )
        except (TypeError, ValueError):
            selected_period = UsagePeriod.DAILY
        engine = self._engine_report(account)
        if account.provider == "grok":
            return self._grok_snapshot(account, selected_period, engine)
        if account.provider not in {"codex", "claude", "cursor"}:
            return CumulativeSnapshot.unsupported(
                provider=account.provider,
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                error="Exact account-wide cumulative usage requires an admin ledger.",
                source_label="ADMIN API REQUIRED",
                period=selected_period,
            )

        billing_kind = self._explicit_billing_kind(account)
        result = next(iter(engine.load_results), None)
        if result is None:
            result = self.loader.load(
                account.provider,
                account.source_path,
                account_id=account.account_id,
            )
        if not result.available or result.report is None:
            cursor_blocked = account.provider == "cursor"
            return CumulativeSnapshot(
                provider=account.provider,
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                report=None,
                status=(
                    "unsupported"
                    if result.status is UsageLoadStatus.UNSUPPORTED or cursor_blocked
                    else "error"
                    if result.status is UsageLoadStatus.ERROR
                    else "unavailable"
                ),
                source_label=(
                    "ADMIN API REQUIRED"
                    if cursor_blocked
                    else "LOCAL HISTORY"
                ),
                period=selected_period,
                error=(
                    "Exact account-wide cumulative usage requires an admin ledger."
                    if cursor_blocked and result.status is UsageLoadStatus.UNSUPPORTED
                    else result.reason
                ),
            )

        partial = result.status is UsageLoadStatus.PARTIAL
        period_cost = result.period_cost(selected_period)
        if (
            (period_cost is None or not period_cost.available)
            and not self._custom_pricing_scope(account)
            and result.report is not None
        ):
            comparison = result.report.comparison(selected_period)
            if comparison is not None:
                try:
                    from quotadeck.usage.pricing import estimate_api_equivalent_cost
                except ImportError:
                    estimate_api_equivalent_cost = None
                if estimate_api_equivalent_cost is not None:
                    period_cost = estimate_period_cost(
                        comparison,
                        estimate_api_equivalent_cost,
                        billing_kind=billing_kind,
                        partial=partial,
                        api_equivalent=True,
                    )
        source_kind = ""
        if result.dataset is not None and result.dataset.coverages:
            source_kind = result.dataset.coverages[0].source_kind.value
        if account.provider == "cursor":
            source_label = "CURSOR PARTIAL" if partial else "CURSOR EXPORT"
        elif source_kind == "token_stats":
            source_label = "TOKEN-STATS PARTIAL" if partial else "TOKEN-STATS"
        elif source_kind == "ccusage":
            source_label = "CCUSAGE PARTIAL" if partial else "CCUSAGE"
        else:
            source_label = "LOCAL PARTIAL" if partial else "THIS DEVICE"
        return CumulativeSnapshot(
            provider=account.provider,
            account_id=account.account_id,
            display_name=account.display_name,
            plan=account.plan,
            report=result.report,
            status="partial" if partial else "ok",
            source_label=source_label,
            period=selected_period,
            this_cost_usd=(
                period_cost.this_usd
                if period_cost is not None and period_cost.available
                else None
            ),
            average_cost_usd=(
                period_cost.average_usd
                if period_cost is not None and period_cost.available
                else None
            ),
            cost_reason=period_cost.reason if period_cost is not None else None,
            error=result.reason,
        )

    def snapshots(
        self,
        accounts: list[AccountRef],
        period: UsagePeriod | str = UsagePeriod.DAILY,
    ) -> list[CumulativeSnapshot]:
        try:
            selected_period = (
                period if isinstance(period, UsagePeriod) else UsagePeriod(period)
            )
        except (TypeError, ValueError):
            selected_period = UsagePeriod.DAILY
        snapshots: list[CumulativeSnapshot] = []
        for account in accounts:
            try:
                snapshots.append(self.snapshot(account, selected_period))
            except Exception:
                snapshots.append(
                    CumulativeSnapshot(
                        provider=account.provider,
                        account_id=account.account_id,
                        display_name=account.display_name,
                        plan=account.plan,
                        report=None,
                        status="error",
                        source_label="LOCAL HISTORY",
                        period=selected_period,
                        error="Cumulative usage could not be loaded.",
                    )
                )
        return snapshots


__all__ = [
    "CostResultLike",
    "CumulativeUsageService",
    "ModelCostEstimator",
    "PeriodCostComparison",
    "UsageCostEstimate",
    "UsageLoadResult",
    "UsageLoadStatus",
    "UsageService",
    "estimate_period_cost",
    "estimate_report_cost",
]
