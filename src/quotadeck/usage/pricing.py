"""Conservative API list-price estimates for observed token usage.

The estimator is intentionally all-or-nothing.  It returns no monetary value
when billing is not explicitly API based, a model is absent from the dated
catalog, or any used token category lacks an exact rate.  A displayed subtotal
therefore never disguises unknown usage as free usage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
from numbers import Integral
import re
from types import MappingProxyType
from typing import Callable

from quotadeck.usage.pricing_catalog_2026_09_11 import (
    CATALOG_ROWS,
    PRICE_CATALOG_DATE,
    PRICE_CATALOG_VERSION,
    PRICE_SOURCE_URLS,
    PRICE_UNIT_TOKENS,
)


class BillingKind(str, Enum):
    """Billing route for an observed local-history account."""

    API = "api"
    SUBSCRIPTION = "subscription"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class CostUnavailableReason(str, Enum):
    """Machine-readable reasons why an estimate was deliberately withheld."""

    SUBSCRIPTION = "subscription"
    BILLING_AMBIGUOUS = "billing_ambiguous"
    BILLING_UNKNOWN = "billing_unknown"
    UNKNOWN_MODEL = "unknown_model"
    INVALID_USAGE = "invalid_usage"
    UNCLASSIFIED_INPUT = "unclassified_input"
    CACHE_READ_RATE_MISSING = "cache_read_rate_missing"
    CACHE_WRITE_RATE_MISSING = "cache_write_rate_missing"
    CACHE_WRITE_TTL_UNKNOWN = "cache_write_ttl_unknown"
    PARTIAL_SCAN = "partial_scan"


_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "codex",
    "chatgpt": "codex",
    "codex": "codex",
    "anthropic": "claude",
    "claude": "claude",
    "claude-code": "claude",
    "cursor": "cursor",
    "xai": "grok",
    "x-ai": "grok",
    "spacexai": "grok",
    "grok": "grok",
    "grok-code": "grok",
}

_DATED_MODEL_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def normalize_provider(value: object) -> str:
    """Return the QuotaDeck service key for a provider spelling/alias."""

    text = str(value or "").strip().casefold().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    return _PROVIDER_ALIASES.get(text, text)


def normalize_model(value: object) -> str:
    """Normalize display names, API IDs, and vendor-prefixed model aliases."""

    text = str(value or "").strip().casefold()
    if "/" in text:
        prefix, remainder = text.split("/", 1)
        if normalize_provider(prefix) in {"codex", "claude", "cursor", "grok"}:
            text = remainder
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def normalize_billing_kind(value: BillingKind | str | None) -> BillingKind:
    """Classify explicit billing labels without assuming an unknown plan."""

    if isinstance(value, BillingKind):
        return value
    text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if text in {"api", "api_key", "payg", "pay_as_you_go", "usage_based"}:
        return BillingKind.API
    if text in {
        "subscription",
        "plus",
        "pro",
        "pro_plus",
        "max",
        "team",
        "teams",
        "business",
        "enterprise",
        "ultra",
        "supergrok",
        "super_grok",
    }:
        return BillingKind.SUBSCRIPTION
    if text in {"mixed", "ambiguous", "api_and_subscription"}:
        return BillingKind.MIXED
    return BillingKind.UNKNOWN


def _decimal(value: object, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid price: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"invalid price: {value!r}")
    return number


@dataclass(frozen=True, slots=True)
class TokenRates:
    """USD list price per catalog unit for mutually exclusive token types."""

    input: Decimal
    output: Decimal
    cached_input: Decimal | None = None
    cache_write: Decimal | None = None
    cache_write_5m: Decimal | None = None
    cache_write_1h: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _decimal(self.input))
        object.__setattr__(self, "output", _decimal(self.output))
        for name in (
            "cached_input",
            "cache_write",
            "cache_write_5m",
            "cache_write_1h",
        ):
            object.__setattr__(
                self,
                name,
                _decimal(getattr(self, name), allow_none=True),
            )


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """One model row from a provider's dated standard price table."""

    provider: str
    model: str
    rates: TokenRates
    aliases: tuple[str, ...] = field(default_factory=tuple)
    price_scope: str = "standard"
    source_url: str = ""
    base_model_only: bool = False

    def __post_init__(self) -> None:
        provider = normalize_provider(self.provider)
        if not provider or not normalize_model(self.model):
            raise ValueError("provider and model are required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "aliases", tuple(str(item) for item in self.aliases))


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    """Immutable model-price catalog with provider-aware alias resolution."""

    version: str
    as_of: date
    unit_tokens: int
    models: tuple[ModelPrice, ...]
    source_urls: Mapping[str, str]
    _alias_index: Mapping[tuple[str, str], ModelPrice] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.unit_tokens <= 0:
            raise ValueError("unit_tokens must be positive")
        object.__setattr__(self, "models", tuple(self.models))
        object.__setattr__(
            self,
            "source_urls",
            MappingProxyType(dict(self.source_urls)),
        )
        index: dict[tuple[str, str], ModelPrice] = {}
        for price in self.models:
            names = (price.model, *price.aliases)
            for name in names:
                key = (price.provider, normalize_model(name))
                previous = index.get(key)
                if previous is not None and previous is not price:
                    raise ValueError(
                        f"duplicate model alias for {price.provider}: {name!r}"
                    )
                index[key] = price
        object.__setattr__(self, "_alias_index", MappingProxyType(index))

    def resolve(self, provider: object, model: object) -> ModelPrice | None:
        provider_key = normalize_provider(provider)
        model_key = normalize_model(model)
        match = self._alias_index.get((provider_key, model_key))
        if match is not None:
            return match
        # Official snapshot IDs commonly append YYYYMMDD or YYYY-MM-DD.  Only
        # that strictly numeric suffix is discarded; arbitrary prefixes and
        # variants remain unknown rather than receiving a guessed price.
        without_date = _DATED_MODEL_SUFFIX.sub("", model_key)
        if without_date != model_key:
            return self._alias_index.get((provider_key, without_date))
        return None

    def for_provider(self, provider: object) -> tuple[ModelPrice, ...]:
        key = normalize_provider(provider)
        return tuple(item for item in self.models if item.provider == key)

    def estimate(
        self,
        provider: str,
        model: str,
        tokens: object,
        *,
        billing_kind: BillingKind | str | None = BillingKind.UNKNOWN,
    ) -> CostEstimate:
        return estimate_model_cost(
            provider,
            model,
            tokens,
            billing_kind=billing_kind,
            catalog=self,
        )


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Exact Decimal arithmetic for the catalog-covered token categories."""

    input_usd: Decimal = Decimal("0")
    cached_input_usd: Decimal = Decimal("0")
    cache_write_usd: Decimal = Decimal("0")
    output_usd: Decimal = Decimal("0")

    @property
    def total_usd(self) -> Decimal:
        return (
            self.input_usd
            + self.cached_input_usd
            + self.cache_write_usd
            + self.output_usd
        )

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        if not isinstance(other, CostBreakdown):
            return NotImplemented
        return CostBreakdown(
            input_usd=self.input_usd + other.input_usd,
            cached_input_usd=self.cached_input_usd + other.cached_input_usd,
            cache_write_usd=self.cache_write_usd + other.cache_write_usd,
            output_usd=self.output_usd + other.output_usd,
        )


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A complete catalog subtotal, or an explicit reason it is unavailable."""

    usd: Decimal | None
    provider: str
    model: str
    billing_kind: BillingKind
    catalog_version: str
    catalog_as_of: date
    price_scope: str | None = None
    breakdown: CostBreakdown | None = None
    reason_code: CostUnavailableReason | None = None
    reason: str | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)
    base_model_only: bool = False

    @property
    def available(self) -> bool:
        return self.usd is not None

    @property
    def exact_for_catalog(self) -> bool:
        """Whether every observed category was priced by the selected rows."""

        return self.available

    @property
    def invoice_complete(self) -> bool:
        """List-price arithmetic is never represented as an account invoice."""

        return False

    @property
    def display(self) -> str | None:
        if self.usd is None:
            return None
        return format_estimated_cost(self.usd)


