from __future__ import annotations

import httpx
import pytest

from quotadeck import http


def test_tls_verification_failure_never_retries_insecurely(monkeypatch) -> None:
    verify_values: list[object] = []

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            verify_values.append(kwargs.get("verify"))

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, *args, **kwargs):
            raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(http.httpx, "Client", FailingClient)

    with pytest.raises(httpx.ConnectError):
        http.get("https://example.invalid")

    assert verify_values == [True]
