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
from quotadeck.usage.grok import GrokUsageScan, scan_grok_session_usage
from quotadeck.usage.normalized import (
    DISCOVERED_GROK_SESSION_LIMITATIONS,
    GROK_USAGE_SOURCE,
    NormalizedUsageRecord,
    UsageConfidence,
    aggregate_normalized_records,
    rollup_normalized_records,
)
from quotadeck.usage.pricing import estimate_api_equivalent_cost
from quotadeck.usage.service import UsageLoadResult, UsageLoadStatus, UsageService


_EVENT_TOTAL_LIMITATIONS = (
    "This total covers only locally retained, attributable usage records.",
)
_GLOBAL_UNKNOWN_LIMITATION = (
    "Unknown component or cost values remain unknown in the global rollup.",
)


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
    load_results: tuple[UsageLoadResult, ...] = field(default_factory=tuple)
    grok_scans: tuple[GrokUsageScan, ...] = field(default_factory=tuple)


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


def _grok_scan_root(account: AccountRef) -> Path | None:
    raw = str(account.source_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.name.casefold() == "auth.json":
        return path.parent
    return path


def _is_session_snapshot(record: NormalizedUsageRecord) -> bool:
    """True for persisted session totals, not incremental usage events."""

    return (
        record.source == GROK_USAGE_SOURCE
        and record.session is not None
        and record.turn is None
    )


def _combine_known(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right


def _combine_known_money(
    left: Decimal | None, right: Decimal | None
) -> Decimal | None:
    if left is None or right is None:
        return None
    return left + right


def _combine_totals(
    events: NormalizedUsageRecord,
    snapshots: NormalizedUsageRecord,
    *,
    provider: str,
    source: str,
    account: str | None,
    limitations: tuple[str, ...],
) -> NormalizedUsageRecord:
    """Join event-backed increments with labelled session inventory.

    Session snapshots are not incremental events.  Combined costs stay
    unknown because resume/fork overlap can inflate session inventory.
    """

    confidence = UsageConfidence.PARTIAL
    if (
        events.confidence is UsageConfidence.UNSUPPORTED
        and snapshots.confidence is UsageConfidence.UNSUPPORTED
    ):
        confidence = UsageConfidence.UNSUPPORTED
    models = {item for item in (events.model, snapshots.model) if item}
    return NormalizedUsageRecord(
        provider=provider,
        account=account,
        model=next(iter(models)) if len(models) == 1 else None,
        input_tokens=_combine_known(events.input_tokens, snapshots.input_tokens),
        output_tokens=_combine_known(events.output_tokens, snapshots.output_tokens),
        cache_read_tokens=_combine_known(
            events.cache_read_tokens, snapshots.cache_read_tokens
        ),
        cache_write_tokens=_combine_known(
            events.cache_write_tokens, snapshots.cache_write_tokens
        ),
        reasoning_tokens=_combine_known(
            events.reasoning_tokens, snapshots.reasoning_tokens
        ),
        total_tokens=_combine_known(events.total_tokens, snapshots.total_tokens),
        reported_cost_usd=None,
        api_equivalent_cost_usd=None,
        api_equivalent_cost_krw=None,
        source=source,
        confidence=confidence,
        limitations=limitations,
    )


def _scope_total(
    records: Iterable[NormalizedUsageRecord],
    *,
    provider: str,
    source: str,
    account: str | None = None,
    limitations: tuple[str, ...] = (),
) -> NormalizedUsageRecord:
    """Roll up incremental events; never treat session snapshots as events."""

    rows = tuple(records)
    snapshots = tuple(row for row in rows if _is_session_snapshot(row))
    events = tuple(row for row in rows if not _is_session_snapshot(row))
    if snapshots and not events:
        return aggregate_normalized_records(
            snapshots,
            provider=provider,
            source=source,
            account=account,
            limitations=DISCOVERED_GROK_SESSION_LIMITATIONS + limitations,
        )
    if snapshots and events:
        return _combine_totals(
            rollup_normalized_records(
                events,
                provider=provider,
                source=source,
                account=account,
                limitations=limitations,
            ),
            aggregate_normalized_records(
                snapshots,
                provider=provider,
                source=source,
                account=account,
                limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
            ),
            provider=provider,
            source=source,
            account=account,
            limitations=limitations + DISCOVERED_GROK_SESSION_LIMITATIONS,
        )
    return rollup_normalized_records(
        events,
        provider=provider,
        source=source,
        account=account,
        limitations=limitations,
    )


def collect_normalized_usage(
    accounts: Iterable[AccountRef],
    *,
    collector: CollectorSettings | None = None,
    fx: FxRateQuote | None = None,
    loader: UsageService | None = None,
) -> NormalizedUsageReport:
    """Collect per-account records and exact-scope account/provider/global totals."""

    settings = collector
    if settings is None and loader is not None:
        settings = loader.collector
    if settings is None:
        settings = CollectorSettings.from_env()
    rate = fx.usd_to_krw if fx is not None else None
    service = loader if loader is not None else UsageService(collector=settings)
    records: list[NormalizedUsageRecord] = []
    issues: list[UsageEngineIssue] = []
    load_results: list[UsageLoadResult] = []
    grok_scans: list[GrokUsageScan] = []

    for account in accounts:
        provider = account.provider.casefold().strip()
        if provider == "grok":
            scan = scan_grok_session_usage(
                root=_grok_scan_root(account),
                usd_krw_rate=rate,
                account=account.account_id,
            )
            grok_scans.append(scan)
            records.extend(scan.session_records)
            issues.extend(
                UsageEngineIssue(provider, account.account_id, item.code)
                for item in scan.issues
            )
            continue
        if provider not in {"codex", "claude", "cursor"}:
            issues.append(UsageEngineIssue(provider, account.account_id, "unsupported"))
            continue
        result = service.load(
            provider,
            Path(account.source_path),
            account_id=account.account_id,
            collector=settings,
        )
        load_results.append(result)
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
            _scope_total(
                selected,
                provider=provider,
                account=account_id,
                source="account_total",
                limitations=_EVENT_TOTAL_LIMITATIONS,
            )
        )

    provider_totals: list[NormalizedUsageRecord] = []
    for provider in sorted({row.provider for row in records}):
        selected = tuple(row for row in records if row.provider == provider)
        provider_totals.append(
            _scope_total(
                selected,
                provider=provider,
                source="provider_total",
                limitations=_EVENT_TOTAL_LIMITATIONS,
            )
        )

    global_total = _scope_total(
        records,
        provider="all",
        source="global_total",
        limitations=_GLOBAL_UNKNOWN_LIMITATION,
    )
    return NormalizedUsageReport(
        records=tuple(records),
        account_totals=tuple(account_totals),
        provider_totals=tuple(provider_totals),
        global_total=global_total,
        fx=fx,
        issues=tuple(issues),
        load_results=tuple(load_results),
        grok_scans=tuple(grok_scans),
    )


__all__ = [
    "NormalizedUsageReport",
    "UsageEngineIssue",
    "collect_normalized_usage",
]
