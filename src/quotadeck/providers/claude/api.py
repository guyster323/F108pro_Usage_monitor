from __future__ import annotations

from datetime import datetime, timezone

import httpx

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.http import get
from quotadeck.providers.claude.credentials import ClaudeAuth

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

def parse_usage(data: dict, auth: ClaudeAuth, email_local: str = "CLAUDE") -> UsageSnapshot:
    windows: list[UsageWindow] = []
    mapping = (
        ("five_hour", "session", "5H"),
        ("seven_day", "weekly", "WEEK"),
        ("seven_day_opus", "opus", "OPUS"),
        ("seven_day_sonnet", "sonnet", "SONNET"),
    )
    for field, window_id, label in mapping:
        raw = data.get(field)
        if not raw:
            continue
        used = float(raw.get("utilization") or 0)
        reset = raw.get("resets_at")
        resets_at = None
        if reset:
            try:
                resets_at = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
            except ValueError:
                resets_at = None
        windows.append(
            UsageWindow(
                id=window_id,
                label=label,
                used_percent=used,
                remaining_percent=max(0.0, 100.0 - used),
                resets_at=resets_at,
            )
        )
    return UsageSnapshot(
        provider="claude",
        account_id=email_local.lower(),
        display_name=email_local.upper(),
        plan=auth.subscription,
        windows=windows,
        status="ok" if windows else "error",
        fetched_at=datetime.now(timezone.utc),
        error=None if windows else "no usage windows",
        source_path=str(auth.path),
    )

def fetch_claude(auth: ClaudeAuth, timeout: float = 20.0) -> UsageSnapshot:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "claude-code/2.1.0",
    }
    response = get(USAGE_URL, headers=headers, timeout=timeout)
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "300")
        raise RuntimeError(f"rate_limited:{retry}")
    if response.status_code in {401, 403}:
        raise RuntimeError("stale")
    response.raise_for_status()
    email_local = "CLAUDE"
    try:
        profile = get(
            PROFILE_URL,
            headers={
                "Authorization": f"Bearer {auth.access_token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        if profile.status_code < 400:
            acct = profile.json().get("account") or {}
            email = acct.get("emailAddress") or acct.get("email_address") or acct.get("email")
            if email:
                email_local = str(email).split("@", 1)[0]
    except httpx.HTTPError:
        pass
    return parse_usage(response.json(), auth, email_local)