def format_estimated_cost(amount: Decimal | int | str) -> str:
    """Format a list-price equivalent without implying an account invoice."""

    value = _decimal(amount)
    assert value is not None
    if Decimal("0") < value < Decimal("0.01"):
        return "LIST <$0.01"
    if value.adjusted() > 12:
        return f"LIST ${value:.2E}"
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 3)
        shown = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"LIST ${shown:,.2f}"


def _catalog_limitations(price: ModelPrice) -> tuple[str, ...]:
    if price.base_model_only:
        return (
            "Cursor base-model token rate only; excludes Cursor Token Rate, "
            "cursorTokenFee, credits, taxes, and other account charges.",
        )
    if price.price_scope == "standard_short_context":
        return (
            "Standard short-context list rate only; long-context, fast, batch, "
            "regional, tool, and other charges are excluded.",
        )
    return (
        "First-party standard list rate only; batch, regional, tool, tax, "
        "credit, and other charges are excluded.",
    )


_API_EQUIVALENT_LIMITATION = (
    "Hypothetical official API list-price equivalent; not reported spend or an invoice."
)


def _with_api_equivalent_limitation(limitations: tuple[str, ...]) -> tuple[str, ...]:
    if _API_EQUIVALENT_LIMITATION in limitations:
        return limitations
    return (_API_EQUIVALENT_LIMITATION, *limitations)


