"""Production-safe USD/KRW lookup with live, cache, then manual fallback.

This module never fetches on import.  Callers inject an HTTP fetcher in tests
and supply an optional on-disk last-known-good cache that stores only rate
metadata.  Unknown rates stay ``None``; they are never coerced to zero.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from enum import Enum
import json
import os
from pathlib import Path
import httpx


FX_CACHE_SCHEMA = "quotadeck.fx.v1"
DEFAULT_USD_KRW_SOURCE_NAME = "api.fiscaldata.treasury.gov"
DEFAULT_USD_KRW_SOURCE_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
    "accounting/od/rates_of_exchange?"
    "fields=country_currency_desc,exchange_rate,record_date&"
    "filter=country_currency_desc:eq:Korea-Won&sort=-record_date&page%5Bsize%5D=1"
)
DEFAULT_FX_TIMEOUT_SECONDS = 5.0
DEFAULT_FX_MAX_BYTES = 64 * 1024
# Treasury publishes this authoritative reporting rate quarterly. Treat a
# quote older than one quarter plus a small publication grace period as stale.
DEFAULT_FX_STALE_AFTER = timedelta(days=100)
MIN_USD_TO_KRW_RATE = Decimal("500")
MAX_USD_TO_KRW_RATE = Decimal("5000")

_SECRET_CACHE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)

FxFetcher = Callable[..., bytes]


class FxFallback(str, Enum):
    """Which step of the fallback chain produced a quote."""

    LIVE = "live"
    LAST_KNOWN_GOOD = "last_known_good"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class FxRateQuote:
    """One USD→KRW quote, or an explicit unknown with provenance."""

    usd_to_krw: Decimal | None
    source: str
    source_url: str = ""
    as_of: date | None = None
    fetched_at: datetime | None = None
    stale: bool = True
    fallback: FxFallback | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.usd_to_krw is not None


def default_fx_cache_path() -> Path:
    """Return the optional last-known-good path without creating directories."""

    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "QuotaDeck" / "fx-rate.json"


def parse_usd_krw_rate(value: object) -> Decimal | None:
    """Return a finite in-range KRW-per-USD rate, or ``None`` when unknown."""

    if value is None or isinstance(value, bool):
        return None
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None
    if not rate.is_finite() or rate <= 0:
        return None
    if rate < MIN_USD_TO_KRW_RATE or rate > MAX_USD_TO_KRW_RATE:
        return None
    return rate


def _unavailable(
    *,
    source: str,
    source_url: str,
    reason: str,
    fallback: FxFallback | None = None,
) -> FxRateQuote:
    return FxRateQuote(
        usd_to_krw=None,
        source=source,
        source_url=source_url,
        reason=reason,
        fallback=fallback,
        stale=True,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_as_of(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return _aware(parsed).date()


def _payload_has_secrets(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, item in payload.items():
            name = str(key).strip().casefold().replace("-", "_")
            if name in _SECRET_CACHE_KEYS:
                return True
            if _payload_has_secrets(item):
                return True
        return False
    if isinstance(payload, list):
        return any(_payload_has_secrets(item) for item in payload)
    return False


def _rate_from_rates_mapping(rates: object) -> Decimal | None:
    if not isinstance(rates, Mapping):
        return None
    for key in ("KRW", "krw"):
        if key in rates:
            return parse_usd_krw_rate(rates[key])
    usd = rates.get("USD") or rates.get("usd")
    if isinstance(usd, Mapping):
        return parse_usd_krw_rate(usd.get("KRW") or usd.get("krw"))
    return None


def parse_fx_payload(
    payload: object,
    *,
    source_url: str = "",
) -> tuple[Decimal, str, date | None] | None:
    """Extract ``(rate, source_name, as_of)`` from a documented public payload."""

    if not isinstance(payload, Mapping) or _payload_has_secrets(payload):
        return None

    schema = str(payload.get("schema") or "")
    if schema == FX_CACHE_SCHEMA:
        rate = parse_usd_krw_rate(payload.get("usd_to_krw"))
        if rate is None:
            return None
        source = str(payload.get("source") or DEFAULT_USD_KRW_SOURCE_NAME)
        return rate, source, _parse_as_of(payload.get("as_of"))

    if payload.get("result") == "success" or "rates" in payload:
        rate = _rate_from_rates_mapping(payload.get("rates"))
        if rate is not None:
            as_of = _parse_as_of(
                payload.get("time_last_update_utc")
                or payload.get("time_last_update_unix")
                or payload.get("date")
            )
            source = str(
                payload.get("provider")
                or payload.get("source")
                or DEFAULT_USD_KRW_SOURCE_NAME
            )
            return rate, source, as_of

    nested_usd = payload.get("usd")
    if isinstance(nested_usd, Mapping):
        rate = parse_usd_krw_rate(nested_usd.get("krw") or nested_usd.get("KRW"))
        if rate is not None:
            return (
                rate,
                str(payload.get("source") or DEFAULT_USD_KRW_SOURCE_NAME),
                _parse_as_of(payload.get("date") or payload.get("as_of")),
            )

    rate = parse_usd_krw_rate(payload.get("usd_to_krw"))
    if rate is not None:
        return (
            rate,
            str(payload.get("source") or DEFAULT_USD_KRW_SOURCE_NAME),
            _parse_as_of(payload.get("as_of") or payload.get("date")),
        )

    rows = payload.get("data")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            label = str(row.get("country_currency_desc") or row.get("country") or "")
            if "korea" not in label.casefold() and "won" not in label.casefold():
                continue
            rate = parse_usd_krw_rate(row.get("exchange_rate") or row.get("usd_to_krw"))
            if rate is None:
                continue
            return (
                rate,
                "api.fiscaldata.treasury.gov",
                _parse_as_of(row.get("record_date")),
            )
    del source_url
    return None


def _decode_json(raw: bytes, *, max_bytes: int) -> object | None:
    if not raw or len(raw) > max_bytes:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError:
        return None


def default_fx_fetcher(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_FX_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_FX_MAX_BYTES,
) -> bytes:
    """GET ``url`` with TLS, a hard timeout, and a body-size cap.

    Response bodies are never logged.  Failures raise ``FxFetchError`` with a
    generic reason so credentials accidentally placed in a URL query stay out
    of diagnostics.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if not str(url).strip().lower().startswith("https://"):
        raise FxFetchError("https_required")
    chunks: list[bytes] = []
    size = 0
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            verify=True,
            follow_redirects=True,
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise FxFetchError("http_error")
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        if int(length) > max_bytes:
                            raise FxFetchError("response_too_large")
                    except (TypeError, ValueError):
                        pass
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FxFetchError("response_too_large")
                    chunks.append(chunk)
    except FxFetchError:
        raise
    except httpx.TimeoutException as exc:
        raise FxFetchError("timeout") from exc
    except httpx.HTTPError as exc:
        raise FxFetchError("http_error") from exc
    return b"".join(chunks)


