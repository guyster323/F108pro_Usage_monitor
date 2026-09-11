from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import nan
from pathlib import Path

from quotadeck.core.models import AccountRef
from quotadeck.usage.cache import UsageDatasetCache
from quotadeck.usage.models import (
    ModelUsage,
    PeriodUsageComparison,
    TokenUsage,
    UsageDataset,
    UsageIntensity,
    UsagePeriod,
)
from quotadeck.usage.service import (
    CumulativeUsageService,
    estimate_period_cost,
    UsageCostEstimate,
    UsageLoadStatus,
    UsageService,
    estimate_report_cost,
)


def _write_claude(
    path: Path,
    *,
    malformed: bool = False,
    timestamp: str = "2026-09-11T01:00:00Z",
    session_id: str = "session",
    message_id: str = "message",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "model": "claude-sonnet-4-5-20250929",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    }
    text = json.dumps(record) + "\n"
    if malformed:
        text += "{bad\n"
    path.write_text(text, encoding="utf-8")


def test_service_returns_cached_report(tmp_path: Path) -> None:
    _write_claude(tmp_path / "project" / "session.jsonl")
    service = UsageService()
    first = service.load(
        "claude", tmp_path, today=date(2026, 9, 11), timezone_name="UTC"
    )
    second = service.load(
        "claude", tmp_path, today=date(2026, 9, 11), timezone_name="UTC"
    )
    assert first.status is UsageLoadStatus.OK
    assert not first.from_cache
    assert second.from_cache
    assert second.report is not None
    assert second.report.tokens.total_tokens == 150


def test_zero_second_cache_disables_reuse(tmp_path: Path) -> None:
    _write_claude(tmp_path / "project" / "session.jsonl")
    service = UsageService(UsageDatasetCache(0))
    first = service.load("claude", tmp_path, today=date(2026, 9, 11))
    second = service.load("claude", tmp_path, today=date(2026, 9, 11))
    assert not first.from_cache
    assert not second.from_cache


def test_cache_rejects_nonfinite_ttl() -> None:
    for value in (nan, float("inf"), float("-inf")):
        try:
            UsageDatasetCache(value)
        except ValueError:
            continue
        raise AssertionError("non-finite TTL should be rejected")


def test_cache_sweeps_expired_entries_and_bounds_path_churn() -> None:
    now = [0.0]
    dataset = UsageDataset((), ())
    cache = UsageDatasetCache(5, clock=lambda: now[0], max_entries=2)
    cache.put("expired-profile", dataset)
    now[0] = 5.0
    cache.put("current-a", dataset)
    assert cache.get("expired-profile") is None
    assert len(cache) == 1

    cache.put("current-b", dataset)
    assert cache.get("current-a") is dataset
    cache.put("current-c", dataset)
    assert cache.get("current-b") is None
    assert cache.get("current-a") is dataset
    assert cache.get("current-c") is dataset
    assert len(cache) == 2


