from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from quotadeck.core.models import AccountRef
from quotadeck.providers.claude.provider import ClaudeProvider
from quotadeck.providers.codex.auth import discover_codex_accounts, read_auth
from quotadeck.providers.codex.provider import CodexProvider


def _stable_path_digest(path: Path) -> str:
    normalized = str(path.expanduser().resolve())
    return sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]


def _clear_codex_auth_environment(monkeypatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_ENDPOINT",
        "OPENAI_API_TYPE",
        "OPENAI_API_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)


def _clear_claude_auth_environment(monkeypatch) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_codex_history(home: Path, *, archived: bool = False) -> Path:
    folder = "archived_sessions" if archived else "sessions"
    path = home / folder / "2026" / "rollout-test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def test_codex_api_key_auth_is_discovered_without_copying_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    secret = "sk-test-do-not-copy"
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": secret}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    auth = read_auth(tmp_path)
    assert auth is not None
    assert auth.is_api_key_only
    assert auth.plan == "api"
    assert auth.access_token is None
    assert secret not in repr(auth)

    first = discover_codex_accounts()
    second = discover_codex_accounts()
    assert len(first) == 1
    assert first[0].plan == "api"
    assert first[0].extra["auth_mode"] == "api_key"
    assert first[0].account_id == f"home-{_stable_path_digest(tmp_path)}"
    assert second[0].account_id == first[0].account_id
    assert secret not in repr(first[0])


def test_codex_custom_endpoint_is_never_given_openai_list_prices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-custom-do-not-copy"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    accounts = discover_codex_accounts()

    assert len(accounts) == 1
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert accounts[0].extra["pricing_scope"] == "custom"


def test_codex_custom_model_provider_is_never_given_openai_list_prices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-custom-do-not-copy"}),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        'model_provider = "compatible"\n'
        '[model_providers.compatible]\n'
        'base_url = "https://gateway.example/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    accounts = discover_codex_accounts()

    assert accounts[0].extra["pricing_scope"] == "custom"


def test_codex_case_variant_openai_section_cannot_hide_custom_base_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-custom-do-not-copy"}),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        'model_provider = "OPENAI"\n'
        '[model_providers.OPENAI]\n'
        'base_url = "https://gateway.example/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    accounts = discover_codex_accounts()

    assert accounts[0].extra["pricing_scope"] == "custom"


def test_codex_file_api_key_wins_when_auth_file_also_contains_oauth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": "sk-test-do-not-copy",
                "tokens": {"access_token": "oauth-token"},
            }
        ),
        encoding="utf-8",
    )

    auth = read_auth(tmp_path)
    assert auth is not None
    assert auth.is_api_key_only
    assert auth.mode == "api_key"
    assert auth.plan == "api"
    assert auth.access_token is None
    assert auth.id_token is None
    assert auth.refresh_token is None
    assert auth.account_id is None
    assert auth.email is None
    assert "sk-test-do-not-copy" not in repr(auth)


def test_codex_explicit_api_key_mode_wins_over_retained_oauth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "ApiKey",
                "tokens": {"access_token": "retained-oauth-token"},
            }
        ),
        encoding="utf-8",
    )

    auth = read_auth(tmp_path)
    assert auth is not None
    assert auth.is_api_key_only
    assert auth.mode == "api_key"
    assert auth.plan == "api"


def test_codex_process_api_key_wins_over_oauth_auth_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    secret = "sk-process-do-not-copy"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "oauth-token"}}),
        encoding="utf-8",
    )

    auth = read_auth(tmp_path)
    assert auth is not None
    assert auth.is_api_key_only
    assert auth.plan == "api"
    assert secret not in repr(auth)


