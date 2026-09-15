from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from quotadeck.core.models import AccountRef
from quotadeck.providers.cursor.api import fetch_cursor
from quotadeck.providers.cursor.provider import CursorProvider
from quotadeck.providers.cursor.statedb import CursorAuth
from quotadeck.providers.errors import FetchFailureKind, UsageFetchError


def _auth() -> CursorAuth:
    return CursorAuth(
        access_token="valid-cursor-token",
        email="user@example.com",
        plan="pro",
        user_id="user-1",
        source="cursor-state",
    )


def _account() -> AccountRef:
    return AccountRef(
        provider="cursor",
        account_id="user-1",
        display_name="USER",
        source_path="cursor-state",
        plan="pro",
    )


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://cursor.com"),
                response=httpx.Response(self.status_code),
            )


def test_valid_token_certificate_failure_is_usage_error_not_stale(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr("quotadeck.providers.cursor.api.get", boom)
    monkeypatch.setattr("quotadeck.providers.cursor.api.post", boom)
    monkeypatch.setattr(
        "quotadeck.providers.cursor.provider.load_cursor_auth_for",
        lambda _account: _auth(),
    )

    snapshot = CursorProvider().fetch(_account())
    assert snapshot.status == "error"
    assert snapshot.status != "stale"
    assert "tls" in (snapshot.error or "").lower()
    assert "usage-summary" in (snapshot.error or "")
    assert "GetCurrentPeriodUsage" in (snapshot.error or "")
    assert "QUOTADECK_CA_BUNDLE" in (snapshot.error or "")


def test_endpoint_fallbacks_retain_both_errors(monkeypatch) -> None:
    def fail_summary(*_args, **_kwargs):
        raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    def fail_period(*_args, **_kwargs):
        raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr("quotadeck.providers.cursor.api.get", fail_summary)
    monkeypatch.setattr("quotadeck.providers.cursor.api.post", fail_period)

    with pytest.raises(UsageFetchError) as caught:
        fetch_cursor(_auth())
    assert caught.value.kind is FetchFailureKind.TLS
    assert len(caught.value.attempts) == 2
    assert "usage-summary" in caught.value.attempts[0]
    assert "GetCurrentPeriodUsage" in caught.value.attempts[1]


def test_usage_summary_failure_can_succeed_via_period_usage(monkeypatch) -> None:
    def fail_summary(*_args, **_kwargs):
        raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    payload = {
        "planUsage": {"autoPercentUsed": 10, "apiPercentUsed": 20},
        "billingCycleEnd": "2026-09-30T00:00:00Z",
    }
    monkeypatch.setattr("quotadeck.providers.cursor.api.get", fail_summary)
    monkeypatch.setattr(
        "quotadeck.providers.cursor.api.post",
        lambda *_args, **_kwargs: _Response(200, payload),
    )
    snapshot = fetch_cursor(_auth())
    assert snapshot.status == "ok"
    assert snapshot.windows


def test_offline_when_not_signed_in(monkeypatch) -> None:
    monkeypatch.setattr(
        "quotadeck.providers.cursor.provider.load_cursor_auth_for",
        lambda _account: None,
    )
    snapshot = CursorProvider().fetch(_account())
    assert snapshot.status == "offline"
    assert "not signed in" in (snapshot.error or "").lower()


def test_unauthorized_fallback_is_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        "quotadeck.providers.cursor.api.get",
        lambda *_args, **_kwargs: _Response(401),
    )
    monkeypatch.setattr(
        "quotadeck.providers.cursor.api.post",
        lambda *_args, **_kwargs: _Response(401),
    )
    monkeypatch.setattr(
        "quotadeck.providers.cursor.provider.load_cursor_auth_for",
        lambda _account: _auth(),
    )
    snapshot = CursorProvider().fetch(_account())
    assert snapshot.status == "stale"
    assert "401" in (snapshot.error or "")
