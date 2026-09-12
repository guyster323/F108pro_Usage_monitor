"""Provider-neutral cumulative usage records with explicit unknowns.

Unknown token or cost fields stay ``None``.  Callers must not treat a missing
value as zero, free, or dated.  Aggregation may deduplicate only when
provider/session/turn identifiers prove equality.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from numbers import Integral


class UsageConfidence(str, Enum):
    """How much of a record is proven rather than inferred."""

    EXACT = "exact"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


GROK_USAGE_SOURCE = "grok_usage"
GROK_WEB_SOURCE = "grok_web"
GROK_DISCOVERED_SESSION_SOURCE = "grok_discovered_sessions"
GROK_PROVIDER_AGGREGATE_SOURCE = "grok_provider_aggregate"

GROK_WEB_LIMITATIONS = (
    "grok.com consumer Web Chat is not a Grok Build cumulative ledger.",
    "No official grok.com token export is used.",
)

DISCOVERED_GROK_SESSION_LIMITATIONS = (
    "Discovered-session inventory is not an account-wide ledger.",
    "Resume and fork history can overlap across session IDs.",
    "Equal provider/session/turn identifiers are the only official dedupe key.",
    "updatedAt is file metadata, not a usage-event timestamp.",
    "Local session directories are not proof of the currently signed-in account.",
    "grok.com consumer Web Chat remains unsupported.",
)


def _optional_count(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{field_name} must be a non-negative integer or None")
    number = int(value)
    if number < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or None")
    return number


def _optional_money(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative Decimal or None")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative Decimal or None") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field_name} must be a non-negative Decimal or None")
    return number


@dataclass(frozen=True, slots=True)
class NormalizedUsageRecord:
    """One usage fact that never fabricates missing identity, tokens, or cost."""

    provider: str
    account: str | None = None
    model: str | None = None
    timestamp: datetime | None = None
    session: str | None = None
    turn: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    reported_cost_usd: Decimal | None = None
    api_equivalent_cost_usd: Decimal | None = None
    api_equivalent_cost_krw: Decimal | None = None
    source: str = ""
    confidence: UsageConfidence = UsageConfidence.PARTIAL
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip()
        if not provider:
            raise ValueError("provider is required")
        object.__setattr__(self, "provider", provider)
        account = self.account.strip() if isinstance(self.account, str) else self.account
        if account == "":
            account = None
        object.__setattr__(self, "account", account)
        model = self.model.strip() if isinstance(self.model, str) else self.model
        if model == "":
            model = None
        object.__setattr__(self, "model", model)
        session = self.session.strip() if isinstance(self.session, str) else self.session
        if session == "":
            session = None
        object.__setattr__(self, "session", session)
        if self.timestamp is not None and self.timestamp.tzinfo is None:
            object.__setattr__(
                self, "timestamp", self.timestamp.replace(tzinfo=timezone.utc)
            )
        object.__setattr__(self, "turn", _optional_count(self.turn, "turn"))
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            object.__setattr__(self, name, _optional_count(getattr(self, name), name))
        object.__setattr__(
            self, "reported_cost_usd", _optional_money(self.reported_cost_usd, "reported_cost_usd")
        )
        object.__setattr__(
            self,
            "api_equivalent_cost_usd",
            _optional_money(self.api_equivalent_cost_usd, "api_equivalent_cost_usd"),
        )
        object.__setattr__(
            self,
            "api_equivalent_cost_krw",
            _optional_money(self.api_equivalent_cost_krw, "api_equivalent_cost_krw"),
        )
        source = str(self.source or "").strip()
        object.__setattr__(self, "source", source)
        confidence = self.confidence
        if isinstance(confidence, str):
            confidence = UsageConfidence(confidence)
        if not isinstance(confidence, UsageConfidence):
            raise ValueError("confidence must be a UsageConfidence")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "limitations", tuple(self.limitations))

    @property
    def identity_key(self) -> tuple[str, str, int | None] | None:
        """Official equality key, or ``None`` when equality cannot be proven."""

        if self.session is None:
            return None
        return (self.provider, self.session, self.turn)

    # Compatibility aliases for the official Grok field terminology used by
    # older callers. The normalized wire contract uses cache_read/write_tokens.
    @property
    def cached_read_tokens(self) -> int | None:
        return self.cache_read_tokens

    @property
    def cache_creation_tokens(self) -> int | None:
        return self.cache_write_tokens


def grok_web_unsupported_record(*, account: str | None = None) -> NormalizedUsageRecord:
    """Explicit grok.com Web Chat gap: every measured field stays unknown."""

    return NormalizedUsageRecord(
        provider="grok",
        account=account,
        source=GROK_WEB_SOURCE,
        confidence=UsageConfidence.UNSUPPORTED,
        limitations=GROK_WEB_LIMITATIONS,
    )


def dedupe_normalized_records(
    records: Iterable[NormalizedUsageRecord],
) -> tuple[NormalizedUsageRecord, ...]:
    """Keep one copy of records whose official identifiers prove equality."""

    ordered: list[NormalizedUsageRecord] = []
    seen: dict[tuple[str, str, int | None], NormalizedUsageRecord] = {}
    for record in records:
        key = record.identity_key
        if key is None:
            ordered.append(record)
            continue
        previous = seen.get(key)
        if previous is None:
            seen[key] = record
            ordered.append(record)
            continue
        if previous != record:
            raise ValueError(
                "conflicting normalized usage records for "
                f"{record.provider}/{record.session}/{record.turn}"
            )
    return tuple(ordered)


def _sum_known(values: list[int | None]) -> int | None:
    if not values or any(value is None for value in values):
        return None
    return sum(values)


def _sum_known_money(values: list[Decimal | None]) -> Decimal | None:
    if not values or any(value is None for value in values):
        return None
    return sum(values, Decimal("0"))


def rollup_normalized_records(
    records: Iterable[NormalizedUsageRecord],
    *,
    provider: str,
    source: str,
    account: str | None = None,
    limitations: tuple[str, ...] = (),
) -> NormalizedUsageRecord:
    """Aggregate selected non-overlapping records with all-or-unknown fields.

    The caller chooses a single granularity (for example events or session
    totals). If any selected row lacks a token/cost category, that aggregate
    field remains ``None`` rather than understating usage or spend.
    """

    rows = dedupe_normalized_records(records)
    confidence = UsageConfidence.UNSUPPORTED
    if rows:
        confidence = (
            UsageConfidence.EXACT
            if all(row.confidence is UsageConfidence.EXACT for row in rows)
            else UsageConfidence.PARTIAL
        )
    models = {row.model for row in rows}
    return NormalizedUsageRecord(
        provider=provider,
        account=account,
        model=next(iter(models)) if len(models) == 1 else None,
        input_tokens=_sum_known([row.input_tokens for row in rows]),
        output_tokens=_sum_known([row.output_tokens for row in rows]),
        cache_read_tokens=_sum_known([row.cache_read_tokens for row in rows]),
        cache_write_tokens=_sum_known([row.cache_write_tokens for row in rows]),
        reasoning_tokens=_sum_known([row.reasoning_tokens for row in rows]),
        total_tokens=_sum_known([row.total_tokens for row in rows]),
        reported_cost_usd=_sum_known_money(
            [row.reported_cost_usd for row in rows]
        ),
        api_equivalent_cost_usd=_sum_known_money(
            [row.api_equivalent_cost_usd for row in rows]
        ),
        api_equivalent_cost_krw=_sum_known_money(
            [row.api_equivalent_cost_krw for row in rows]
        ),
        source=source,
        confidence=confidence,
        limitations=limitations,
    )


def aggregate_normalized_records(
    records: Iterable[NormalizedUsageRecord],
    *,
    provider: str,
    source: str,
    limitations: tuple[str, ...],
    account: str | None = None,
    label_model: bool = True,
) -> NormalizedUsageRecord:
    """Labelled inventory total.  Costs stay unknown when overlap is possible."""

    unique = dedupe_normalized_records(records)
    session_rows = tuple(item for item in unique if item.turn is None and item.session)
    models = {item.model for item in session_rows}
    accounts = {item.account for item in session_rows}
    model = None
    if label_model and len(models) == 1:
        model = next(iter(models))
    resolved_account = account
    if resolved_account is None and len(accounts) == 1:
        resolved_account = next(iter(accounts))
    confidence = (
        UsageConfidence.PARTIAL if session_rows else UsageConfidence.UNSUPPORTED
    )
    return NormalizedUsageRecord(
        provider=provider,
        account=resolved_account,
        model=model,
        timestamp=None,
        session=None,
        turn=None,
        input_tokens=_sum_known([item.input_tokens for item in session_rows]),
        output_tokens=_sum_known([item.output_tokens for item in session_rows]),
        cache_read_tokens=_sum_known([item.cache_read_tokens for item in session_rows]),
        cache_write_tokens=_sum_known(
            [item.cache_write_tokens for item in session_rows]
        ),
        reasoning_tokens=_sum_known([item.reasoning_tokens for item in session_rows]),
        total_tokens=_sum_known([item.total_tokens for item in session_rows]),
        reported_cost_usd=None,
        api_equivalent_cost_usd=None,
        api_equivalent_cost_krw=None,
        source=source,
        confidence=confidence,
        limitations=limitations,
    )


__all__ = [
    "DISCOVERED_GROK_SESSION_LIMITATIONS",
    "GROK_DISCOVERED_SESSION_SOURCE",
    "GROK_PROVIDER_AGGREGATE_SOURCE",
    "GROK_USAGE_SOURCE",
    "GROK_WEB_LIMITATIONS",
    "GROK_WEB_SOURCE",
    "NormalizedUsageRecord",
    "UsageConfidence",
    "aggregate_normalized_records",
    "dedupe_normalized_records",
    "grok_web_unsupported_record",
    "rollup_normalized_records",
]
