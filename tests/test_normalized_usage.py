from __future__ import annotations

from decimal import Decimal

import pytest

from quotadeck.usage.grok import grok_session_record, parse_grok_usage_payload
from quotadeck.usage.normalized import (
    DISCOVERED_GROK_SESSION_LIMITATIONS,
    GROK_DISCOVERED_SESSION_SOURCE,
    GROK_PROVIDER_AGGREGATE_SOURCE,
    NormalizedUsageRecord,
    UsageConfidence,
    aggregate_normalized_records,
    dedupe_normalized_records,
    grok_web_unsupported_record,
)


def _summary(
    *,
    cached: int = 20,
    created: int = 10,
    cost_ticks: int | None = 25_000_000_000,
    cost_is_partial: bool = False,
    usage_is_incomplete: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "inputTokens": 120,
        "outputTokens": 30,
        "totalTokens": 150,
        "cachedReadTokens": cached,
        "cacheCreationTokens": created,
        "reasoningTokens": 5,
        "modelCalls": 2,
        "turnCount": 1,
        "primaryModelId": "grok-4.6",
    }
    if cost_ticks is not None:
        value["costUsdTicks"] = cost_ticks
    if cost_is_partial:
        value["costIsPartial"] = True
    if usage_is_incomplete:
        value["usageIsIncomplete"] = True
    return value


def _payload(session_id: str = "session-1") -> dict[str, object]:
    session = _summary()
    session["modelUsage"] = {"grok-4.6": _summary()}
    turn = _summary()
    turn.update({"turnNumber": 1, "endedAt": "2026-09-11T12:34:50Z"})
    return {
        "sessionId": session_id,
        "updatedAt": "2026-09-11T12:34:56Z",
        "session": session,
        "turns": [turn],
    }


def test_unknown_fields_stay_none_not_zero() -> None:
    record = NormalizedUsageRecord(provider="grok", source="unit")

    assert record.account is None
    assert record.model is None
    assert record.timestamp is None
    assert record.session is None
    assert record.turn is None
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.cached_read_tokens is None
    assert record.cached_read_tokens != 0
    assert record.cache_creation_tokens is None
    assert record.reasoning_tokens is None
    assert record.total_tokens is None
    assert record.reported_cost_usd is None
    assert record.api_equivalent_cost_usd is None
    assert record.api_equivalent_cost_krw is None


def test_grok_web_is_explicitly_unsupported() -> None:
    record = grok_web_unsupported_record()

    assert record.source == "grok_web"
    assert record.confidence is UsageConfidence.UNSUPPORTED
    assert record.total_tokens is None
    assert record.reported_cost_usd is None
    assert "grok.com" in " ".join(record.limitations)


def test_official_session_record_keeps_reported_cost_and_nullable_timestamp() -> None:
    parsed = parse_grok_usage_payload(_payload())
    record = grok_session_record(parsed, usd_krw_rate=Decimal("1400"))

    assert record.session == "session-1"
    assert record.timestamp is None
    assert record.turn is None
    assert record.input_tokens == 90
    assert record.cached_read_tokens == 20
    assert record.cache_creation_tokens == 10
    assert record.total_tokens == 150
    assert record.reported_cost_usd == Decimal("2.5")
    assert record.model == "grok-4.6"
    assert record.account is None
    if record.api_equivalent_cost_usd is None:
        assert record.api_equivalent_cost_krw is None
    else:
        assert record.api_equivalent_cost_krw == record.api_equivalent_cost_usd * Decimal(
            "1400"
        )


def test_cache_creation_without_ttl_keeps_api_equivalent_unknown() -> None:
    parsed = parse_grok_usage_payload(_payload())
    record = grok_session_record(parsed)

    assert record.cache_creation_tokens == 10
    assert record.api_equivalent_cost_usd is None
    assert record.api_equivalent_cost_krw is None