def _unavailable(
    *,
    provider: object,
    model: object,
    billing_kind: BillingKind,
    catalog: PriceCatalog,
    reason_code: CostUnavailableReason,
    reason: str,
) -> CostEstimate:
    return CostEstimate(
        usd=None,
        provider=normalize_provider(provider),
        model=str(model or ""),
        billing_kind=billing_kind,
        catalog_version=catalog.version,
        catalog_as_of=catalog.as_of,
        reason_code=reason_code,
        reason=reason,
    )


_TOKEN_FIELDS = (
    "input_tokens",
    "unclassified_input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "reasoning_output_tokens",
)
_MISSING_TOKEN_FIELD = object()


def _read_token_counts(tokens: object) -> tuple[dict[str, int] | None, str | None]:
    values: dict[str, int] = {}
    recognized_fields = 0
    for name in _TOKEN_FIELDS:
        try:
            raw = getattr(tokens, name, _MISSING_TOKEN_FIELD)
        except Exception as exc:  # pragma: no cover - hostile duck type
            return None, f"could not read {name}: {exc}"
        if raw is _MISSING_TOKEN_FIELD:
            raw = 0
        else:
            recognized_fields += 1
        if isinstance(raw, bool) or not isinstance(raw, Integral):
            return None, f"{name} must be a non-negative integer"
        value = int(raw)
        if value < 0:
            return None, f"{name} must be a non-negative integer"
        values[name] = value

    if recognized_fields == 0:
        return None, "usage object exposes no recognized token counters"

    if values["reasoning_output_tokens"] > values["output_tokens"]:
        return None, "reasoning_output_tokens cannot exceed output_tokens"
    cache_detail = (
        values["cache_write_5m_tokens"] + values["cache_write_1h_tokens"]
    )
    if cache_detail > values["cache_write_tokens"]:
        return None, "cache-write TTL detail exceeds aggregate cache writes"
    return values, None


def _charge(tokens: int, rate: Decimal, unit_tokens: int) -> Decimal:
    return Decimal(tokens) * rate / Decimal(unit_tokens)