def test_codex_process_key_discovers_only_explicit_home_with_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    selected = tmp_path / "selected"
    unrelated = tmp_path / "extra"
    _write_codex_history(selected)
    _write_codex_history(unrelated)
    secret = "sk-process-selected-only"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("CODEX_HOME", str(selected))
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [selected, unrelated, selected],
    )

    accounts = discover_codex_accounts()
    assert len(accounts) == 1
    assert accounts[0].source_path == str(selected)
    assert accounts[0].account_id == f"home-{_stable_path_digest(selected)}"
    assert accounts[0].plan == "api"
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert secret not in repr(accounts[0])

    monkeypatch.setattr(
        "quotadeck.providers.codex.provider.fetch_wham",
        lambda auth: (_ for _ in ()).throw(AssertionError("OAuth endpoint called")),
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.provider.fetch_app_server",
        lambda auth: (_ for _ in ()).throw(AssertionError("RPC endpoint called")),
    )
    snapshot = CodexProvider().fetch(accounts[0])
    assert snapshot.status == "offline"
    assert snapshot.plan == "api"
    assert snapshot.error == "Quota data is unavailable for API-key accounts"


def test_codex_process_key_defaults_to_one_home_not_extra_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    default = tmp_path / ".codex"
    extra = tmp_path / ".codex-work"
    _write_codex_history(default)
    _write_codex_history(extra)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-default-only")
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth._environment_api_home",
        lambda: default,
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [extra, default],
    )

    accounts = discover_codex_accounts()
    assert len(accounts) == 1
    assert accounts[0].source_path == str(default)


def test_codex_process_key_does_not_override_other_profile_oauth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    selected.mkdir()
    other.mkdir()
    (selected / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "selected-oauth"}}),
        encoding="utf-8",
    )
    (other / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "other-oauth"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-selected-only")
    monkeypatch.setenv("CODEX_HOME", str(selected))
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [selected, other],
    )

    accounts = discover_codex_accounts()
    by_home = {item.source_path: item for item in accounts}
    assert by_home[str(selected)].extra["auth_mode"] == "api_key"
    assert by_home[str(selected)].account_id == f"home-{_stable_path_digest(selected)}"
    assert by_home[str(other)].extra["auth_mode"] == "oauth"
    assert by_home[str(other)].plan is None


def test_codex_process_key_accepts_archived_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    _write_codex_history(tmp_path, archived=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-archived")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    accounts = discover_codex_accounts()
    assert len(accounts) == 1
    assert accounts[0].source_path == str(tmp_path)
    assert accounts[0].plan == "api"


def test_codex_process_key_without_retained_history_invents_no_account(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-no-history")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        "quotadeck.providers.codex.auth.candidate_homes",
        lambda: [tmp_path],
    )

    assert discover_codex_accounts() == []


def test_codex_api_key_quota_fetch_is_explicitly_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_codex_auth_environment(monkeypatch)
    secret = "sk-test-never-call"
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": secret}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.provider.fetch_wham",
        lambda auth: (_ for _ in ()).throw(AssertionError("OAuth endpoint called")),
    )
    monkeypatch.setattr(
        "quotadeck.providers.codex.provider.fetch_app_server",
        lambda auth: (_ for _ in ()).throw(AssertionError("RPC endpoint called")),
    )
    account = AccountRef(
        provider="codex",
        account_id="local",
        display_name="CODEX",
        source_path=str(tmp_path),
        plan="api",
    )

    snapshot = CodexProvider().fetch(account)
    assert snapshot.status == "offline"
    assert snapshot.plan == "api"
    assert snapshot.windows == []
    assert snapshot.error == "Quota data is unavailable for API-key accounts"
    assert secret not in repr(snapshot)
    assert str(tmp_path) not in (snapshot.error or "")


def test_claude_discovers_local_history_without_oauth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    projects = tmp_path / "projects"
    session = projects / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].account_id == f"local-{_stable_path_digest(projects)}"
    assert accounts[0].source_path == str(projects)
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "local_history"


def test_claude_local_history_is_api_billed_only_when_env_key_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = "sk-ant-test-do-not-copy"
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].plan == "api"
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert secret not in repr(accounts[0])

    snapshot = ClaudeProvider().fetch(accounts[0])
    assert snapshot.status == "offline"
    assert snapshot.error == "Quota data is unavailable for API-key accounts"
    assert secret not in repr(snapshot)
    assert str(tmp_path) not in (snapshot.error or "")