def test_all_malformed_is_unavailable_not_zero(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("{bad-json\n", encoding="utf-8")
    result = UsageService().load("claude", tmp_path)
    assert result.status is UsageLoadStatus.UNAVAILABLE
    assert result.report is None


def test_only_out_of_window_history_is_unavailable_not_zero(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "old-session",
                "timestamp": "2025-09-10T01:00:00Z",
                "message": {
                    "id": "old-message",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = UsageService().load(
        "claude",
        tmp_path,
        today=date(2026, 9, 11),
        timezone_name="UTC",
        lookback_days=365,
    )
    assert result.status is UsageLoadStatus.UNAVAILABLE
    assert result.dataset is not None and len(result.dataset.observations) == 1
    assert result.report is None
    assert "requested window" in (result.reason or "")


def test_partial_subtotal_is_retained_but_cost_is_refused(tmp_path: Path) -> None:
    _write_claude(tmp_path / "session.jsonl", malformed=True)
    calls: list[str] = []

    def estimator(provider, model, tokens, *, billing_kind):
        calls.append(model)
        return _Price(True, Decimal("1.25"))

    result = UsageService().load(
        "claude",
        tmp_path,
        today=date(2026, 9, 11),
        timezone_name="UTC",
        cost_estimator=estimator,
        billing_kind="api",
    )
    assert result.status is UsageLoadStatus.PARTIAL
    assert result.report is not None
    assert result.report.tokens.total_tokens == 150
    assert result.cost is not None and not result.cost.available
    assert calls == []

    direct = estimate_report_cost(result.report, estimator, billing_kind="api")
    assert not direct.available
    assert calls == []


class _Price:
    def __init__(
        self,
        available: bool,
        usd: Decimal | None,
        reason: str | None = None,
    ) -> None:
        self.available = available
        self.usd = usd
        self.reason = reason


def _period_comparison(
    *,
    current: int = 100,
    history: int = 300,
    history_periods: int = 3,
) -> PeriodUsageComparison:
    current_usage = TokenUsage(input_tokens=current)
    history_usage = TokenUsage(input_tokens=history)
    return PeriodUsageComparison(
        period=UsagePeriod.MONTHLY,
        current_start=date(2026, 9, 1),
        current_end=date(2026, 9, 11),
        current_usage=current_usage,
        current_models=(ModelUsage("codex", "model", current_usage),) if current else (),
        history_usage=history_usage,
        history_models=(ModelUsage("codex", "model", history_usage),) if history else (),
        history_periods=history_periods,
        minimum_history_periods=1,
        ratio=current * history_periods / history if history else float("inf"),
        intensity=UsageIntensity.SIMILAR,
    )


def test_period_cost_prices_this_and_actual_historical_model_mix() -> None:
    current_usage = TokenUsage(input_tokens=120)
    history_usage = TokenUsage(input_tokens=600)
    comparison = PeriodUsageComparison(
        period=UsagePeriod.MONTHLY,
        current_start=date(2026, 9, 1),
        current_end=date(2026, 9, 11),
        current_usage=current_usage,
        current_models=(ModelUsage("codex", "current-expensive", current_usage),),
        history_usage=history_usage,
        history_models=(ModelUsage("codex", "history-cheap", history_usage),),
        history_periods=3,
        minimum_history_periods=1,
        ratio=0.6,
        intensity=UsageIntensity.BELOW_AVERAGE,
    )

    def estimator(provider, model, tokens, *, billing_kind):
        assert billing_kind == "api"
        divisor = Decimal(50 if model == "current-expensive" else 100)
        return _Price(True, Decimal(tokens.total_tokens) / divisor)

    result = estimate_period_cost(comparison, estimator, billing_kind="api")
    assert result.available
    assert result.this_usd == Decimal("2.4")
    assert result.average_usd == Decimal("2")


def test_period_cost_is_atomic_for_unknown_or_partial_usage() -> None:
    comparison = _period_comparison()
    unavailable = estimate_period_cost(
        comparison,
        lambda *_args, **_kwargs: _Price(False, None, "unknown model"),
        billing_kind="api",
    )
    assert not unavailable.available
    assert unavailable.this_usd is None and unavailable.average_usd is None

    partial = estimate_period_cost(
        comparison,
        lambda *_args, **_kwargs: _Price(True, Decimal("1")),
        billing_kind="api",
        partial=True,
    )
    assert not partial.available
    assert partial.this_usd is None and partial.average_usd is None


def test_period_cost_accepts_a_measured_zero_current_period() -> None:
    comparison = _period_comparison(current=0, history=300, history_periods=3)
    result = estimate_period_cost(
        comparison,
        lambda _provider, _model, tokens, **_kwargs: _Price(
            True, Decimal(tokens.total_tokens) / Decimal(100)
        ),
        billing_kind="api",
    )
    assert result.available
    assert result.this_usd == 0
    assert result.average_usd == 1

    unknown_billing = estimate_period_cost(
        _period_comparison(current=0, history=0),
        lambda *_args, **_kwargs: _Price(True, Decimal("0")),
        billing_kind="unknown",
    )
    assert not unknown_billing.available


def test_cost_hook_is_all_or_nothing(tmp_path: Path) -> None:
    _write_claude(tmp_path / "session.jsonl")
    result = UsageService().load(
        "claude", tmp_path, today=date(2026, 9, 11), timezone_name="UTC"
    )
    assert result.report is not None

    estimate = estimate_report_cost(
        result.report,
        lambda provider, model, tokens, *, billing_kind: _Price(
            True, Decimal("0.004")
        ),
        billing_kind="api",
    )
    assert estimate.available
    assert estimate.usd == Decimal("0.004")
    assert estimate.display == "LIST <$0.01"

    unavailable = estimate_report_cost(
        result.report,
        lambda provider, model, tokens, *, billing_kind: _Price(
            False, None, "unknown model"
        ),
        billing_kind="api",
    )
    assert not unavailable.available
    assert unavailable.usd is None
    assert unavailable.reason == "unknown model"


def test_cost_display_handles_extreme_decimal_without_crashing() -> None:
    estimate = UsageCostEstimate(Decimal("1e400"), True, priced_models=1)
    assert estimate.display == "LIST $1.00E+400"


def test_cost_hook_accepts_pricing_factory_callable(tmp_path: Path) -> None:
    from quotadeck.usage.pricing import make_cost_estimator

    _write_claude(tmp_path / "session.jsonl")
    result = UsageService().load(
        "claude", tmp_path, today=date(2026, 9, 11), timezone_name="UTC"
    )
    assert result.report is not None
    estimate = estimate_report_cost(
        result.report,
        make_cost_estimator(billing_kind="api"),
        billing_kind="api",
    )
    assert estimate.available
    assert estimate.display == "LIST <$0.01"


def test_cumulative_facade_keeps_subscription_cost_hidden(tmp_path: Path) -> None:
    _write_claude(
        tmp_path / "projects" / "session.jsonl",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    account = AccountRef(
        provider="claude",
        account_id="account",
        display_name="CLAUDE",
        source_path=str(tmp_path),
        plan="max",
        extra={"auth_mode": "oauth"},
    )
    snapshot = CumulativeUsageService().snapshot(account)
    assert snapshot.available
    assert snapshot.total_tokens == 150
    assert snapshot.cost_display is None


def test_cumulative_facade_prices_only_explicit_api_history(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _write_claude(
        tmp_path / "projects" / "session.jsonl",
        timestamp=now.isoformat(),
        session_id="current-session",
        message_id="current-message",
    )
    _write_claude(
        tmp_path / "projects" / "old-session.jsonl",
        timestamp=(now - timedelta(days=8)).isoformat(),
        session_id="old-session",
        message_id="old-message",
    )
    account = AccountRef(
        provider="claude",
        account_id="api-account",
        display_name="CLAUDE API",
        source_path=str(tmp_path),
        plan="api",
        extra={"auth_mode": "api_key", "pricing_scope": "official"},
    )
    snapshot = CumulativeUsageService().snapshot(account)
    assert snapshot.available
    assert snapshot.cost_display == "LIST <$0.01"


def test_cumulative_facade_never_prices_custom_endpoint_history(tmp_path: Path) -> None:
    _write_claude(
        tmp_path / "projects" / "session.jsonl",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    account = AccountRef(
        provider="claude",
        account_id="custom-api-account",
        display_name="CLAUDE GATEWAY",
        source_path=str(tmp_path),
        plan="api",
        extra={"auth_mode": "api_key", "pricing_scope": "custom"},
    )

    snapshot = CumulativeUsageService().snapshot(account)

    assert snapshot.available
    assert snapshot.cost_display is None


def test_cumulative_facade_marks_partial_and_never_prices_it(tmp_path: Path) -> None:
    _write_claude(
        tmp_path / "projects" / "session.jsonl",
        malformed=True,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    account = AccountRef(
        provider="claude",
        account_id="partial-api",
        display_name="CLAUDE API",
        source_path=str(tmp_path),
        plan="api",
        extra={"auth_mode": "api_key", "pricing_scope": "official"},
    )
    snapshot = CumulativeUsageService().snapshot(account)
    assert snapshot.available
    assert snapshot.status == "partial"
    assert snapshot.total_tokens == 150
    assert snapshot.cost_display is None
    assert snapshot.sprite_state == "stale"
    assert snapshot.ratio is None
    assert snapshot.intensity is None
    assert snapshot.comparison_display == "N/A"


def test_cumulative_facade_marks_cursor_unsupported(tmp_path: Path) -> None:
    account = AccountRef(
        provider="cursor",
        account_id="cursor",
        display_name="CURSOR",
        source_path=str(tmp_path),
    )
    snapshot = CumulativeUsageService().snapshot(account)
    assert not snapshot.available
    assert snapshot.status == "unsupported"
    assert snapshot.source_label == "ADMIN API REQUIRED"