def estimate_model_cost(
    provider: str,
    model: str,
    tokens: object,
    *,
    billing_kind: BillingKind | str | None = BillingKind.UNKNOWN,
    catalog: PriceCatalog | None = None,
) -> CostEstimate:
    """Price one model only when billing and every used category are known."""

    selected_catalog = catalog or DEFAULT_PRICE_CATALOG
    billing = normalize_billing_kind(billing_kind)
    if billing is BillingKind.SUBSCRIPTION:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.SUBSCRIPTION,
            reason="subscription usage is not API pay-as-you-go cost",
        )
    if billing is BillingKind.MIXED:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.BILLING_AMBIGUOUS,
            reason="mixed subscription/API history cannot be priced exactly",
        )
    if billing is not BillingKind.API:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.BILLING_UNKNOWN,
            reason="API billing was not explicitly established",
        )
    return _price_catalog_tokens(
        provider,
        model,
        tokens,
        billing=billing,
        catalog=selected_catalog,
    )


def estimate_api_equivalent_cost(
    provider: str,
    model: str,
    tokens: object,
    *,
    billing_kind: BillingKind | str | None = BillingKind.UNKNOWN,
    catalog: PriceCatalog | None = None,
) -> CostEstimate:
    """Apply official catalog rates regardless of subscription or mixed billing.

    This is a hypothetical list-price equivalent, never reported spend.  Token
    coverage, model identity, and category rates still fail closed to ``None``.
    """

    selected_catalog = catalog or DEFAULT_PRICE_CATALOG
    billing = normalize_billing_kind(billing_kind)
    estimate = _price_catalog_tokens(
        provider,
        model,
        tokens,
        billing=billing,
        catalog=selected_catalog,
    )
    if not estimate.available:
        return estimate
    return replace(
        estimate,
        limitations=_with_api_equivalent_limitation(estimate.limitations),
    )


def _price_catalog_tokens(
    provider: str,
    model: str,
    tokens: object,
    *,
    billing: BillingKind,
    catalog: PriceCatalog,
) -> CostEstimate:
    price = catalog.resolve(provider, model)
    if price is None:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=catalog,
            reason_code=CostUnavailableReason.UNKNOWN_MODEL,
            reason=f"no {catalog.as_of.isoformat()} price for this model",
        )

    counts, invalid_reason = _read_token_counts(tokens)
    if counts is None:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=catalog,
            reason_code=CostUnavailableReason.INVALID_USAGE,
            reason=invalid_reason or "invalid token usage",
        )
    if counts["unclassified_input_tokens"]:
        return _unavailable(
            provider=provider,
            model=model,
            billing_kind=billing,
            catalog=catalog,
            reason_code=CostUnavailableReason.UNCLASSIFIED_INPUT,
            reason="input token categories changed and cannot be priced exactly",
        )

    rates = price.rates
    unit = catalog.unit_tokens
    input_usd = _charge(counts["input_tokens"], rates.input, unit)
    output_usd = _charge(counts["output_tokens"], rates.output, unit)

    cached_input_usd = Decimal("0")
    if counts["cached_input_tokens"]:
        if rates.cached_input is None:
            return _unavailable(
                provider=provider,
                model=model,
                billing_kind=billing,
                catalog=catalog,
                reason_code=CostUnavailableReason.CACHE_READ_RATE_MISSING,
                reason="observed cache reads have no catalog rate",
            )
        cached_input_usd = _charge(counts["cached_input_tokens"], rates.cached_input, unit)

    cache_write_usd = Decimal("0")
    five_minute = counts["cache_write_5m_tokens"]
    one_hour = counts["cache_write_1h_tokens"]
    aggregate = counts["cache_write_tokens"]
    unclassified_write = aggregate - five_minute - one_hour

    if five_minute:
        rate = rates.cache_write_5m or rates.cache_write
        if rate is None:
            return _unavailable(
                provider=provider,
                model=model,
                billing_kind=billing,
                catalog=catalog,
                reason_code=CostUnavailableReason.CACHE_WRITE_RATE_MISSING,
                reason="observed 5-minute cache writes have no catalog rate",
            )
        cache_write_usd += _charge(five_minute, rate, unit)
    if one_hour:
        rate = rates.cache_write_1h or rates.cache_write
        if rate is None:
            return _unavailable(
                provider=provider,
                model=model,
                billing_kind=billing,
                catalog=catalog,
                reason_code=CostUnavailableReason.CACHE_WRITE_RATE_MISSING,
                reason="observed 1-hour cache writes have no catalog rate",
            )
        cache_write_usd += _charge(one_hour, rate, unit)
    if unclassified_write:
        if rates.cache_write is None:
            return _unavailable(
                provider=provider,
                model=model,
                billing_kind=billing,
                catalog=catalog,
                reason_code=CostUnavailableReason.CACHE_WRITE_TTL_UNKNOWN,
                reason="cache-write TTL is required to choose an exact rate",
            )
        cache_write_usd += _charge(unclassified_write, rates.cache_write, unit)

    breakdown = CostBreakdown(
        input_usd=input_usd,
        cached_input_usd=cached_input_usd,
        cache_write_usd=cache_write_usd,
        output_usd=output_usd,
    )
    return CostEstimate(
        usd=breakdown.total_usd,
        provider=price.provider,
        model=price.model,
        billing_kind=billing,
        catalog_version=catalog.version,
        catalog_as_of=catalog.as_of,
        price_scope=price.price_scope,
        breakdown=breakdown,
        limitations=_catalog_limitations(price),
        base_model_only=price.base_model_only,
    )