class FxFetchError(RuntimeError):
    """Bounded FX HTTP failure without a response body."""


def _quote(
    *,
    rate: Decimal,
    source: str,
    source_url: str,
    as_of: date | None,
    fetched_at: datetime | None,
    stale: bool,
    fallback: FxFallback,
    reason: str | None = None,
) -> FxRateQuote:
    return FxRateQuote(
        usd_to_krw=rate,
        source=source,
        source_url=source_url,
        as_of=as_of,
        fetched_at=_aware(fetched_at) if fetched_at is not None else None,
        stale=stale,
        fallback=fallback,
        reason=reason,
    )


def _cache_is_stale(
    fetched_at: datetime | None,
    as_of: date | None,
    *,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    if as_of is not None:
        return now.date() - as_of >= stale_after
    if fetched_at is not None:
        return now - _aware(fetched_at) >= stale_after
    return True


def read_fx_cache(path: Path) -> FxRateQuote | None:
    """Load a last-known-good quote.  Secret-bearing files are ignored."""

    try:
        if not path.is_file() or path.stat().st_size > DEFAULT_FX_MAX_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    payload = _decode_json(raw, max_bytes=DEFAULT_FX_MAX_BYTES)
    if not isinstance(payload, Mapping) or _payload_has_secrets(payload):
        return None
    if str(payload.get("schema") or "") != FX_CACHE_SCHEMA:
        parsed = parse_fx_payload(payload, source_url=str(payload.get("source_url") or ""))
        if parsed is None:
            return None
        rate, source, as_of = parsed
        fetched_at = None
    else:
        rate = parse_usd_krw_rate(payload.get("usd_to_krw"))
        if rate is None:
            return None
        source = str(payload.get("source") or DEFAULT_USD_KRW_SOURCE_NAME)
        as_of = _parse_as_of(payload.get("as_of"))
        fetched_raw = payload.get("fetched_at")
        fetched_at = None
        if isinstance(fetched_raw, str) and fetched_raw.strip():
            try:
                fetched_at = _aware(datetime.fromisoformat(fetched_raw.replace("Z", "+00:00")))
            except ValueError:
                fetched_at = None
    source_url = str(payload.get("source_url") or "") if isinstance(payload, Mapping) else ""
    return _quote(
        rate=rate,
        source=source,
        source_url=source_url,
        as_of=as_of,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        stale=True,
        fallback=FxFallback.LAST_KNOWN_GOOD,
    )


def write_fx_cache(path: Path, quote: FxRateQuote) -> None:
    """Persist a last-known-good quote without secrets or response bodies."""

    if quote.usd_to_krw is None or quote.fetched_at is None:
        return
    payload = {
        "schema": FX_CACHE_SCHEMA,
        "usd_to_krw": str(quote.usd_to_krw),
        "source": quote.source,
        "source_url": quote.source_url,
        "as_of": quote.as_of.isoformat() if quote.as_of else None,
        "fetched_at": _aware(quote.fetched_at).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def _manual_quote(
    manual_rate: object,
    *,
    source_url: str,
) -> FxRateQuote | None:
    rate = parse_usd_krw_rate(manual_rate)
    if rate is None:
        return None
    return _quote(
        rate=rate,
        source="manual",
        source_url=source_url,
        as_of=None,
        fetched_at=None,
        stale=True,
        fallback=FxFallback.MANUAL,
    )


def resolve_usd_krw_rate(
    *,
    fetcher: FxFetcher | None = None,
    cache_path: Path | None = None,
    manual_rate: object = None,
    source_url: str = DEFAULT_USD_KRW_SOURCE_URL,
    timeout_seconds: float = DEFAULT_FX_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_FX_MAX_BYTES,
    stale_after: timedelta = DEFAULT_FX_STALE_AFTER,
    now: datetime | None = None,
) -> FxRateQuote:
    """Resolve USD/KRW as live → last-known-good → explicit manual.

    A missing or invalid value at every step yields ``usd_to_krw=None``.
    """

    selected_url = str(source_url or DEFAULT_USD_KRW_SOURCE_URL)
    clock = _aware(now) if now is not None else datetime.now(timezone.utc)
    fetch = fetcher if fetcher is not None else default_fx_fetcher

    live_reason = "live_unavailable"
    try:
        raw = fetch(
            selected_url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        payload = _decode_json(raw, max_bytes=max_bytes)
        parsed = parse_fx_payload(payload, source_url=selected_url)
        if parsed is not None:
            rate, source, as_of = parsed
            quote = _quote(
                rate=rate,
                source=source,
                source_url=selected_url,
                as_of=as_of,
                fetched_at=clock,
                stale=_cache_is_stale(clock, as_of, now=clock, stale_after=stale_after),
                fallback=FxFallback.LIVE,
            )
            if cache_path is not None:
                write_fx_cache(cache_path, quote)
            return quote
        live_reason = "invalid_live_payload"
    except FxFetchError as exc:
        live_reason = str(exc) or "live_unavailable"
    except (TypeError, ValueError, OSError, httpx.HTTPError):
        live_reason = "live_unavailable"

    if cache_path is not None:
        cached = read_fx_cache(cache_path)
        if cached is not None and cached.usd_to_krw is not None:
            return FxRateQuote(
                usd_to_krw=cached.usd_to_krw,
                source=cached.source,
                source_url=cached.source_url or selected_url,
                as_of=cached.as_of,
                fetched_at=cached.fetched_at,
                stale=True,
                fallback=FxFallback.LAST_KNOWN_GOOD,
                reason=live_reason,
            )

    manual = _manual_quote(manual_rate, source_url=selected_url)
    if manual is not None:
        return FxRateQuote(
            usd_to_krw=manual.usd_to_krw,
            source=manual.source,
            source_url=selected_url,
            as_of=None,
            fetched_at=None,
            stale=True,
            fallback=FxFallback.MANUAL,
            reason=live_reason,
        )

    return _unavailable(
        source=DEFAULT_USD_KRW_SOURCE_NAME,
        source_url=selected_url,
        reason=live_reason,
    )


__all__ = [
    "DEFAULT_FX_MAX_BYTES",
    "DEFAULT_FX_TIMEOUT_SECONDS",
    "DEFAULT_USD_KRW_SOURCE_NAME",
    "DEFAULT_USD_KRW_SOURCE_URL",
    "FX_CACHE_SCHEMA",
    "FxFallback",
    "FxFetchError",
    "FxRateQuote",
    "default_fx_cache_path",
    "default_fx_fetcher",
    "parse_fx_payload",
    "parse_usd_krw_rate",
    "read_fx_cache",
    "resolve_usd_krw_rate",
    "write_fx_cache",
]
