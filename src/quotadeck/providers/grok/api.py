from __future__ import annotations

from datetime import datetime, timezone

import httpx

from quotadeck.core.mask import email_local
from quotadeck.http import get
from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.providers.grok.auth import GrokAuth

BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"

def parse_billing(data: dict, auth: GrokAuth, plan: str | None = None) -> UsageSnapshot:
    config = data.get("config") or data
    period = config.get("currentPeriod") or {}
    used = config.get("creditUsagePercent")
    if used is None:
        used = 0.0 if period else None
    used_f = float(used or 0)
    period_type = str(period.get("type") or "USAGE_PERIOD_TYPE_WEEKLY")
    label = "MONTH" if "MONTH" in period_type else "WEEK"
    reset = period.get("end")
    resets_at = None
    if reset:
        try:
            resets_at = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
        except ValueError:
            resets_at = None
    windows = [
        UsageWindow(
            id="credits",
            label=label,
            used_percent=used_f,
            remaining_percent=max(0.0, 100.0 - used_f),
            resets_at=resets_at,
        )
    ]
    local = email_local(auth.email) or "GROK"
    return UsageSnapshot(
        provider="grok",
        account_id=auth.user_id,
        display_name=local.upper(),
        plan=plan or data.get("subscription_tier"),
        windows=windows,
        status="ok",
        fetched_at=datetime.now(timezone.utc),
        source_path=str(auth.path),
    )

def fetch_grok(auth: GrokAuth, timeout: float = 20.0) -> UsageSnapshot:
    headers = {
        "Authorization": f"Bearer {auth.key}",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-userid": auth.user_id,
        "Accept": "application/json",
        "User-Agent": "QuotaDeck",
    }
    response = get(BILLING_URL, headers=headers, timeout=timeout)
    if response.status_code in {401, 403}:
        raise RuntimeError("stale")
    response.raise_for_status()
    plan = None
    try:
        settings = get(SETTINGS_URL, headers=headers, timeout=timeout)
        if settings.status_code < 400:
            plan = settings.json().get("subscription_tier_display")
    except httpx.HTTPError:
        pass
    return parse_billing(response.json(), auth, plan)