def estimate_usage_cost(
    model_usages: Iterable[object],
    *,
    billing_kind: BillingKind | str | None = BillingKind.UNKNOWN,
    catalog: PriceCatalog | None = None,
) -> CostEstimate:
    """Price model totals atomically; one unknown row withholds the whole sum."""

    selected_catalog = catalog or DEFAULT_PRICE_CATALOG
    billing = normalize_billing_kind(billing_kind)
    items = tuple(model_usages)
    if not items:
        # Use the model estimator's billing gate without inventing a model.
        if billing is not BillingKind.API:
            return estimate_model_cost(
                "",
                "",
                object(),
                billing_kind=billing,
                catalog=selected_catalog,
            )
        return _unavailable(
            provider="",
            model="",
            billing_kind=billing,
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.INVALID_USAGE,
            reason="no observed model usage is available to price",
        )

    total = CostBreakdown()
    providers: set[str] = set()
    scopes: set[str] = set()
    limitations: list[str] = []
    base_model_only = False
    for item in items:
        try:
            provider = getattr(item, "provider")
            model = getattr(item, "model")
            tokens = getattr(item, "tokens")
        except Exception as exc:
            return _unavailable(
                provider="",
                model="",
                billing_kind=billing,
                catalog=selected_catalog,
                reason_code=CostUnavailableReason.INVALID_USAGE,
                reason=f"invalid model usage row: {exc}",
            )
        estimate = estimate_model_cost(
            provider,
            model,
            tokens,
            billing_kind=billing,
            catalog=selected_catalog,
        )
        if not estimate.available:
            return CostEstimate(
                usd=None,
                provider=estimate.provider,
                model=estimate.model,
                billing_kind=billing,
                catalog_version=selected_catalog.version,
                catalog_as_of=selected_catalog.as_of,
                reason_code=estimate.reason_code,
                reason=f"{provider}/{model}: {estimate.reason}",
            )
        assert estimate.breakdown is not None
        total = total + estimate.breakdown
        providers.add(estimate.provider)
        if estimate.price_scope:
            scopes.add(estimate.price_scope)
        for limitation in estimate.limitations:
            if limitation not in limitations:
                limitations.append(limitation)
        base_model_only = base_model_only or estimate.base_model_only

    return CostEstimate(
        usd=total.total_usd,
        provider=next(iter(providers)) if len(providers) == 1 else "multiple",
        model="multiple" if len(items) > 1 else str(getattr(items[0], "model")),
        billing_kind=billing,
        catalog_version=selected_catalog.version,
        catalog_as_of=selected_catalog.as_of,
        price_scope=next(iter(scopes)) if len(scopes) == 1 else "mixed_catalog_rates",
        breakdown=total,
        limitations=tuple(limitations),
        base_model_only=base_model_only,
    )


