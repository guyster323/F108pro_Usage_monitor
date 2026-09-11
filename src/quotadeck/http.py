from __future__ import annotations

import httpx


def _client() -> httpx.Client:
    # Keep certificate verification mandatory for every authenticated request.
    # httpx still honours the platform trust configuration and SSL_CERT_FILE.
    return httpx.Client(timeout=20.0, verify=True, follow_redirects=True)

def request(method: str, url: str, **kwargs) -> httpx.Response:
    """Send an HTTPS request while failing closed on TLS verification errors."""

    with _client() as client:
        return client.request(method, url, **kwargs)

def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return request("POST", url, **kwargs)
