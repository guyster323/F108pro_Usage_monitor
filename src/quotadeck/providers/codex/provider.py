from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.codex.appserver_rpc import CodexRPCError, fetch_app_server
from quotadeck.providers.codex.auth import (
    discover_codex_accounts,
    process_api_key_applies_to,
    read_auth,
)
from quotadeck.providers.codex.oauth_usage import CodexUsageError, fetch_wham


class CodexProvider(Provider):
    id = "codex"
    def discover(self) -> list[AccountRef]:
        return discover_codex_accounts()
    def fetch(self, account: AccountRef) -> UsageSnapshot:
        home = Path(account.source_path)
        auth = read_auth(
            home,
            include_process_environment=process_api_key_applies_to(home),
        )
        if auth is None:
            return UsageSnapshot(
                provider="codex",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="auth.json missing",
                source_path=account.source_path,
            )
        if auth.is_api_key_only:
            return UsageSnapshot(
                provider="codex",
                account_id=account.account_id,
                display_name=account.display_name,
                plan="api",
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Quota data is unavailable for API-key accounts",
                source_path=account.source_path,
            )
        try:
            snapshot = fetch_wham(auth)
            snapshot.display_name = account.display_name
            snapshot.account_id = account.account_id
            return snapshot
        except CodexUsageError as exc:
            if exc.stale or auth.stale:
                try:
                    snapshot = fetch_app_server(auth)
                    snapshot.display_name = account.display_name
                    snapshot.account_id = account.account_id
                    return snapshot
                except CodexRPCError as rpc_exc:
                    return UsageSnapshot(
                        provider="codex",
                        account_id=account.account_id,
                        display_name=account.display_name,
                        plan=account.plan,
                        windows=[],
                        status="stale",
                        fetched_at=datetime.now(timezone.utc),
                        error=str(rpc_exc),
                        source_path=account.source_path,
                    )
            return UsageSnapshot(
                provider="codex",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="error",
                fetched_at=datetime.now(timezone.utc),
                error=str(exc),
                source_path=account.source_path,
            )
