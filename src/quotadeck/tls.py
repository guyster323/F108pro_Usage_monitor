"""TLS verification helpers that never disable certificate checks.

httpx's default ``verify=True`` uses certifi. Corporate TLS inspection and
some Windows enterprise roots are only in the OS trust store, so QuotaDeck
also loads those certificates and honours an explicit PEM bundle.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

CA_BUNDLE_ENV = "QUOTADECK_CA_BUNDLE"
_SSL_CERT_FILE_ENV = "SSL_CERT_FILE"
_SSL_CERT_DIR_ENV = "SSL_CERT_DIR"


def resolve_ca_bundle() -> Path | None:
    """Return the first existing explicit CA bundle path, if configured."""

    for key in (CA_BUNDLE_ENV, _SSL_CERT_FILE_ENV):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        path = Path(raw)
        if path.is_file():
            return path
    return None


def resolve_ca_directory() -> Path | None:
    raw = os.environ.get(_SSL_CERT_DIR_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _load_windows_store(ctx: ssl.SSLContext) -> None:
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return
    for store in ("CA", "ROOT"):
        try:
            entries = ssl.enum_certificates(store)
        except OSError:
            continue
        for item in entries:
            try:
                cert, encoding, _trust = item
            except (TypeError, ValueError):
                continue
            if encoding != "x509_asn" or not cert:
                continue
            try:
                ctx.load_verify_locations(cadata=cert)
            except ssl.SSLError:
                continue


def _load_extra_capath(ctx: ssl.SSLContext) -> None:
    directory = resolve_ca_directory()
    if directory is None:
        return
    try:
        ctx.load_verify_locations(capath=str(directory))
    except OSError:
        return


def tls_verify() -> ssl.SSLContext:
    """Return a verifying SSL context. Verification cannot be turned off."""

    ctx = ssl.create_default_context()
    bundle = resolve_ca_bundle()
    if bundle is not None:
        ctx.load_verify_locations(cafile=str(bundle))
    _load_extra_capath(ctx)
    _load_windows_store(ctx)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def tls_is_disabled(verify: object) -> bool:
    return verify is False
