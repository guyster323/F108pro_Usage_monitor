"""Provider-neutral cumulative usage collection and rollups.

Quota/rate-limit windows intentionally do not enter this module.  It returns
observed token usage, vendor-reported cost, and hypothetical API-equivalent
cost as separate fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from quotadeck.core.models import AccountRef
from quotadeck.usage.collectors import CollectorSettings
from quotadeck.usage.cursor_export import (
    discover_cursor_export_files,
    parse_cursor_csv_result,
)
from quotadeck.usage.fx import FxRateQuote
from quotadeck.usage.grok import scan_grok_session_usage
from quotadeck.usage.normalized import (
    NormalizedUsageRecord,
    UsageConfidence,
    rollup_normalized_records,
)
from quotadeck.usage.pricing import estimate_api_equivalent_cost
from quotadeck.usage.service import UsageLoadStatus, UsageService


@dataclass(frozen=True, slots=True)
class UsageEngineIssue:
    provider: str
    account: str | None
    code: str


@dataclass(frozen=True, slots=True)
class NormalizedUsageReport:
    records: tuple[NormalizedUsageRecord, ...]
    account_totals: tuple[NormalizedUsageRecord, ...]
    provider_totals: tuple[NormalizedUsageRecord, ...]
    global_total: NormalizedUsageRecord
    fx: FxRateQuote | None = None
    issues: tuple[UsageEngineIssue, ...] = field(default_factory=tuple)


def _api_cost(
    provider: str,
    model: str,
    tokens: object,
    rate: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    estimate = estimate_api_equivalent_cost(provider, model, tokens)
    usd = estimate.usd if estimate.available else None
    return usd, usd * rate if usd is not None and rate is not None else None


def _dataset_records(
    account: AccountRef,
    dataset: object,
    *,
    rate: Decimal | None,
    reported_costs: dict[str, Decimal | None] | None = None,
) -> tuple[NormalizedUsageRecord, ...]:
    observations = tuple(getattr(dataset, "observations", ()) or ())
    partial = any(bool(getattr(item, "is_partial", False)) for item in (
        getattr(dataset, "coverages", ()) or ()
    ))
    rows: list[NormalizedUsageRecord] = []
    for observation in observations:
        tokens = observation.tokens
        api_usd, api_krw = _api_cost(
            account.provider, observation.model, tokens, rate
        )
        reported = None
        if reported_costs is not None:
            reported = reported_costs.get(observation.event_id)
        limitations: list[str] = []
        reasoning: int | None = tokens.reasoning_output_tokens
        if account.provider == "cursor":
            reasoning = None
            limitations.append(
                "Cursor export does not report reasoning tokens; the field is unknown."
            )
        rows.append(
            NormalizedUsageRecord(
                provider=account.provider,
                account=account.account_id,
                model=observation.model,
                timestamp=observation.observed_at,
                input_tokens=tokens.input_tokens,
                output_tokens=tokens.output_tokens,
                cache_read_tokens=tokens.cached_input_tokens,
                cache_write_tokens=tokens.cache_write_tokens,
                reasoning_tokens=reasoning,
                total_tokens=tokens.total_tokens,
                reported_cost_usd=reported,
                api_equivalent_cost_usd=api_usd,
                api_equivalent_cost_krw=api_krw,
                source=observation.source_kind.value,
                confidence=(
                    UsageConfidence.PARTIAL if partial else UsageConfidence.EXACT
                ),
                limitations=tuple(limitations),
            )
        )
    return tuple(rows)


def _cursor_reported_costs(
    account: AccountRef,
    settings: CollectorSettings,
) -> dict[str, Decimal | None]:
    files = discover_cursor_export_files(
        explicit=settings.cursor_export_path or settings.import_path,
        account=account.account_id,
        bind_generic_account=settings.cursor_usage_csv_account,
    )
    for path in files:
        if path.suffix.casefold() != ".csv":
            continue
        result = parse_cursor_csv_result(
            path,
            account=account.account_id,
            max_records=settings.max_records,
            max_bytes=settings.max_json_bytes,
            bind_generic_account=settings.cursor_usage_csv_account,
        )
        if result.usable:
            return {
                row.observation.event_id: (
                    row.reported_cost.usd if row.reported_cost is not None else None
                )
                for row in result.rows
            }
    return {}


def collect_normalized_usage(
    accounts: Iterable[AccountRef],
    *,
    collector: CollectorSettings | None = None,
    fx: FxRateQuote | None = None,
) -> NormalizedUsageReport:
    """Collect per-account records and exact-scope account/provider/global totals."""

    settings = collector or CollectorSettings.from_env()
    rate = fx.usd_to_krw if fx is not None else None
    loader = UsageService(collector=settings)
    records: list[NormalizedUsageRecord] = []
    issues: list[UsageEngineIssue] = []

    for account in accounts:
        provider = account.provider.casefold().strip()
        if provider == "grok":
            scan = scan_grok_session_usage(
                account=account.account_id,
                usd_krw_rate=rate,
            )
            records.extend(scan.session_records)
            issues.extend(
                UsageEngineIssue(provider, account.account_id, item.code)
                for item in scan.issues
            )
            continue
        if provider not in {"codex", "claude", "cursor"}:
            issues.append(UsageEngineIssue(provider, account.account_id, "unsupported"))
            continue
        result = loader.load(
            provider,
            Path(account.source_path),
            account_id=account.account_id,
            collector=settings,
        )
        if result.dataset is None:
            issues.append(
                UsageEngineIssue(provider, account.account_id, result.status.value)
            )
            continue
        reported = (
            _cursor_reported_costs(account, settings) if provider == "cursor" else None
        )
        records.extend(
            _dataset_records(account, result.dataset, rate=rate, reported_costs=reported)
        )
        if result.status is not UsageLoadStatus.OK:
            issues.append(
                UsageEngineIssue(provider, account.account_id, result.status.value)
            )

    account_totals: list[NormalizedUsageRecord] = []
    account_keys = sorted({(row.provider, row.account) for row in records})
    for provider, account_id in account_keys:
        selected = tuple(
            row
            for row in records
            if row.provider == provider and row.account == account_id
        )
        account_totals.append(
            rollup_normalized_records(
                selected,
                provider=provider,
                account=account_id,
                source="account_total",
                limitations=(
                    "This total covers only locally retained, attributable usage records.",
                ),
            )
        )

    provider_totals: list[NormalizedUsageRecord] = []
    for provider in sorted({row.provider for row in records}):
        selected = tuple(row for row in records if row.provider == provider)
        provider_totals.append(
            rollup_normalized_records(
                selected,
                provider=provider,
                source="provider_total",
                limitations=(
                    "This total covers only locally retained, attributable usage records.",
                ),
            )
        )

    global_total = rollup_normalized_records(
        records,
        provider="all",
        source="global_total",
        limitations=(
            "Unknown component or cost values remain unknown in the global rollup.",
        ),
    )
    return NormalizedUsageReport(
        records=tuple(records),
        account_totals=tuple(account_totals),
        provider_totals=tuple(provider_totals),
        global_total=global_total,
        fx=fx,
        issues=tuple(issues),
    )


__all__ = [
    "NormalizedUsageReport",
    "UsageEngineIssue",
    "collect_normalized_usage",
]
