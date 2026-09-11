from __future__ import annotations

import json
from pathlib import Path

import pytest

from quotadeck.providers.grok.auth import OIDC_SCOPE, read_grok_auth
from quotadeck.providers.grok.provider import GrokProvider, _has_retained_sessions


def _set_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_CONFIG", raising=False)
    monkeypatch.delenv("GROK_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CUSTOM_MODEL_KEY", raising=False)


def test_xai_api_key_account_is_discovered_without_copying_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    secret = "xai-test-never-retain"
    monkeypatch.setenv("XAI_API_KEY", secret)

    first = GrokProvider().discover()
    second = GrokProvider().discover()

    assert len(first) == 1
    assert first[0].plan == "api"
    assert first[0].extra["auth_mode"] == "api_key"
    assert first[0].source_path == str(tmp_path)
    assert first[0].account_id == second[0].account_id
    assert secret not in repr(first[0])


def test_whitespace_xai_api_key_does_not_invent_account(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", " \t ")

    assert GrokProvider().discover() == []


def test_active_session_auth_wins_over_xai_api_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    session_secret = "oauth-session-secret"
    fallback_secret = "xai-fallback-secret"
    monkeypatch.setenv("XAI_API_KEY", fallback_secret)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                OIDC_SCOPE: {
                    "auth_mode": "oidc",
                    "key": session_secret,
                    "user_id": "user-123",
                    "email": "demo@example.com",
                    "expires_at": "2999-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    auth = read_grok_auth()
    assert auth is not None
    assert session_secret not in repr(auth)

    accounts = GrokProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].account_id == "user-123"
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "session"
    assert fallback_secret not in repr(accounts[0])
    assert session_secret not in repr(accounts[0])


def test_malformed_grok_identity_fields_do_not_crash_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                OIDC_SCOPE: {
                    "auth_mode": "oidc",
                    "key": "session-secret",
                    "user_id": "user-123",
                    "email": {"unexpected": "object"},
                    "expires_at": "2999-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    auth = read_grok_auth()
    accounts = GrokProvider().discover()

    assert auth is not None and auth.email is None
    assert len(accounts) == 1
    assert accounts[0].account_id == "user-123"


def test_expired_session_does_not_mask_xai_api_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    secret = "xai-fallback-after-expiry"
    monkeypatch.setenv("XAI_API_KEY", secret)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                OIDC_SCOPE: {
                    "auth_mode": "oidc",
                    "key": "expired-session-secret",
                    "user_id": "old-user",
                    "expires_at": "2000-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    accounts = GrokProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].plan == "api"
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert accounts[0].account_id != "old-user"
    assert secret not in repr(accounts[0])


def test_per_model_inline_key_makes_session_billing_ambiguous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    inline_secret = "model-inline-never-retain"
    (tmp_path / "config.toml").write_text(
        '[model."custom"]\napi_key = "model-inline-never-retain"\n',
        encoding="utf-8",
    )
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                OIDC_SCOPE: {
                    "auth_mode": "oidc",
                    "key": "session-never-retain",
                    "user_id": "user-123",
                    "expires_at": "2999-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    account = GrokProvider().discover()[0]
    assert account.plan is None
    assert account.extra["auth_mode"] == "ambiguous"
    assert account.account_id != "user-123"
    assert inline_secret not in repr(account)
    snapshot = GrokProvider().fetch(account)
    assert snapshot.status == "offline"
    assert "ambiguous" in (snapshot.error or "")


def test_per_model_env_key_counts_only_when_environment_value_is_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    (tmp_path / "config.toml").write_text(
        '[model."custom"]\nenv_key = "CUSTOM_MODEL_KEY"\n',
        encoding="utf-8",
    )
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                OIDC_SCOPE: {
                    "auth_mode": "oidc",
                    "key": "session-never-retain",
                    "user_id": "user-123",
                    "expires_at": "2999-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    assert GrokProvider().discover()[0].extra["auth_mode"] == "session"
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "model-env-never-retain")
    account = GrokProvider().discover()[0]
    assert account.extra["auth_mode"] == "ambiguous"
    assert "model-env-never-retain" not in repr(account)


def test_api_key_quota_fetch_is_explicitly_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    secret = "xai-test-never-send-to-subscription-endpoint"
    monkeypatch.setenv("XAI_API_KEY", secret)
    monkeypatch.setattr(
        "quotadeck.providers.grok.provider.fetch_grok",
        lambda auth: (_ for _ in ()).throw(AssertionError("quota endpoint called")),
    )
    account = GrokProvider().discover()[0]

    snapshot = GrokProvider().fetch(account)

    assert snapshot.status == "offline"
    assert snapshot.plan == "api"
    assert snapshot.windows == []
    assert snapshot.error == "Quota data is unavailable for API-key accounts"
    assert secret not in repr(snapshot)


def test_retained_grok_build_sessions_are_discovered_without_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(tmp_path, monkeypatch)
    summary = (
        tmp_path
        / "sessions"
        / "encoded-cwd"
        / "019e0f4b-f861-7c70-8000-000000000001"
        / "summary.json"
    )
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")

    accounts = GrokProvider().discover()

    assert len(accounts) == 1
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "local_history"
    assert accounts[0].source_path == str(tmp_path)


def test_retained_session_probe_stops_at_entry_budget(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    (sessions / "decoy").mkdir(parents=True)
    summary = sessions / "target" / "session-1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")

    assert not _has_retained_sessions(sessions, max_entries=1)
    assert _has_retained_sessions(sessions, max_entries=10)


def test_retained_session_probe_does_not_follow_directory_symlinks(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    outside_summary = tmp_path / "outside" / "session-1" / "summary.json"
    outside_summary.parent.mkdir(parents=True)
    outside_summary.write_text("{}", encoding="utf-8")
    try:
        (sessions / "linked").symlink_to(
            outside_summary.parent.parent,
            target_is_directory=True,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("directory symlinks require SeCreateSymbolicLinkPrivilege")
        raise

    assert not _has_retained_sessions(sessions, max_entries=10)
