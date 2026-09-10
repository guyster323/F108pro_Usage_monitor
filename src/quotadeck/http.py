from __future__ import annotations

import ssl

import httpx


def _client(verify: ssl.SSLContext | bool) -> httpx.Client:
    return httpx.Client(timeout=20.0, verify=verify, follow_redirects=True)

def request(method: str, url: str, **kwargs) -> httpx.Response:
    """HTTP helper that retries once if a corporate SSL intercept breaks certifi."""
    try:
        with _client(True) as client:
            return client.request(method, url, **kwargs)
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc) and "SSL" not in str(exc):
            raise
    ctx = ssl.create_default_context()
    try:
        with _client(ctx) as client:
            return client.request(method, url, **kwargs)
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc) and "SSL" not in str(exc):
            raise
    with _client(False) as client:
        return client.request(method, url, **kwargs)

def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return request("POST", url, **kwargs)
