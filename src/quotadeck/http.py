from __future__ import annotations

import httpx

from quotadeck.tls import tls_verify


def _client() -> httpx.Client:
    # Certificate verification is mandatory. tls_verify() adds the OS trust
    # store and an optional QUOTADECK_CA_BUNDLE / SSL_CERT_FILE PEM path.
    return httpx.Client(timeout=20.0, verify=tls_verify(), follow_redirects=True)

def request(method: str, url: str, **kwargs) -> httpx.Response:
    """Send an HTTPS request while failing closed on TLS verification errors."""

    with _client() as client:
        return client.request(method, url, **kwargs)

def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return request("POST", url, **kwargs)
