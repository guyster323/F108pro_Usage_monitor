"""Classify provider usage-fetch failures without collapsing them to stale."""

from __future__ import annotations

from enum import Enum


class FetchFailureKind(str, Enum):
    UNAUTHORIZED = "unauthorized"
    TLS = "tls"
    HTTP = "http"
    NETWORK = "network"
    OTHER = "other"


class UsageFetchError(RuntimeError):
    """A usage API failure that preserves every attempted endpoint."""

    def __init__(
        self,
        message: str,
        *,
        kind: FetchFailureKind,
        attempts: list[str],
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = list(attempts)


TLS_ACTION = (
    "Usage fetch failed TLS verification. Install the corporate CA in the OS "
    "trust store or set QUOTADECK_CA_BUNDLE (or SSL_CERT_FILE) to a PEM bundle. "
    "TLS verification is never disabled."
)

_TLS_MARKERS = (
    "certificate",
    "certifi",
    "ssl",
    "tls",
    "certificate_verify_failed",
    "self signed",
    "self-signed",
    "unknown ca",
    "unable to get local issuer",
    "untrusted",
)


def classify_exception(exc: BaseException) -> FetchFailureKind:
    text = f"{type(exc).__name__} {exc}".casefold()
    if any(marker in text for marker in _TLS_MARKERS):
        return FetchFailureKind.TLS
    if "connect" in text or "timeout" in text or "network" in text:
        return FetchFailureKind.NETWORK
    if "http" in type(exc).__name__.casefold():
        return FetchFailureKind.HTTP
    return FetchFailureKind.OTHER


def combine_attempts(attempts: list[str], *, kind: FetchFailureKind) -> str:
    detail = " | ".join(item for item in attempts if item)
    if kind is FetchFailureKind.TLS:
        return f"{TLS_ACTION} ({detail})" if detail else TLS_ACTION
    if kind is FetchFailureKind.UNAUTHORIZED:
        return detail or "Usage API unauthorized — sign in again"
    return detail or "Usage fetch failed"


def dominant_kind(kinds: list[FetchFailureKind]) -> FetchFailureKind:
    if not kinds:
        return FetchFailureKind.OTHER
    if FetchFailureKind.UNAUTHORIZED in kinds and all(
        item is FetchFailureKind.UNAUTHORIZED for item in kinds
    ):
        return FetchFailureKind.UNAUTHORIZED
    if FetchFailureKind.TLS in kinds:
        return FetchFailureKind.TLS
    if FetchFailureKind.UNAUTHORIZED in kinds:
        return FetchFailureKind.UNAUTHORIZED
    return kinds[-1]
