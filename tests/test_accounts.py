from __future__ import annotations

from quotadeck.config import AccountConfig, AppConfig, _account_from_dict
from quotadeck.core.models import AccountRef
from quotadeck.discovery.accounts import select_accounts
from quotadeck.discovery.sources import classify_source

def _ref(provider: str, account_id: str, name: str, path: str = "") -> AccountRef:
    return AccountRef(
        provider=provider,
        account_id=account_id,
        display_name=name,
        source_path=path,
        source_kind="cli",
        source_label=f"{provider} CLI",
    )

def test_classify_cursor_app_vs_cli() -> None:
    kind, label = classify_source(r"C:\Users\me\AppData\Roaming\Cursor\User\globalStorage\state.vscdb", "cursor")
    assert kind == "app"
    assert "App" in label
    kind, label = classify_source(r"C:\Users\me\AppData\Roaming\Cursor\auth.json", "cursor")
    assert kind == "cli"
    assert "CLI" in label

def test_classify_codex_cli() -> None:
    kind, label = classify_source(r"C:\Users\me\.codex", "codex")
    assert kind == "cli"
    assert label == "Codex CLI"


def test_select_accounts_first_run_uses_all() -> None:
    discovered = [_ref("codex", "a", "TEAM"), _ref("cursor", "b", "CURSOR")]
    assert select_accounts(discovered, AppConfig()) == discovered

def test_select_accounts_respects_enabled() -> None:
    discovered = [_ref("codex", "a", "TEAM"), _ref("cursor", "b", "CURSOR")]
    config = AppConfig(
        accounts=[
            AccountConfig("codex", "a", "WORK", True),
            AccountConfig("cursor", "b", "CUR", False),
        ]
    )
    selected = select_accounts(discovered, config)
    assert [item.account_id for item in selected] == ["a"]
    assert selected[0].display_name == "WORK"

def test_select_accounts_empty_when_all_disabled() -> None:
    discovered = [_ref("codex", "a", "TEAM")]
    config = AppConfig(accounts=[AccountConfig("codex", "a", "TEAM", False)])
    assert select_accounts(discovered, config) == []


def test_config_defaults_are_sixty_five_ten() -> None:
    from quotadeck.config import AppConfig, _hold_seconds
    config = AppConfig()
    assert config.poll_seconds == 60
    assert config.scene_hold_seconds == 5
    assert config.min_upload_minutes == 10
    assert _hold_seconds({}) == 5
    assert _hold_seconds({"scene_hold_seconds": 4}) == 4
    assert _hold_seconds({"scene_hold_seconds": 10}) == 10
    assert _hold_seconds({"scene_hold_seconds": 8}) == 8
    assert _hold_seconds({"scene_hold_seconds": 10, "config_version": 2}) == 10


def test_custom_hold_survives_save_load(tmp_path) -> None:
    from quotadeck.config import AppConfig, load_config, save_config

    path = tmp_path / "config.json"
    save_config(AppConfig(scene_hold_seconds=7), path)
    loaded = load_config(path)
    assert loaded.scene_hold_seconds == 7
    assert loaded.config_version == 2


def test_save_always_stamps_current_config_version(tmp_path) -> None:
    import json

    from quotadeck.config import CONFIG_VERSION, AppConfig, save_config

    path = tmp_path / "config.json"
    save_config(AppConfig(scene_hold_seconds=10, config_version=1), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["config_version"] == CONFIG_VERSION

def test_config_ignores_unknown_keys() -> None:
    account = _account_from_dict(
        {
            "provider": "codex",
            "account_id": "x",
            "alias": "X",
            "mystery": True,
        }
    )
    assert account.alias == "X"
    assert account.source_kind == "cli"
