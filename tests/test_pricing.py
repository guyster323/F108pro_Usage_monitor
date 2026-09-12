from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quotadeck.usage.models import ModelUsage, TokenUsage
from quotadeck.usage.pricing import (
    BillingKind,
    CostUnavailableReason,
    DEFAULT_PRICE_CATALOG,
    PRICE_CATALOG_AS_OF,
    estimate_api_equivalent_cost,
    estimate_model_cost,
    estimate_report_cost,
    estimate_usage_cost,
    format_estimated_cost,
    make_cost_estimator,
    normalize_billing_kind,
    normalize_provider,
)


def test_catalog_is_dated_and_exposes_all_four_services() -> None:
    catalog = DEFAULT_PRICE_CATALOG
    assert catalog.as_of == date(2026, 9, 11)
    assert PRICE_CATALOG_AS_OF == catalog.as_of
    assert catalog.version == "2026-09-11.2"
    assert catalog.unit_tokens == 1_000_000
    assert {item.provider for item in catalog.models} == {
        "codex",
        "claude",
        "cursor",
        "grok",
    }


def test_official_standard_rate_rows_are_preserved_as_decimals() -> None:
    catalog = DEFAULT_PRICE_CATALOG
    astra = catalog.resolve("openai", "gpt-6-astra")
    sonnet = catalog.resolve("anthropic", "Claude Sonnet 5")
    grok = catalog.resolve("xAI", "grok-4.6")
    assert astra is not None and (
        astra.rates.input,
        astra.rates.cached_input,
        astra.rates.cache_write,
        astra.rates.output,
    ) == (Decimal("10"), Decimal("1"), Decimal("12.5"), Decimal("50"))
    assert sonnet is not None and (
        sonnet.rates.input,
        sonnet.rates.cache_write_5m,
        sonnet.rates.cache_write_1h,
        sonnet.rates.cached_input,
        sonnet.rates.output,
    ) == (
        Decimal("2"),
        Decimal("2.5"),
        Decimal("4"),
        Decimal("0.2"),
        Decimal("10"),
    )
    assert grok is not None and (
        grok.rates.input,
        grok.rates.cached_input,
        grok.rates.output,
    ) == (Decimal("2"), Decimal("0.5"), Decimal("6"))


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("OpenAI", "codex"),
        ("anthropic", "claude"),
        ("Cursor", "cursor"),
        ("x-ai", "grok"),
    ],
)
def test_provider_aliases(alias: str, expected: str) -> None:
    assert normalize_provider(alias) == expected


def test_model_alias_and_official_date_snapshot_resolution() -> None:
    catalog = DEFAULT_PRICE_CATALOG
    display = catalog.resolve("claude", "Claude Sonnet 4.5")
    snapshot = catalog.resolve("anthropic", "claude-sonnet-4-5-20250929")
    slash = catalog.resolve("anthropic", "anthropic/claude-sonnet-4-5")
    assert display is not None
    assert display is snapshot is slash
    assert catalog.resolve("claude", "claude-sonnet-4-5-experimental") is None


@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        ("api_key", BillingKind.API),
        ("pay as you go", BillingKind.API),
        ("Plus", BillingKind.SUBSCRIPTION),
        ("SuperGrok", BillingKind.SUBSCRIPTION),
        ("ambiguous", BillingKind.MIXED),
        (None, BillingKind.UNKNOWN),
    ],
)
def test_billing_kind_is_conservative(
    billing: str | None,
    expected: BillingKind,
) -> None:
    assert normalize_billing_kind(billing) is expected


def test_unknown_billing_never_defaults_to_api_cost() -> None:
    usage = TokenUsage(input_tokens=1_000_000)
    estimate = estimate_model_cost("codex", "gpt-5.6-sol", usage)
    assert not estimate.available
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.BILLING_UNKNOWN


