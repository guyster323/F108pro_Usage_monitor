from __future__ import annotations

import logging
from pathlib import Path

from quotadeck.config import AppConfig, CursorUsageBinding, save_config
from quotadeck.core.mask import mask_text
from quotadeck.diagnostics import RedactingFormatter
from quotadeck.secrets.store import (
    MemorySecretStore,
    delete_cursor_admin_key,
    load_cursor_admin_key,
    store_cursor_admin_key,
)
from quotadeck.usage.collectors import CollectorSettings
from quotadeck.usage.cursor_admin import collect_cursor_admin


SECRET = "super-secret-admin-key-VALUE-9f3c2a1b"


class FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200

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


def test_admin_key_is_absent_from_config(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key(SECRET, store=store)
    path = tmp_path / "config.json"
    save_config(
        AppConfig(
            cursor_bindings=[
                CursorUsageBinding(
                    account_id="work",
                    source="admin_api",
                    admin_email="dev@company.com",
                )
            ]
        ),
        path,
    )
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "super-secret" not in text
    assert load_cursor_admin_key(store=store) == SECRET
    delete_cursor_admin_key(store=store)
    assert load_cursor_admin_key(store=store) is None


def test_admin_key_and_raw_body_are_absent_from_logs(tmp_path: Path, caplog) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key(SECRET, store=store)
    logger = logging.getLogger("quotadeck.usage.cursor_admin")
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger.addHandler(handler)
    caplog.set_level(logging.INFO, logger="quotadeck.usage.cursor_admin")
    try:
        collect_cursor_admin(
            account="work",
            email="dev@company.com",
            settings=CollectorSettings(cursor_admin_accounts=("work",)),
            store=store,
            root=tmp_path,
            poster=lambda *_args, **_kwargs: FakeResponse(),
            force=True,
        )
    finally:
        logger.removeHandler(handler)
    combined = "\n".join(item.getMessage() for item in caplog.records)
    combined += "\n" + mask_text(f"Authorization: Basic {SECRET} api_key={SECRET}")
    assert SECRET not in combined
    cache = (tmp_path / "cursor-admin").joinpath("work.json")
    if cache.is_file():
        assert SECRET not in cache.read_text(encoding="utf-8")
    gate = tmp_path / "cursor-admin" / "sync-gate.json"
    if gate.is_file():
        assert SECRET not in gate.read_text(encoding="utf-8")


def test_windows_credential_ctypes_boundary_and_safe_round_trip() -> None:
    import os
    import uuid

    import pytest

    from quotadeck.secrets.store import WindowsCredentialStore

    store = WindowsCredentialStore()
    try:
        store._require_windows()
        advapi32, credential_type = store._api()
    except OSError as exc:
        pytest.skip(f"Windows Credential Manager ctypes boundary unavailable: {exc}")
    assert hasattr(advapi32, "CredWriteW")
    assert hasattr(advapi32, "CredReadW")
    assert hasattr(advapi32, "CredDeleteW")
    fields = {name for name, _type in credential_type._fields_}
    assert {"TargetName", "CredentialBlob", "Type", "Persist", "UserName"} <= fields

    target = f"QuotaDeck/CursorAdminAPI/pytest-{uuid.uuid4().hex}"
    secret = f"pytest-only-{uuid.uuid4().hex}"
    try:
        store.set(target, secret)
        assert store.get(target) == secret
        assert SECRET not in (store.get(target) or "")
    except OSError as exc:
        pytest.skip(f"Windows Credential Manager is not writable here: {exc}")
    finally:
        store.delete(target)
        assert store.get(target) is None
