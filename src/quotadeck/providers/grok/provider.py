from __future__ import annotations

from datetime import datetime, timezone

from quotadeck.core.mask import email_local
from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.grok.api import fetch_grok
from quotadeck.providers.grok.auth import read_grok_auth


class GrokProvider(Provider):
    id = "grok"

    def discover(self) -> list[AccountRef]:
        auth = read_grok_auth()
        if auth is None:
            return []
        local = email_local(auth.email) or "GROK"
        return [
            AccountRef(
                provider="grok",
                account_id=auth.user_id,
                display_name=local.upper(),
                source_path=str(auth.path),
                email_local=local,
                source_kind="cli",
                source_label="Grok CLI",
            )
        ]

    def fetch(self, account: AccountRef) -> UsageSnapshot:
        auth = read_grok_auth()
        if auth is None:
            return UsageSnapshot(
                provider="grok",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=None,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Grok Build CLI is not signed in",
                source_path=account.source_path,
            )
        if auth.stale:
            return UsageSnapshot(
                provider="grok",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=None,
                windows=[],
                status="stale",
                fetched_at=datetime.now(timezone.utc),
                error="run `grok` to refresh login",
                source_path=account.source_path,
            )
        try:
            snapshot = fetch_grok(auth)
            snapshot.display_name = account.display_name
            snapshot.account_id = account.account_id
            return snapshot
        except Exception as exc:
            return UsageSnapshot(
                provider="grok",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=None,
                windows=[],
                status="stale" if str(exc) == "stale" else "error",
                fetched_at=datetime.now(timezone.utc),
                error=str(exc),
                source_path=account.source_path,
            )
