from __future__ import annotations

import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from quotadeck.core.mask import email_local
from quotadeck.core.models import AccountRef, UsageSnapshot
from quotadeck.providers.base import Provider
from quotadeck.providers.grok.api import fetch_grok
from quotadeck.providers.grok.auth import (
    grok_home,
    grok_sessions_root,
    has_possible_model_credentials,
    has_xai_api_key,
    read_grok_auth,
)


DEFAULT_MAX_RETAINED_SESSION_ENTRIES = 100_000


def _stable_path_id(path: Path) -> str:
    try:
        normalized = str(path.expanduser().resolve())
    except OSError:
        normalized = str(path.expanduser().absolute())
    digest = sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]
    return f"local-{digest}"


def _has_retained_sessions(
    root: Path,
    *,
    max_entries: int = DEFAULT_MAX_RETAINED_SESSION_ENTRIES,
) -> bool:
    """Check for a retained summary with bounded, symlink-safe traversal."""

    if max_entries < 1:
        return False
    if not root.is_dir():
        return False
    try:
        resolved_root = root.resolve()
    except OSError:
        return False

    pending = [root]
    entries_seen = 0
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        try:
            with entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > max_entries:
                        return False
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if (
                            entry.name == "summary.json"
                            and entry.is_file(follow_symlinks=False)
                            and Path(entry.path).resolve().is_relative_to(resolved_root)
                        ):
                            return True
                    except (OSError, RuntimeError):
                        continue
        except OSError:
            continue
    return False


class GrokProvider(Provider):
    id = "grok"

    def discover(self) -> list[AccountRef]:
        auth = read_grok_auth()
        sessions = grok_sessions_root()
        model_credentials = has_possible_model_credentials()
        if model_credentials:
            # A per-model key/helper outranks stored session auth for whichever
            # model is selected. Discovery has no active CLI --model context,
            # so do not mislabel the combined local history as subscription or
            # API billed and never call the subscription quota endpoint.
            home = grok_home()
            return [
                AccountRef(
                    provider="grok",
                    account_id=_stable_path_id(home),
                    display_name="GROK",
                    source_path=str(home),
                    plan=None,
                    source_kind="cli",
                    source_label="Grok CLI",
                    extra={"auth_mode": "ambiguous"},
                )
            ]

        # Only a non-expired stored session masks the fallback environment key.
        # An expired token must not prevent XAI_API_KEY from becoming active.
        if auth is not None and (not auth.stale or not has_xai_api_key()):
            # Official precedence: an active stored session token wins over the
            # fallback XAI_API_KEY, so never label this account API-billed just
            # because that environment variable also exists.
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
                    extra={"auth_mode": "session"},
                )
            ]

        api_key = has_xai_api_key()
        if not api_key and not _has_retained_sessions(sessions):
            return []
        # The secret is intentionally never copied into GrokAuth or AccountRef.
        # The stable local id is derived only from GROK_HOME.
        home = grok_home()
        mode = "api_key" if api_key else "local_history"
        return [
            AccountRef(
                provider="grok",
                account_id=_stable_path_id(home),
                display_name="GROK API" if api_key else "GROK",
                source_path=str(home),
                plan="api" if api_key else None,
                source_kind="cli",
                source_label="Grok CLI",
                extra={"auth_mode": mode},
            )
        ]

    def fetch(self, account: AccountRef) -> UsageSnapshot:
        mode = account.extra.get("auth_mode")
        if mode in {"api_key", "local_history", "ambiguous"}:
            return UsageSnapshot(
                provider="grok",
                account_id=account.account_id,
                display_name=account.display_name,
                plan=account.plan,
                windows=[],
                status="offline",
                fetched_at=datetime.now(timezone.utc),
                error=(
                    "Quota data is unavailable for API-key accounts"
                    if mode == "api_key"
                    else (
                        "Quota data is unavailable while Grok auth mode is ambiguous"
                        if mode == "ambiguous"
                        else "Grok Build CLI is not signed in"
                    )
                ),
                source_path=account.source_path,
            )
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
