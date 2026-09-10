from __future__ import annotations

import ssl

import httpx


def _client(verify: ssl.SSLContext | bool) -> httpx.Client:
    return httpx.Client(timeout=20.0, verify=verify, follow_redirects=True)

def request(method: str, url: str, **kwargs) -> httpx.Response:
    """HTTP helper.

    Retries once with the system trust store (instead of certifi) when a
    corporate SSL intercept breaks certificate verification. Verification is
    never disabled: credentials must not travel over an unverified channel.
    """
    try:
        with _client(True) as client:
            return client.request(method, url, **kwargs)
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc) and "SSL" not in str(exc):
            raise
        first_error = exc
    ctx = ssl.create_default_context()
    try:
        with _client(ctx) as client:
            return client.request(method, url, **kwargs)
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in str(exc):
            raise httpx.ConnectError(
                f"TLS verification failed with both certifi and the system trust store: {exc}. "
                "If you are behind a corporate proxy, add its CA to the Windows certificate store."
            ) from first_error
        raise

def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return request("POST", url, **kwargs)