def test_subscription_and_mixed_history_hide_cost() -> None:
    usage = TokenUsage(input_tokens=1_000_000)
    subscription = estimate_model_cost(
        "claude",
        "claude-sonnet-5",
        usage,
        billing_kind="subscription",
    )
    mixed = estimate_model_cost(
        "claude",
        "claude-sonnet-5",
        usage,
        billing_kind="mixed",
    )
    assert subscription.reason_code is CostUnavailableReason.SUBSCRIPTION
    assert mixed.reason_code is CostUnavailableReason.BILLING_AMBIGUOUS
    assert subscription.display is None
    assert mixed.display is None


def test_openai_cost_prices_each_mutually_exclusive_category_exactly() -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        output_tokens=1_000_000,
        reasoning_output_tokens=400_000,
    )
    estimate = estimate_model_cost(
        "codex",
        "gpt-5.6-sol",
        usage,
        billing_kind=BillingKind.API,
    )
    assert estimate.available
    assert estimate.usd == Decimal("29.4")
    assert estimate.display == "LIST $29.40"
    assert estimate.exact_for_catalog
    assert not estimate.invoice_complete
    # Reasoning output is already a subset of output and is not double billed.
    assert estimate.breakdown is not None
    assert estimate.breakdown.output_usd == Decimal("20")


def test_anthropic_cache_write_ttls_use_distinct_rates() -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        cache_write_tokens=2_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    estimate = estimate_model_cost(
        "claude",
        "claude-sonnet-5",
        usage,
        billing_kind="api",
    )
    assert estimate.usd == Decimal("18.7")
    assert estimate.breakdown is not None
    assert estimate.breakdown.cache_write_usd == Decimal("6.5")


def test_anthropic_aggregate_cache_write_without_ttl_is_not_guessed() -> None:
    estimate = estimate_model_cost(
        "claude",
        "claude-sonnet-5",
        TokenUsage(cache_write_tokens=100),
        billing_kind="api",
    )
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.CACHE_WRITE_TTL_UNKNOWN


def test_unclassified_input_refuses_all_cost() -> None:
    estimate = estimate_model_cost(
        "codex",
        "gpt-5.6-luna",
        TokenUsage(input_tokens=100, unclassified_input_tokens=1),
        billing_kind="api",
    )
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.UNCLASSIFIED_INPUT


def test_unknown_model_refuses_cost_even_for_zero_usage() -> None:
    estimate = estimate_model_cost(
        "codex",
        "gpt-future-unknown",
        TokenUsage(),
        billing_kind="api",
    )
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.UNKNOWN_MODEL


def test_used_category_without_rate_refuses_cost() -> None:
    estimate = estimate_model_cost(
        "grok",
        "grok-4.6",
        TokenUsage(cache_write_tokens=10),
        billing_kind="api",
    )
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.CACHE_WRITE_TTL_UNKNOWN


def test_multi_model_estimator_is_all_or_nothing() -> None:
    rows = (
        ModelUsage("codex", "gpt-5.6-luna", TokenUsage(input_tokens=1_000_000)),
        ModelUsage("codex", "not-in-catalog", TokenUsage(output_tokens=1)),
    )
    estimate = estimate_usage_cost(rows, billing_kind="api")
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.UNKNOWN_MODEL
    assert estimate.display is None


def test_empty_api_usage_is_unknown_not_a_zero_cost() -> None:
    estimate = estimate_usage_cost((), billing_kind="api")
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.INVALID_USAGE
    assert estimate.display is None


def test_multi_model_known_rows_sum_with_decimal_arithmetic() -> None:
    rows = (
        ModelUsage("codex", "gpt-5.6-luna", TokenUsage(input_tokens=1_000_000)),
        ModelUsage("codex", "gpt-5.6-sol", TokenUsage(output_tokens=1_000_000)),
    )
    estimate = estimate_usage_cost(rows, billing_kind="api")
    assert estimate.usd == Decimal("20.2")
    assert estimate.provider == "codex"
    assert estimate.model == "multiple"


def test_cost_hook_accepts_the_service_billing_keyword() -> None:
    hook = make_cost_estimator(billing_kind="unknown")
    estimate = hook(
        "codex",
        "gpt-5.6-luna",
        TokenUsage(input_tokens=1_000_000),
        billing_kind="api",
    )
    assert estimate.usd == Decimal("0.2")