def test_claude_custom_base_url_keeps_local_history_unpriced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-custom-do-not-copy")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert len(accounts) == 1
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert accounts[0].extra["pricing_scope"] == "custom"


def test_claude_settings_gateway_keeps_local_history_unpriced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-custom-do-not-copy")
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example"}}
        ),
        encoding="utf-8",
    )
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert accounts[0].extra["pricing_scope"] == "custom"


@pytest.mark.parametrize(
    "gateway_setting",
    [
        {"forceLoginMethod": "gateway"},
        {"forceLoginGatewayUrl": "https://gateway.example/login"},
    ],
)
def test_claude_forced_login_gateway_keeps_local_history_unpriced(
    tmp_path: Path,
    monkeypatch,
    gateway_setting: dict[str, str],
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-custom-do-not-copy")
    (tmp_path / "settings.json").write_text(
        json.dumps(gateway_setting),
        encoding="utf-8",
    )
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert accounts[0].extra["pricing_scope"] == "custom"


def test_claude_settings_only_api_key_is_discovered_for_local_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_API_KEY": "settings-only-secret"}}),
        encoding="utf-8",
    )
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert len(accounts) == 1
    assert accounts[0].plan == "api"
    assert accounts[0].extra["auth_mode"] == "api_key"
    assert "settings-only-secret" not in repr(accounts[0])


def test_claude_settings_auth_token_makes_process_api_key_ambiguous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-secret")
    (tmp_path / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "settings-secret"}}),
        encoding="utf-8",
    )
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert len(accounts) == 1
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "ambiguous"
    rendered = repr(accounts[0])
    assert "process-secret" not in rendered
    assert "settings-secret" not in rendered


def test_claude_program_files_managed_fragment_gateway_is_unpriced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    config = tmp_path / "profile"
    program_files = tmp_path / "Program Files"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-secret")
    fragments = program_files / "ClaudeCode" / "managed-settings.d"
    fragments.mkdir(parents=True)
    (fragments / "20-gateway.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.example"}}),
        encoding="utf-8",
    )
    session = config / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()

    assert len(accounts) == 1
    assert accounts[0].extra["pricing_scope"] == "custom"


def test_claude_mixed_api_key_and_oauth_is_ambiguous_without_quota_fetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secondary")
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")
    (tmp_path / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "oauth-token",
                    "subscriptionType": "max",
                    "scopes": ["user:profile"],
                }
            }
        ),
        encoding="utf-8",
    )

    accounts = ClaudeProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].account_id == "claude"
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "ambiguous"

    monkeypatch.setattr(
        "quotadeck.providers.claude.provider.fetch_claude",
        lambda auth: (_ for _ in ()).throw(AssertionError("OAuth endpoint called")),
    )
    snapshot = ClaudeProvider().fetch(accounts[0])
    assert snapshot.status == "offline"
    assert snapshot.plan is None
    assert snapshot.error == "Quota data is unavailable while Claude auth mode is ambiguous"


def test_claude_auth_token_context_is_ambiguous_and_unpriced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-do-not-copy")
    session = tmp_path / "projects" / "project-a" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n", encoding="utf-8")

    accounts = ClaudeProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "ambiguous"
    assert "bearer-do-not-copy" not in repr(accounts[0])


def test_claude_cloud_context_with_oauth_does_not_fetch_subscription_quota(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    (tmp_path / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "retained-oauth-token",
                    "subscriptionType": "max",
                    "scopes": ["user:profile"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "quotadeck.providers.claude.provider.fetch_claude",
        lambda auth: (_ for _ in ()).throw(AssertionError("OAuth endpoint called")),
    )

    accounts = ClaudeProvider().discover()
    assert len(accounts) == 1
    assert accounts[0].plan is None
    assert accounts[0].extra["auth_mode"] == "ambiguous"
    snapshot = ClaudeProvider().fetch(accounts[0])
    assert snapshot.status == "offline"
    assert snapshot.plan is None


def test_claude_does_not_invent_account_without_retained_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_claude_auth_environment(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-without-history")

    assert ClaudeProvider().discover() == []
