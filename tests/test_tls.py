from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from quotadeck import http
from quotadeck.tls import CA_BUNDLE_ENV, resolve_ca_bundle, tls_is_disabled, tls_verify
from quotadeck.usage import fx as fx_mod


def test_tls_verify_never_disables_checks(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / "corp-ca.pem"
    # Minimal self-signed-looking PEM is not required; missing file is ignored.
    monkeypatch.setenv(CA_BUNDLE_ENV, str(tmp_path / "missing.pem"))
    ctx = tls_verify()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert tls_is_disabled(ctx) is False
    assert tls_is_disabled(False) is True


def test_explicit_ca_bundle_is_used(tmp_path, monkeypatch) -> None:
    default = ssl.get_default_verify_paths().cafile
    if default and Path(default).is_file():
        monkeypatch.setenv(CA_BUNDLE_ENV, default)
        assert resolve_ca_bundle() == Path(default)
        ctx = tls_verify()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        return
    bundle = tmp_path / "marker.pem"
    bundle.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv(CA_BUNDLE_ENV, str(bundle))
    assert resolve_ca_bundle() == bundle


def test_source_tree_never_sets_verify_false() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "quotadeck"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0]
            if "verify=False" in stripped or "verify = False" in stripped:
                offenders.append(str(path.relative_to(root.parents[1])))
    assert offenders == []


def test_http_and_fx_clients_use_verifying_context(monkeypatch) -> None:
    seen: list[object] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            seen.append(kwargs.get("verify"))

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, *args, **kwargs):
            raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(http.httpx, "Client", FakeClient)
    monkeypatch.setattr(fx_mod.httpx, "Client", FakeClient)
    try:
        http.get("https://example.invalid")
    except httpx.ConnectError:
        pass
    try:
        fx_mod.default_fx_fetcher("https://example.invalid/fx")
    except fx_mod.FxFetchError:
        pass
    assert seen
    assert False not in seen
