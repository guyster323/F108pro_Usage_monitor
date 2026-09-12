from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quotadeck.usage.fx import (
    DEFAULT_USD_KRW_SOURCE_URL,
    FX_CACHE_SCHEMA,
    FxFallback,
    FxFetchError,
    default_fx_fetcher,
    parse_usd_krw_rate,
    read_fx_cache,
    resolve_usd_krw_rate,
    write_fx_cache,
)


NOW = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)


def _payload_bytes(
    rate: str = "1345.061572",
    *,
    as_of: str = "Fri, 11 Sep 2026 00:02:31 +0000",
) -> bytes:
    return (
        '{"result":"success","provider":"https://www.exchangerate-api.com",'
        '"base_code":"USD","time_last_update_utc":"%s",'
        '"rates":{"KRW":%s}}' % (as_of, rate)
    ).encode("utf-8")


def _live_fetcher(body: bytes):
    def fetch(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        assert url.startswith("https://")
        assert timeout_seconds > 0
        assert max_bytes >= 1
        if len(body) > max_bytes:
            raise FxFetchError("response_too_large")
        return body

    return fetch


def test_importing_fx_does_not_perform_http() -> None:
    import quotadeck.usage.fx as fx

    assert fx.resolve_usd_krw_rate is resolve_usd_krw_rate
    assert DEFAULT_USD_KRW_SOURCE_URL.startswith("https://")


def test_unknown_rate_is_none_never_zero() -> None:
    assert parse_usd_krw_rate(None) is None
    assert parse_usd_krw_rate(0) is None
    assert parse_usd_krw_rate("0") is None
    assert parse_usd_krw_rate("-1") is None
    assert parse_usd_krw_rate("inf") is None
    assert parse_usd_krw_rate("abc") is None
    assert parse_usd_krw_rate("499") is None
    assert parse_usd_krw_rate("5001") is None
    assert parse_usd_krw_rate("1345.061572") == Decimal("1345.061572")


def test_live_quote_records_source_as_of_and_fetched_at() -> None:
    quote = resolve_usd_krw_rate(
        fetcher=_live_fetcher(_payload_bytes()),
        cache_path=None,
        now=NOW,
    )
    assert quote.available
    assert quote.usd_to_krw == Decimal("1345.061572")
    assert quote.fallback is FxFallback.LIVE
    assert quote.stale is False
    assert quote.as_of == date(2026, 9, 11)
    assert quote.fetched_at == NOW
    assert quote.source_url == DEFAULT_USD_KRW_SOURCE_URL
    assert "exchangerate-api.com" in quote.source
    assert quote.reason is None


def test_malformed_or_oversized_live_payload_falls_back_to_cache(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "fx-rate.json"
    write_fx_cache(
        cache,
        resolve_usd_krw_rate(
            fetcher=_live_fetcher(_payload_bytes("1400")),
            cache_path=cache,
            now=NOW - timedelta(hours=2),
        ),
    )

    def exploding_fetcher(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        raise FxFetchError("timeout")

    quote = resolve_usd_krw_rate(
        fetcher=exploding_fetcher,
        cache_path=cache,
        manual_rate="1555",
        now=NOW,
    )
    assert quote.usd_to_krw == Decimal("1400")
    assert quote.fallback is FxFallback.LAST_KNOWN_GOOD
    assert quote.stale is True
    assert quote.as_of == date(2026, 9, 11)
    assert quote.fetched_at is not None
    assert quote.reason == "timeout"


def test_last_known_good_cache_contains_no_secrets(tmp_path: Path) -> None:
    cache = tmp_path / "fx-rate.json"
    quote = resolve_usd_krw_rate(
        fetcher=_live_fetcher(_payload_bytes("1410")),
        cache_path=cache,
        now=NOW,
    )
    text = cache.read_text(encoding="utf-8")
    assert quote.usd_to_krw == Decimal("1410")
    assert FX_CACHE_SCHEMA in text
    assert "api_key" not in text
    assert "token" not in text
    assert "authorization" not in text
    assert "cookie" not in text
    cached = read_fx_cache(cache)
    assert cached is not None
    assert cached.usd_to_krw == Decimal("1410")


def test_secret_bearing_cache_is_ignored_then_manual_is_used(tmp_path: Path) -> None:
    cache = tmp_path / "fx-rate.json"
    cache.write_text(
        '{"schema":"quotadeck.fx.v1","usd_to_krw":"1400","api_key":"sk-secret"}',
        encoding="utf-8",
    )

    def fail(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        raise FxFetchError("http_error")

    quote = resolve_usd_krw_rate(
        fetcher=fail,
        cache_path=cache,
        manual_rate=Decimal("1420"),
        now=NOW,
    )
    assert quote.usd_to_krw == Decimal("1420")
    assert quote.fallback is FxFallback.MANUAL
    assert quote.source == "manual"
    assert quote.stale is True
    assert quote.as_of is None
    assert quote.fetched_at is None


def test_explicit_manual_rate_is_last_fallback_when_live_and_cache_fail(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"

    def fail(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        return b"{"

    quote = resolve_usd_krw_rate(
        fetcher=fail,
        cache_path=missing,
        manual_rate="1400",
        now=NOW,
    )
    assert quote.usd_to_krw == Decimal("1400")
    assert quote.fallback is FxFallback.MANUAL
    assert quote.stale is True


def test_all_sources_unavailable_returns_none_not_zero(tmp_path: Path) -> None:
    def fail(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        raise FxFetchError("timeout")

    quote = resolve_usd_krw_rate(
        fetcher=fail,
        cache_path=tmp_path / "none.json",
        manual_rate=None,
        now=NOW,
    )
    assert quote.usd_to_krw is None
    assert not quote.available
    assert quote.fallback is None
    assert quote.reason == "timeout"


def test_oversized_live_body_does_not_become_a_zero_rate() -> None:
    def huge(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        return b"0" * (max_bytes + 1)

    quote = resolve_usd_krw_rate(
        fetcher=huge,
        cache_path=None,
        manual_rate=None,
        max_bytes=64,
        now=NOW,
    )
    assert quote.usd_to_krw is None
    assert quote.reason == "invalid_live_payload"


def test_treasury_style_payload_is_accepted() -> None:
    body = (
        b'{"data":[{"country_currency_desc":"Korea, South-Won",'
        b'"exchange_rate":"1398.12","record_date":"2026-06-30"}]}'
    )
    quote = resolve_usd_krw_rate(
        fetcher=_live_fetcher(body),
        now=NOW,
    )
    assert quote.usd_to_krw == Decimal("1398.12")
    assert quote.as_of == date(2026, 6, 30)
    assert quote.source == "api.fiscaldata.treasury.gov"


def test_default_fetcher_requires_https_and_tls_verification(monkeypatch) -> None:
    with pytest.raises(FxFetchError, match="https_required"):
        default_fx_fetcher("http://example.invalid/fx")

    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "40"}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def iter_bytes(self):
            yield b'{"usd":{"krw":"1400"},"date":"2026-09-11"}'

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            seen["verify"] = kwargs.get("verify")
            seen["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def stream(self, method: str, url: str):
            seen["method"] = method
            seen["url"] = url
            return FakeResponse()

    monkeypatch.setattr("quotadeck.usage.fx.httpx.Client", FakeClient)
    raw = default_fx_fetcher(
        "https://open.er-api.com/v6/latest/USD",
        timeout_seconds=5.0,
        max_bytes=1024,
    )
    assert seen["verify"] is True
    assert seen["timeout"] == 5.0
    assert seen["method"] == "GET"
    assert b"krw" in raw