def test_api_equivalent_prices_complete_categories_without_cache_writes() -> None:
    payload = _payload()
    session = _summary(cached=0, created=0, cost_ticks=25_000_000_000)
    session["modelUsage"] = {"grok-4.6": _summary(cached=0, created=0)}
    payload["session"] = session
    payload["turns"] = []
    parsed = parse_grok_usage_payload(payload)
    record = grok_session_record(parsed, usd_krw_rate=Decimal("1400"))

    assert record.cache_creation_tokens == 0
    assert record.api_equivalent_cost_usd is not None
    assert record.api_equivalent_cost_krw == record.api_equivalent_cost_usd * Decimal(
        "1400"
    )
    assert record.reported_cost_usd == Decimal("2.5")
    assert record.reported_cost_usd != record.api_equivalent_cost_usd


def test_incomplete_flags_hide_reported_cost() -> None:
    payload = _payload()
    payload["session"] = _summary(cost_is_partial=True, usage_is_incomplete=True)
    record = grok_session_record(parse_grok_usage_payload(payload))

    assert record.reported_cost_usd is None
    assert record.confidence is UsageConfidence.PARTIAL


def test_session_and_provider_aggregates_are_labelled_inventory_not_ledgers() -> None:
    first = grok_session_record(parse_grok_usage_payload(_payload("session-a")))
    second = grok_session_record(parse_grok_usage_payload(_payload("session-b")))
    duplicate = grok_session_record(parse_grok_usage_payload(_payload("session-a")))

    discovered = aggregate_normalized_records(
        (first, second, duplicate),
        provider="grok",
        source=GROK_DISCOVERED_SESSION_SOURCE,
        limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
    )
    provider = aggregate_normalized_records(
        (first, second, duplicate),
        provider="grok",
        source=GROK_PROVIDER_AGGREGATE_SOURCE,
        limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
    )

    assert discovered.session is None
    assert discovered.timestamp is None
    assert discovered.reported_cost_usd is None
    assert discovered.total_tokens == 300
    assert discovered.source == GROK_DISCOVERED_SESSION_SOURCE
    assert "not an account-wide ledger" in " ".join(discovered.limitations)
    assert provider.source == GROK_PROVIDER_AGGREGATE_SOURCE
    assert provider.total_tokens == 300
    assert provider.reported_cost_usd is None


def test_dedupe_uses_official_identifiers_only() -> None:
    first = NormalizedUsageRecord(
        provider="grok",
        session="session-1",
        turn=None,
        total_tokens=10,
        source="grok_usage",
        confidence=UsageConfidence.EXACT,
    )
    copy = NormalizedUsageRecord(
        provider="grok",
        session="session-1",
        turn=None,
        total_tokens=10,
        source="grok_usage",
        confidence=UsageConfidence.EXACT,
    )
    other = NormalizedUsageRecord(
        provider="grok",
        session="session-2",
        turn=None,
        total_tokens=4,
        source="grok_usage",
        confidence=UsageConfidence.EXACT,
    )
    unlabeled = NormalizedUsageRecord(
        provider="grok",
        total_tokens=99,
        source="grok_usage",
        confidence=UsageConfidence.PARTIAL,
    )

    deduped = dedupe_normalized_records((first, copy, other, unlabeled))
    assert len(deduped) == 3

    conflict = NormalizedUsageRecord(
        provider="grok",
        session="session-1",
        turn=None,
        total_tokens=11,
        source="grok_usage",
        confidence=UsageConfidence.EXACT,
    )
    with pytest.raises(ValueError, match="conflicting"):
        dedupe_normalized_records((first, conflict))


def test_aggregate_unknown_token_category_stays_none() -> None:
    known = NormalizedUsageRecord(
        provider="grok",
        session="a",
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        source="grok_usage",
        confidence=UsageConfidence.EXACT,
    )
    unknown_input = NormalizedUsageRecord(
        provider="grok",
        session="b",
        input_tokens=None,
        output_tokens=2,
        total_tokens=None,
        source="grok_usage",
        confidence=UsageConfidence.PARTIAL,
    )
    aggregate = aggregate_normalized_records(
        (known, unknown_input),
        provider="grok",
        source=GROK_DISCOVERED_SESSION_SOURCE,
        limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
    )

    assert aggregate.input_tokens is None
    assert aggregate.output_tokens == 4
    assert aggregate.total_tokens is None
    assert aggregate.timestamp is None
