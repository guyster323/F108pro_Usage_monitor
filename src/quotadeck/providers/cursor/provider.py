from __future__ import annotations

from datetime import datetime, timezone

from quotadeck.core.mask import email_local
from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.cursor.api import fetch_cursor
from quotadeck.providers.cursor.statedb import discover_cursor_auths, load_cursor_auth_for


class CursorProvider(Provider):
    id = "cursor"

    def discover(self) -> list[AccountRef]:
        accounts: list[AccountRef] = []
        for auth in discover_cursor_auths():
            local = email_local(auth.email) or "CURSOR"
            accounts.append(
                AccountRef(
                    provider="cursor",
                    account_id=auth.user_id,
                    display_name=local.upper(),
                    source_path=auth.source,
                    plan=auth.plan,
                    email_local=local,
                    source_kind=auth.source_kind,
                    source_label=auth.source_label,
                )
            )
        return accounts

    def fetch(self, account: AccountRef) -> UsageSnapshot:
        auth = load_cursor_auth_for(account)
        if auth is None:
            return UsageSnapshot(
                provider="cursor",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Cursor is not signed in",
                source_path=account.source_path,
            )
        try:
            snapshot = fetch_cursor(auth)
            snapshot.display_name = account.display_name
            snapshot.account_id = account.account_id
            snapshot.source_path = account.source_path or snapshot.source_path
            return snapshot
        except Exception as exc:
            return UsageSnapshot(
                provider="cursor",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="stale",
                fetched_at=datetime.now(timezone.utc),
                error=str(exc),
                source_path=account.source_path,
            )
