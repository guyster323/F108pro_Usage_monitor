"""Shared collector contracts for optional token-stats / ccusage adapters.

External CLIs are optional. When they are missing, time out, or emit an
untrusted/incomplete schema, QuotaDeck falls back to native parsers or keeps
``PARTIAL`` / ``N/A``. This module never stores prompt or response bodies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone, tzinfo
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from quotadeck.core.mask import safe_display_text
from quotadeck.usage.models import (
    TokenUsage,
    UsageCoverage,
    UsageDataset,
    UsageObservation,
    UsageSourceKind,
)
from quotadeck.usage.process import (
    DEFAULT_COLLECTOR_TIMEOUT_SECONDS,
    DEFAULT_MAX_JSON_BYTES,
    DEFAULT_MAX_RECORDS,
)


COLLECTOR_SCHEMA = "quotadeck.collector.v1"

CLIENT_TO_PROVIDER = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok",
}

# token-stats uses `client` for the tool and `provider` for the model vendor.
# Only the client identity maps onto a QuotaDeck cumulative card.
_IGNORED_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "response",
        "content",
        "messages",
        "text",
        "body",
        "conversation",
        "transcript",
    }
)


class CollectorName(str, Enum):
    TOKEN_STATS = "token-stats"
    CCUSAGE = "ccusage"
    NATIVE = "native"
    IMPORT = "import"
    CURSOR_EXPORT = "cursor-export"


class CollectorStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    UNUSABLE = "unusable"
    FALLBACK = "fallback"


class ValidationStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNUSABLE = "unusable"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    """Optional collector discovery. Missing values keep native behavior."""

    token_stats_executable: Path | None = None
    ccusage_executable: Path | None = None
    import_path: Path | None = None
    cursor_export_path: Path | None = None
    enable_token_stats: bool = False
    enable_ccusage: bool = False
    timeout_seconds: float = DEFAULT_COLLECTOR_TIMEOUT_SECONDS
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES
    max_records: int = DEFAULT_MAX_RECORDS

    @classmethod
    def from_env(cls) -> CollectorSettings:
        token_stats = _env_file("QUOTADECK_TOKEN_STATS")
        ccusage = _env_file("QUOTADECK_CCUSAGE")
        imported = _env_file("QUOTADECK_COLLECTOR_IMPORT")
        cursor_export = _env_file("QUOTADECK_CURSOR_EXPORT")
        return cls(
            token_stats_executable=token_stats,
            ccusage_executable=ccusage,
            import_path=imported,
            cursor_export_path=cursor_export,
            enable_token_stats=_env_flag(
                "QUOTADECK_ENABLE_TOKEN_STATS",
                token_stats is not None or imported is not None,
            ),
            enable_ccusage=_env_flag(
                "QUOTADECK_ENABLE_CCUSAGE",
                ccusage is not None,
            ),
        )


@dataclass(frozen=True, slots=True)
class CollectorAttempt:
    name: CollectorName
    status: CollectorStatus
    dataset: UsageDataset | None = None
    reason: str | None = None
    truncated: bool = False

    @property
    def usable(self) -> bool:
        return (
            self.status is CollectorStatus.OK
            and self.dataset is not None
            and bool(self.dataset.observations)
            and observations_have_dates(self.dataset)
        )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    status: ValidationStatus
    reason: str | None = None
    overlapping_days: int = 0


def _env_file(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off"}


def map_client_to_provider(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return CLIENT_TO_PROVIDER.get(value.casefold().strip())


def local_timezone() -> tzinfo:
    zone = datetime.now().astimezone().tzinfo
    return zone or timezone.utc


def parse_calendar_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 10 or not text[:10].replace("-", "").isdigit():
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_timestamp(value: object, *, zone: tzinfo | None = None) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=zone or timezone.utc)
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
        if number > 1e12:
            number /= 1000.0
        if number <= 0 or number > 1e12:
            return None
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        day = parse_calendar_date(value)
        if day is None:
            return None
        return datetime.combine(day, time(12, 0), tzinfo=zone or local_timezone())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or timezone.utc)
    return parsed


def observed_at_for_day(day: date, *, zone: tzinfo | None = None) -> datetime:
    """Place a daily aggregate on that local calendar date, not a UTC shift."""

    return datetime.combine(day, time(12, 0), tzinfo=zone or local_timezone())


def _first_present(raw: Mapping[str, Any], names: tuple[str, ...]) -> object:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def tokens_from_mapping(raw: Mapping[str, Any]) -> TokenUsage | None:
    """Read mutually exclusive token categories from a documented collector row.

    Reasoning is treated as a subset of output and is never added again.
    A lone ``total`` without categories is refused rather than guessed.
    """

    if any(key in raw for key in _IGNORED_CONTENT_KEYS) and not any(
        name in raw
        for name in (
            "input",
            "inputTokens",
            "input_tokens",
            "output",
            "outputTokens",
            "output_tokens",
        )
    ):
        return None
    input_tokens = _first_present(raw, ("input_tokens", "inputTokens", "input"))
    output_tokens = _first_present(raw, ("output_tokens", "outputTokens", "output"))
    if input_tokens is None and output_tokens is None:
        return None
    cached = _first_present(
        raw,
        (
            "cached_input_tokens",
            "cacheReadTokens",
            "cache_read_tokens",
            "cacheRead",
            "cache_read",
            "cached",
        ),
    )
    cache_write = _first_present(
        raw,
        (
            "cache_write_tokens",
            "cacheCreationTokens",
            "cache_creation_tokens",
            "cacheWrite",
            "cacheWriteTokens",
            "cache_write",
            "cacheCreate",
        ),
    )
    reasoning = _first_present(
        raw,
        (
            "reasoning_output_tokens",
            "reasoningTokens",
            "reasoning_tokens",
            "reasoning",
        ),
    )
    usage = TokenUsage(
        input_tokens=0 if input_tokens is None else input_tokens,
        output_tokens=0 if output_tokens is None else output_tokens,
        cached_input_tokens=0 if cached is None else cached,
        cache_write_tokens=0 if cache_write is None else cache_write,
        reasoning_output_tokens=0 if reasoning is None else reasoning,
    )
    if usage.is_zero and (input_tokens is None or output_tokens is None):
        return None
    return usage


def observation_from_row(
    raw: Mapping[str, Any],
    *,
    provider: str,
    source_kind: UsageSourceKind,
    default_model: str = "unknown",
    default_session: str = "collector",
    index: int = 0,
    zone: tzinfo | None = None,
    account: str | None = None,
) -> UsageObservation | None:
    tokens = tokens_from_mapping(raw)
    if tokens is None:
        return None
    observed = (
        parse_timestamp(
            _first_present(raw, ("observed_at", "timestamp", "createdAt", "created_at")),
            zone=zone,
        )
        or None
    )
    if observed is None:
        day = parse_calendar_date(
            _first_present(raw, ("date", "day", "period"))
        )
        if day is None:
            return None
        observed = observed_at_for_day(day, zone=zone)
    model = raw.get("model") or raw.get("modelName") or raw.get("model_name")
    session = raw.get("session_id") or raw.get("sessionId") or raw.get("session")
    event = raw.get("event_id") or raw.get("eventId") or raw.get("id")
    row_account = raw.get("account") or raw.get("account_id") or raw.get("accountId")
    if account and row_account and str(row_account).casefold() != account.casefold():
        return None
    client = map_client_to_provider(raw.get("client"))
    if client and client != provider:
        return None
    model_text = safe_display_text(
        str(model) if model else default_model, max_length=160
    )
    session_text = safe_display_text(
        str(session) if session else default_session, max_length=160
    )
    if event:
        event_text = safe_display_text(str(event), max_length=160)
    else:
        day_key = observed.date().isoformat()
        event_text = f"{provider}:{session_text}:{model_text}:{day_key}:{index}"
    return UsageObservation(
        provider=provider,
        model=model_text,
        observed_at=observed,
        tokens=tokens,
        session_id=session_text,
        event_id=event_text,
        source_kind=source_kind,
    )


def observations_have_dates(dataset: UsageDataset) -> bool:
    return all(
        isinstance(item.observed_at, datetime) for item in dataset.observations
    ) and bool(dataset.observations)


def coverage_for(
    *,
    provider: str,
    source_kind: UsageSourceKind,
    source_label: str,
    location_hint: str,
    observations: tuple[UsageObservation, ...],
    files_discovered: int = 1,
    files_read: int = 1,
    read_errors: int = 0,
    malformed: int = 0,
    truncated: bool = False,
    limitations: tuple[str, ...] = (),
) -> UsageCoverage:
    times = [item.observed_at for item in observations]
    return UsageCoverage(
        provider=provider,
        source_kind=source_kind,
        source_label=source_label,
        location_hint=location_hint,
        scanned_at=datetime.now(timezone.utc),
        files_discovered=files_discovered,
        files_read=files_read,
        read_errors=read_errors,
        usage_events_seen=len(observations) + malformed,
        observations_emitted=len(observations),
        malformed_usage_events=malformed,
        scan_truncated=truncated,
        observation_start=min(times) if times else None,
        observation_end=max(times) if times else None,
        limitations=limitations,
    )


def mark_dataset_partial(dataset: UsageDataset, limitation: str) -> UsageDataset:
    """Keep observed totals but refuse AVG/cost by marking coverage partial."""

    coverages = tuple(
        replace(
            item,
            scan_truncated=True,
            limitations=item.limitations + (limitation,),
        )
        for item in dataset.coverages
    )
    if not coverages:
        coverages = (
            coverage_for(
                provider="unknown",
                source_kind=UsageSourceKind.TOKEN_STATS,
                source_label="COLLECTOR",
                location_hint="collector",
                observations=dataset.observations,
                truncated=True,
                limitations=(limitation,),
            ),
        )
    return UsageDataset(dataset.observations, coverages)


def daily_token_totals(dataset: UsageDataset) -> dict[date, int]:
    totals: dict[date, int] = {}
    zone = local_timezone()
    for item in dataset.observations:
        day = item.observed_at.astimezone(zone).date()
        totals[day] = totals.get(day, 0) + item.tokens.total_tokens
    return totals


def validate_daily_totals(
    primary: UsageDataset,
    secondary: UsageDataset,
) -> ValidationResult:
    """Compare overlapping calendar-day totals without blending the sources."""

    if not observations_have_dates(primary) or not observations_have_dates(secondary):
        return ValidationResult(
            ValidationStatus.UNUSABLE,
            "A validator row is missing a calendar date or timestamp.",
        )
    left = daily_token_totals(primary)
    right = daily_token_totals(secondary)
    overlap = set(left) & set(right)
    if not overlap:
        return ValidationResult(
            ValidationStatus.UNUSABLE,
            "Collector periods do not overlap, so they cannot be cross-checked.",
        )
    mismatches = [day for day in sorted(overlap) if left[day] != right[day]]
    if mismatches:
        return ValidationResult(
            ValidationStatus.MISMATCH,
            "Collector daily totals disagree; values were not blended.",
            overlapping_days=len(overlap),
        )
    return ValidationResult(
        ValidationStatus.MATCH,
        overlapping_days=len(overlap),
    )


def parse_quotadeck_import(
    payload: object,
    *,
    provider: str,
    account: str | None = None,
    source_kind: UsageSourceKind = UsageSourceKind.TOKEN_STATS,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> CollectorAttempt:
    """Parse the documented QuotaDeck collector import document."""

    if not isinstance(payload, Mapping):
        return CollectorAttempt(
            CollectorName.IMPORT,
            CollectorStatus.UNUSABLE,
            reason="Collector import must be a JSON object.",
        )
    schema = str(payload.get("schema") or "")
    if schema and schema != COLLECTOR_SCHEMA:
        return CollectorAttempt(
            CollectorName.IMPORT,
            CollectorStatus.UNUSABLE,
            reason="Collector import uses an unsupported schema.",
        )
    rows = payload.get("observations")
    if not isinstance(rows, list):
        return CollectorAttempt(
            CollectorName.IMPORT,
            CollectorStatus.UNUSABLE,
            reason="Collector import is missing an observations array.",
        )
    observations: list[UsageObservation] = []
    malformed = 0
    truncated = False
    for index, row in enumerate(rows):
        if len(observations) >= max_records:
            truncated = True
            break
        if not isinstance(row, Mapping):
            malformed += 1
            continue
        item = observation_from_row(
            row,
            provider=str(row.get("provider") or provider),
            source_kind=source_kind,
            account=account,
            index=index,
        )
        if item is None or item.provider != provider:
            malformed += 1
            continue
        observations.append(item)
    if not observations:
        return CollectorAttempt(
            CollectorName.IMPORT,
            CollectorStatus.UNUSABLE,
            reason="Collector import had no dated token observations.",
        )
    coverage = coverage_for(
        provider=provider,
        source_kind=source_kind,
        source_label="COLLECTOR IMPORT",
        location_hint="import",
        observations=tuple(observations),
        malformed=malformed,
        truncated=truncated,
        limitations=(
            "Imported collector rows keep only timestamps, models, and token counters.",
        ),
    )
    return CollectorAttempt(
        CollectorName.IMPORT,
        CollectorStatus.OK,
        dataset=UsageDataset(tuple(observations), (coverage,)),
        truncated=truncated,
    )


__all__ = [
    "COLLECTOR_SCHEMA",
    "CLIENT_TO_PROVIDER",
    "CollectorAttempt",
    "CollectorName",
    "CollectorSettings",
    "CollectorStatus",
    "ValidationResult",
    "ValidationStatus",
    "coverage_for",
    "daily_token_totals",
    "local_timezone",
    "map_client_to_provider",
    "mark_dataset_partial",
    "observation_from_row",
    "observations_have_dates",
    "observed_at_for_day",
    "parse_calendar_date",
    "parse_quotadeck_import",
    "parse_timestamp",
    "tokens_from_mapping",
    "validate_daily_totals",
]
