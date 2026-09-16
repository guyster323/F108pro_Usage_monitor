from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from quotadeck.config import (
    AppConfig,
    CursorUsageBinding,
    load_config,
    save_config,
)
from quotadeck.core.models import AccountRef
from quotadeck.usage.collectors import CollectorSettings
from quotadeck.usage.cursor_export import collect_cursor_usage
from quotadeck.secrets.store import (
    MemorySecretStore,
    load_cursor_admin_key,
    store_cursor_admin_key,
)
from quotadeck.usage.cursor_import import (
    bind_imported_cursor_csv,
    disconnect_cursor_account,
    disconnect_cursor_csv,
    import_cursor_csv,
    imported_cursor_csv_path,
)
from quotadeck.usage.service import CumulativeUsageService, UsageService


_V1_CSV = """Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you
2025-02-01,gpt-4o,10,5,0,15,30,$0.10,$0.10
2025-02-02,gpt-4o-mini,0,0,0,5,5,$0.05,$0.05
"""


def _history_csv(path: Path, *, days: int = 12) -> Path:
    today = date.today()
    lines = [
        "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),"
        "Cache Read,Output Tokens,Total Tokens,Cost,Cost to you"
    ]
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        lines.append(f"{day.isoformat()},grok-4.6,1000,1000,0,500,1500,$0.25,$0.10")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_gui_csv_import_does_not_need_env_or_rename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("QUOTADECK_CURSOR_EXPORT", raising=False)
    monkeypatch.delenv("QUOTADECK_CURSOR_USAGE_CSV_ACCOUNT", raising=False)
    source = tmp_path / "Cursor usage.csv"
    source.write_text(_V1_CSV, encoding="utf-8")
    stored = import_cursor_csv(source, "work", root=tmp_path)
    assert stored == imported_cursor_csv_path("work", root=tmp_path)
    assert stored.is_file()
    config = bind_imported_cursor_csv(AppConfig(), "work", stored)
    assert config.active_cursor_source("work") == "csv"
    settings = CollectorSettings.from_config(config)
    attempt = collect_cursor_usage(account="work", settings=settings)
    assert attempt.usable
    assert attempt.dataset is not None
    assert attempt.dataset.observations[0].tokens.input_tokens == 5


def test_invalid_csv_is_rejected_before_copy(tmp_path: Path) -> None:
    source = tmp_path / "notes.csv"
    source.write_text("hello,world\n1,2\n", encoding="utf-8")
    try:
        import_cursor_csv(source, "work", root=tmp_path)
    except ValueError as exc:
        assert "date" in str(exc).casefold() or "csv" in str(exc).casefold()
    else:
        raise AssertionError("invalid CSV must be rejected")
    assert not imported_cursor_csv_path("work", root=tmp_path).exists()


def test_cursor_binding_survives_save_load_without_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(
        cursor_bindings=[
            CursorUsageBinding(
                account_id="work",
                source="csv",
                csv_path=str(tmp_path / "usage.csv"),
                admin_email="dev@company.com",
            )
        ]
    )
    save_config(config, path)
    raw = path.read_text(encoding="utf-8")
    assert "dev@company.com" in raw
    assert "api" not in raw.casefold() or "admin_api" in raw
    loaded = load_config(path)
    assert loaded.cursor_binding("work") is not None
    assert loaded.active_cursor_source("work") == "csv"


