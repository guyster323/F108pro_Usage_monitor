from __future__ import annotations

from datetime import datetime, timezone

import httpx

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.http import get
from quotadeck.providers.codex.auth import CodexAuth


WHAM_URL = "https://chatgpt.com/backend-api/wham/usage"


class CodexUsageError(RuntimeError):
    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


def _window(raw: dict | None, window_id: str, label: str) -> UsageWindow | None:
    if not raw:
        return None
    used = raw.get("used_percent")
    if used is None:
        used = raw.get("usedPercent")
    if used is None:
        return None
    used_f = float(used)
    reset = raw.get("reset_at") or raw.get("resetsAt")
    resets_at = None
    if isinstance(reset, (int, float)):
        resets_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
    return UsageWindow(
        id=window_id,
        label=label,
        used_percent=used_f,
        remaining_percent=max(0.0, 100.0 - used_f),
        resets_at=resets_at,
    )


def parse_wham(data: dict, auth: CodexAuth) -> UsageSnapshot:
    rate = data.get("rate_limit") or {}
    windows: list[UsageWindow] = []
    primary = _window(rate.get("primary_window") or rate.get("primary"), "session", "5H")
    secondary = _window(rate.get("secondary_window") or rate.get("secondary"), "weekly", "WEEK")
    if primary:
        windows.append(primary)
    if secondary:
        windows.append(secondary)
    plan = data.get("plan_type") or auth.plan
    local = (auth.email or "CODEX").split("@", 1)[0].upper()
    return UsageSnapshot(
        provider="codex",
        account_id=str(data.get("account_id") or auth.account_id or "unknown"),
        display_name=local,
        plan=str(plan) if plan else None,
        windows=windows,
        status="ok" if windows else "error",
        fetched_at=datetime.now(timezone.utc),
        error=None if windows else "no rate-limit windows",
        source_path=str(auth.home),
    )


def fetch_wham(auth: CodexAuth, timeout: float = 20.0) -> UsageSnapshot:
    if not auth.access_token:
        raise CodexUsageError("missing access token", stale=True)
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Accept": "application/json",
        "User-Agent": "QuotaDeck",
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id
    try:
        response = get(WHAM_URL, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise CodexUsageError(str(exc)) from exc
    if response.status_code in {401, 403}:
        raise CodexUsageError("unauthorized", stale=True)
    if response.status_code >= 400:
        raise CodexUsageError(f"wham/usage HTTP {response.status_code}")
    return parse_wham(response.json(), auth)
