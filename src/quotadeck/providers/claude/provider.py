from __future__ import annotations

from datetime import datetime, timezone

from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.claude.api import fetch_claude
from quotadeck.providers.claude.credentials import read_claude_auth


class ClaudeProvider(Provider):
    id = "claude"
    def discover(self) -> list[AccountRef]:
        auth = read_claude_auth()
        if auth is None:
            return []
        return [
            AccountRef(
                provider="claude",
                account_id="claude",
                display_name="CLAUDE",
                source_path=str(auth.path),
                plan=auth.subscription,
                email_local="CLAUDE",
                source_kind="cli",
                source_label="Claude Code",
            )
        ]
    def fetch(self, account: AccountRef) -> UsageSnapshot:
        auth = read_claude_auth()
        if auth is None:
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Claude Code is not signed in",
                source_path=account.source_path,
            )
        if auth.stale or not auth.has_profile_scope:
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="stale",
                fetched_at=datetime.now(timezone.utc),
                error="run `claude` to refresh login",
                source_path=account.source_path,
            )
        try:
            snapshot = fetch_claude(auth)
            snapshot.display_name = account.display_name
            snapshot.account_id = account.account_id
            return snapshot
        except Exception as exc:
            message = str(exc)
            status = "rate_limited" if message.startswith("rate_limited") else "stale" if message == "stale" else "error"
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status=status,  # type: ignore[arg-type]
                fetched_at=datetime.now(timezone.utc),
                error=message,
                source_path=account.source_path,
            )