def test_disconnect_removes_imported_csv_and_binding(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text(_V1_CSV, encoding="utf-8")
    stored = import_cursor_csv(source, "work", root=tmp_path)
    config = bind_imported_cursor_csv(AppConfig(), "work", stored)
    config = disconnect_cursor_csv(config, "work", root=tmp_path)
    assert config.active_cursor_source("work") == ""
    assert not stored.exists()


def test_gui_csv_enables_cumulative_card_and_invalidates_cache(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("QUOTADECK_CURSOR_EXPORT", raising=False)
    export = _history_csv(tmp_path / "download.csv")
    stored = import_cursor_csv(export, "work", root=tmp_path)
    config = bind_imported_cursor_csv(AppConfig(), "work", stored)
    settings = CollectorSettings.from_config(config)
    service = CumulativeUsageService()
    service.loader.collector = settings
    account = AccountRef(
        provider="cursor",
        account_id="work",
        display_name="WORK",
        source_path=str(tmp_path / "state.vscdb"),
    )
    card = service.snapshot(account)
    assert card.available
    assert card.source_label == "CURSOR CSV"
    service.invalidate()
    assert len(service.loader.cache) == 0


def test_admin_source_is_not_blended_with_gui_csv(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text(_V1_CSV, encoding="utf-8")
    stored = import_cursor_csv(source, "work", root=tmp_path)
    config = bind_imported_cursor_csv(AppConfig(), "work", stored)
    config.cursor_bindings[0].source = "admin_api"
    settings = CollectorSettings.from_config(config)
    assert settings.admin_enabled_for("work")
    from quotadeck.secrets.store import MemorySecretStore

    store = MemorySecretStore()
    attempt = collect_cursor_usage(
        account="work",
        settings=settings,
        store=store,
        admin_root=tmp_path,
        email="dev@company.com",
        force_admin=True,
    )
    assert not attempt.usable
    assert attempt.name.value == "cursor-admin"
    assert attempt.dataset is None or all(
        item.source_kind.value == "cursor_admin"
        for item in attempt.dataset.observations
    )


def test_csv_bind_replaces_admin_source(tmp_path: Path) -> None:
    source = tmp_path / "usage.csv"
    source.write_text(_V1_CSV, encoding="utf-8")
    stored = import_cursor_csv(source, "work", root=tmp_path)
    config = AppConfig(
        cursor_bindings=[
            CursorUsageBinding(
                account_id="work",
                source="admin_api",
                admin_email="dev@company.com",
            )
        ]
    )
    config = bind_imported_cursor_csv(config, "work", stored)
    assert config.active_cursor_source("work") == "csv"
    settings = CollectorSettings.from_config(config)
    assert not settings.admin_enabled_for("work")
    assert settings.gui_csv_for("work") == stored


def test_csv_only_disconnect_keeps_admin_key_and_other_account(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("keep-this-admin-key", store=store)
    source = tmp_path / "usage.csv"
    source.write_text(_V1_CSV, encoding="utf-8")
    stored = import_cursor_csv(source, "work", root=tmp_path)
    config = bind_imported_cursor_csv(AppConfig(), "work", stored)
    config.cursor_bindings.append(
        CursorUsageBinding(account_id="home", source="admin_api", admin_email="home@x.com")
    )
    config = disconnect_cursor_csv(config, "work", root=tmp_path)
    assert config.active_cursor_source("work") == ""
    assert config.active_cursor_source("home") == "admin_api"
    assert load_cursor_admin_key(store=store) == "keep-this-admin-key"
    assert not stored.exists()


def test_full_admin_disconnect_removes_binding_cache_and_last_key(tmp_path: Path) -> None:
    from quotadeck.usage.cursor_admin import (
        admin_cache_path,
        collect_cursor_admin,
    )

    store = MemorySecretStore()
    store_cursor_admin_key("last-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {
                "pagination": {"hasNextPage": False},
                "usageEvents": [
                    {
                        "timestamp": "1750979225854",
                        "userEmail": "dev@company.com",
                        "conversationId": "conv-1",
                        "model": "claude-4.5-sonnet",
                        "isTokenBasedCall": True,
                        "tokenUsage": {
                            "inputTokens": 3,
                            "outputTokens": 1,
                            "cacheWriteTokens": 0,
                            "cacheReadTokens": 0,
                        },
                    }
                ],
            }

    collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=lambda *_a, **_k: _Resp(),
        force=True,
    )
    cache = admin_cache_path("work", root=tmp_path)
    assert cache.is_file()
    config = AppConfig(
        cursor_bindings=[
            CursorUsageBinding(account_id="work", source="admin_api", admin_email="dev@company.com"),
            CursorUsageBinding(account_id="home", source="admin_api"),
        ]
    )
    config = disconnect_cursor_account(config, "work", root=tmp_path, store=store)
    assert config.cursor_binding("work") is None
    assert config.active_cursor_source("home") == "admin_api"
    assert not cache.exists()
    assert load_cursor_admin_key(store=store) == "last-admin-key"

    config = disconnect_cursor_account(config, "home", root=tmp_path, store=store)
    assert config.cursor_bindings == []
    assert load_cursor_admin_key(store=store) is None


def test_usage_service_invalidates_cursor_cache(tmp_path: Path) -> None:
    export = _history_csv(tmp_path / "usage.work.csv")
    settings = CollectorSettings(cursor_export_path=export)
    service = UsageService(collector=settings)
    first = service.load("cursor", tmp_path, account_id="work")
    assert first.available
    service.invalidate()
    assert len(service.cache) == 0