def test_cursor_result_is_explicitly_only_a_base_model_rate() -> None:
    estimate = estimate_model_cost(
        "cursor",
        "Composer 2.5",
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        billing_kind="api",
    )
    assert estimate.usd == Decimal("3.0")
    assert estimate.base_model_only
    assert not estimate.invoice_complete
    assert "Cursor Token Rate" in estimate.limitations[0]


def test_invalid_duck_typed_counts_are_not_silently_rounded() -> None:
    usage = SimpleNamespace(input_tokens=1.5)
    estimate = estimate_model_cost(
        "codex",
        "gpt-5.6-sol",
        usage,
        billing_kind="api",
    )
    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.INVALID_USAGE


def test_object_without_token_counters_is_not_zero_cost_usage() -> None:
    estimate = estimate_model_cost(
        "codex",
        "gpt-5.6-sol",
        object(),
        billing_kind="api",
    )

    assert estimate.usd is None
    assert estimate.reason_code is CostUnavailableReason.INVALID_USAGE


def test_positive_subcent_estimate_never_looks_like_zero() -> None:
    assert format_estimated_cost(Decimal("0")) == "LIST $0.00"
    assert format_estimated_cost(Decimal("0.000001")) == "LIST <$0.01"


def test_huge_list_price_equivalent_uses_bounded_scientific_display() -> None:
    estimate = estimate_model_cost(
        "codex",
        "gpt-5.6-sol",
        TokenUsage(input_tokens=10**100),
        billing_kind="api",
    )

    assert estimate.available
    assert estimate.display is not None
    assert estimate.display.startswith("LIST $")
    assert "E+" in estimate.display


def test_public_report_cost_helper_refuses_partial_coverage() -> None:
    report = SimpleNamespace(
        model_totals=(
            ModelUsage("codex", "gpt-5.6-sol", TokenUsage(input_tokens=100)),
        ),
        coverages=(SimpleNamespace(is_partial=True),),
    )

    estimate = estimate_report_cost(report, billing_kind="api")

    assert not estimate.available
    assert estimate.reason_code is CostUnavailableReason.PARTIAL_SCAN


def test_api_equivalent_cost_prices_subscription_history_without_changing_gates() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    reported = estimate_model_cost(
        "codex",
        "gpt-5.6-sol",
        usage,
        billing_kind="subscription",
    )
    equivalent = estimate_api_equivalent_cost(
        "codex",
        "gpt-5.6-sol",
        usage,
        billing_kind="subscription",
    )
    mixed = estimate_api_equivalent_cost(
        "codex",
        "gpt-5.6-sol",
        usage,
        billing_kind="mixed",
    )
    unknown = estimate_api_equivalent_cost("codex", "gpt-5.6-sol", usage)

    assert reported.usd is None
    assert reported.reason_code is CostUnavailableReason.SUBSCRIPTION
    assert equivalent.usd == Decimal("24")
    assert equivalent.billing_kind is BillingKind.SUBSCRIPTION
    assert equivalent.display == "LIST $24.00"
    assert not equivalent.invoice_complete
    assert "Hypothetical official API list-price equivalent" in equivalent.limitations[0]
    assert mixed.usd == Decimal("24")
    assert unknown.usd == Decimal("24")
    assert unknown.billing_kind is BillingKind.UNKNOWN


def test_api_equivalent_cost_still_refuses_unknown_or_unclassified_usage() -> None:
    missing_model = estimate_api_equivalent_cost(
        "codex",
        "gpt-future-unknown",
        TokenUsage(input_tokens=1),
        billing_kind="subscription",
    )
    unclassified = estimate_api_equivalent_cost(
        "codex",
        "gpt-5.6-luna",
        TokenUsage(input_tokens=100, unclassified_input_tokens=1),
        billing_kind="api",
    )
    assert missing_model.usd is None
    assert missing_model.reason_code is CostUnavailableReason.UNKNOWN_MODEL
    assert unclassified.usd is None
    assert unclassified.reason_code is CostUnavailableReason.UNCLASSIFIED_INPUT