def estimate_report_cost(
    report: object,
    *,
    billing_kind: BillingKind | str | None = BillingKind.UNKNOWN,
    catalog: PriceCatalog | None = None,
) -> CostEstimate:
    """Convenience wrapper for a duck-typed UsageReport.model_totals."""

    try:
        models = getattr(report, "model_totals")
        coverages = tuple(getattr(report, "coverages", ()))
    except Exception as exc:
        selected_catalog = catalog or DEFAULT_PRICE_CATALOG
        return _unavailable(
            provider="",
            model="",
            billing_kind=normalize_billing_kind(billing_kind),
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.INVALID_USAGE,
            reason=f"invalid usage report: {exc}",
        )
    if any(bool(getattr(coverage, "is_partial", True)) for coverage in coverages):
        selected_catalog = catalog or DEFAULT_PRICE_CATALOG
        return _unavailable(
            provider="",
            model="",
            billing_kind=normalize_billing_kind(billing_kind),
            catalog=selected_catalog,
            reason_code=CostUnavailableReason.PARTIAL_SCAN,
            reason="partial retained-history scans are never priced",
        )
    return estimate_usage_cost(
        models,
        billing_kind=billing_kind,
        catalog=catalog,
    )


def make_cost_estimator(
    *,
    billing_kind: BillingKind | str | None,
    catalog: PriceCatalog | None = None,
) -> Callable[..., CostEstimate]:
    """Create the small callable hook used by the cumulative-usage service."""

    selected_catalog = catalog or DEFAULT_PRICE_CATALOG

    configured_billing = billing_kind

    def estimate(
        provider: str,
        model: str,
        tokens: object,
        *,
        billing_kind: BillingKind | str | None = configured_billing,
    ) -> CostEstimate:
        return estimate_model_cost(
            provider,
            model,
            tokens,
            billing_kind=billing_kind,
            catalog=selected_catalog,
        )

    return estimate


def _build_default_catalog() -> PriceCatalog:
    models: list[ModelPrice] = []
    for row in CATALOG_ROWS:
        provider = str(row["provider"])
        rates = TokenRates(
            input=row["input"],
            output=row["output"],
            cached_input=row["cached_input"],
            cache_write=row["cache_write"],
            cache_write_5m=row["cache_write_5m"],
            cache_write_1h=row["cache_write_1h"],
        )
        models.append(
            ModelPrice(
                provider=provider,
                model=str(row["model"]),
                aliases=tuple(str(item) for item in row["aliases"]),
                rates=rates,
                price_scope=str(row["price_scope"]),
                source_url=PRICE_SOURCE_URLS[provider],
                base_model_only=bool(row["base_model_only"]),
            )
        )
    return PriceCatalog(
        version=PRICE_CATALOG_VERSION,
        as_of=date.fromisoformat(PRICE_CATALOG_DATE),
        unit_tokens=PRICE_UNIT_TOKENS,
        models=tuple(models),
        source_urls=PRICE_SOURCE_URLS,
    )


DEFAULT_PRICE_CATALOG = _build_default_catalog()
PRICE_CATALOG_AS_OF = DEFAULT_PRICE_CATALOG.as_of


__all__ = [
    "BillingKind",
    "CostBreakdown",
    "CostEstimate",
    "CostUnavailableReason",
    "DEFAULT_PRICE_CATALOG",
    "ModelPrice",
    "PRICE_CATALOG_AS_OF",
    "PRICE_CATALOG_VERSION",
    "PriceCatalog",
    "TokenRates",
    "estimate_api_equivalent_cost",
    "estimate_model_cost",
    "estimate_report_cost",
    "estimate_usage_cost",
    "format_estimated_cost",
    "make_cost_estimator",
    "normalize_billing_kind",
    "normalize_model",
    "normalize_provider",
]
