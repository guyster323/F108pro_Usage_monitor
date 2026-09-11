from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.claude.api import fetch_claude
from quotadeck.providers.claude.credentials import (
    has_ambiguous_auth_environment,
    has_anthropic_api_key,
    has_local_project_history,
    projects_path,
    read_claude_auth,
    uses_official_anthropic_pricing_endpoint,
)


def _local_account_id(path: Path) -> str:
    try:
        normalized = str(path.expanduser().resolve())
    except OSError:
        normalized = str(path.expanduser().absolute())
    digest = sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"local-{digest[:12]}"


class ClaudeProvider(Provider):
    id = "claude"
    def discover(self) -> list[AccountRef]:
        auth = read_claude_auth()
        projects = projects_path()
        local_history = has_local_project_history(projects)
        api_key = has_anthropic_api_key()
        ambiguous_environment = has_ambiguous_auth_environment()
        pricing_scope = (
            "official"
            if uses_official_anthropic_pricing_endpoint()
            else "custom"
        )

        # Environment presence alone cannot prove which Claude credential was
        # accepted for a retained session. Mixed OAuth/API credentials and
        # cloud/bearer-token selectors are deliberately left unpriced.
        if (api_key and auth is not None) or ambiguous_environment:
            if auth is None and not local_history:
                return []
            return [
                AccountRef(
                    provider="claude",
                    account_id=(
                        "claude" if auth is not None else _local_account_id(projects)
                    ),
                    display_name="CLAUDE",
                    source_path=str(auth.path if auth is not None else projects),
                    plan=None,
                    email_local="CLAUDE",
                    source_kind="cli",
                    source_label="Claude Code",
                    extra={
                        "auth_mode": "ambiguous",
                        "local_history": "1" if local_history else "0",
                        "pricing_scope": pricing_scope,
                    },
                )
            ]

        # A direct API key is classified as API-billed only when it is the sole
        # detected credential and there is retained history to price.
        if api_key and local_history:
            return [
                AccountRef(
                    provider="claude",
                    account_id=_local_account_id(projects),
                    display_name="CLAUDE",
                    source_path=str(projects),
                    plan="api",
                    email_local="CLAUDE",
                    source_kind="cli",
                    source_label="Claude Code",
                    extra={
                        "auth_mode": "api_key",
                        "local_history": "1",
                        "pricing_scope": pricing_scope,
                    },
                )
            ]

        if auth is not None:
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
                    extra={"auth_mode": "oauth", "pricing_scope": pricing_scope},
                )
            ]

        if not local_history:
            return []
        return [
            AccountRef(
                provider="claude",
                account_id=_local_account_id(projects),
                display_name="CLAUDE",
                source_path=str(projects),
                plan=None,
                email_local="CLAUDE",
                source_kind="cli",
                source_label="Claude Code",
                extra={
                    "auth_mode": "local_history",
                    "local_history": "1",
                    "pricing_scope": pricing_scope,
                },
            )
        ]
    def fetch(self, account: AccountRef) -> UsageSnapshot:
        auth_mode = account.extra.get("auth_mode")
        auth = read_claude_auth()
        ambiguous_now = has_ambiguous_auth_environment() or (
            has_anthropic_api_key() and auth is not None
        )
        if auth_mode == "ambiguous" or ambiguous_now:
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=None,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Quota data is unavailable while Claude auth mode is ambiguous",
                source_path=account.source_path,
            )
        api_account = account.plan == "api" or auth_mode == "api_key"
        if api_account:
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan="api",
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Quota data is unavailable for API-key accounts",
                source_path=account.source_path,
            )
        if auth is None:
            return UsageSnapshot(
                provider="claude",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error="Quota data is unavailable without a Claude subscription login",
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
