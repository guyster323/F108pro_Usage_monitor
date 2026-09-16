"""GUI one-click Cursor Usage CSV import into QuotaDeck AppData.

Users pick a dashboard CSV. QuotaDeck validates it, copies it atomically, and
binds the selected Cursor account in config. File renames and environment
variables are not required.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from quotadeck.config import (
    AppConfig,
    CursorUsageBinding,
    app_dir,
    has_admin_cursor_binding,
    remove_cursor_binding,
    upsert_cursor_binding,
)
from quotadeck.usage.cursor_export import parse_cursor_csv_result

_SAFE_ACCOUNT = re.compile(r"[^A-Za-z0-9._-]+")


def default_cursor_usage_dir(*, root: Path | None = None) -> Path:
    base = root if root is not None else app_dir()
    path = Path(base) / "cursor-usage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_cursor_account_filename(account_id: str) -> str:
    cleaned = _SAFE_ACCOUNT.sub("_", account_id.strip())[:80].strip("._")
    return cleaned or "cursor"


def imported_cursor_csv_path(account_id: str, *, root: Path | None = None) -> Path:
    return default_cursor_usage_dir(root=root) / f"{safe_cursor_account_filename(account_id)}.csv"


def validate_cursor_csv_for_account(path: Path, account_id: str):
    """Validate a user-selected CSV for the chosen Cursor account."""

    return parse_cursor_csv_result(
        Path(path),
        account=account_id,
        bind_generic_account=account_id,
    )


def atomic_copy_file(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.tmp")
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, destination)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def import_cursor_csv(
    source: Path,
    account_id: str,
    *,
    root: Path | None = None,
) -> Path:
    """Validate then atomically copy ``source`` into QuotaDeck AppData."""

    account = account_id.strip()
    if not account:
        raise ValueError("A Cursor account is required.")
    path = Path(source).expanduser()
    result = validate_cursor_csv_for_account(path, account)
    if not result.usable:
        raise ValueError(result.attempt.reason or "Cursor CSV could not be used.")
    return atomic_copy_file(path, imported_cursor_csv_path(account, root=root))


def bind_imported_cursor_csv(
    config: AppConfig,
    account_id: str,
    csv_path: Path,
    *,
    imported_at: str | None = None,
) -> AppConfig:
    stamp = imported_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = config.cursor_binding(account_id)
    return upsert_cursor_binding(
        config,
        CursorUsageBinding(
            account_id=account_id,
            source="csv",
            csv_path=str(csv_path),
            imported_at=stamp,
            admin_email=existing.admin_email if existing else "",
            admin_user_id=existing.admin_user_id if existing else "",
        ),
    )


def _remove_imported_csv_files(
    config: AppConfig,
    account_id: str,
    *,
    root: Path | None = None,
) -> None:
    existing = config.cursor_binding(account_id)
    stored = imported_cursor_csv_path(account_id, root=root)
    candidates: list[Path] = [stored]
    if existing is not None and existing.csv_path:
        candidates.append(Path(existing.csv_path))
    usage_root = default_cursor_usage_dir(root=root).resolve()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(usage_root)
        except ValueError:
            continue
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            continue


def disconnect_cursor_csv(
    config: AppConfig,
    account_id: str,
    *,
    root: Path | None = None,
) -> AppConfig:
    """Clear this account's imported CSV only. Never deletes the Admin API key."""

    existing = config.cursor_binding(account_id)
    _remove_imported_csv_files(config, account_id, root=root)
    if existing is None:
        return config
    if existing.source == "admin_api":
        return upsert_cursor_binding(
            config,
            CursorUsageBinding(
                account_id=existing.account_id,
                source="admin_api",
                csv_path="",
                imported_at="",
                admin_email=existing.admin_email,
                admin_user_id=existing.admin_user_id,
            ),
        )
    return remove_cursor_binding(config, account_id)


def disconnect_cursor_account(
    config: AppConfig,
    account_id: str,
    *,
    root: Path | None = None,
    store=None,
) -> AppConfig:
    """Full disconnect: remove binding, Admin cache/gate, and the key if last."""

    existing = config.cursor_binding(account_id)
    _remove_imported_csv_files(config, account_id, root=root)
    was_admin = existing is not None and existing.source == "admin_api"
    config = remove_cursor_binding(config, account_id)
    if was_admin:
        from quotadeck.secrets.store import delete_cursor_admin_key
        from quotadeck.usage.cursor_admin import clear_admin_account_state

        clear_admin_account_state(account_id, root=root)
        if not has_admin_cursor_binding(config):
            delete_cursor_admin_key(store=store)
    return config


def release_unused_cursor_admin_key(config: AppConfig, *, store=None) -> None:
    if not has_admin_cursor_binding(config):
        from quotadeck.secrets.store import delete_cursor_admin_key

        delete_cursor_admin_key(store=store)


__all__ = [
    "atomic_copy_file",
    "bind_imported_cursor_csv",
    "default_cursor_usage_dir",
    "disconnect_cursor_account",
    "disconnect_cursor_csv",
    "import_cursor_csv",
    "imported_cursor_csv_path",
    "release_unused_cursor_admin_key",
    "safe_cursor_account_filename",
    "validate_cursor_csv_for_account",
]
