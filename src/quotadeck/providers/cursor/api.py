from __future__ import annotations

from datetime import datetime, timezone

import httpx

from quotadeck.core.mask import email_local
from quotadeck.http import get, post
from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.providers.cursor.statedb import CursorAuth

USAGE_SUMMARY = "https://cursor.com/api/usage-summary"
PERIOD_USAGE = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value)
    if text.isdigit():
        return _parse_dt(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_usage_summary(data: dict, auth: CursorAuth) -> UsageSnapshot:
    individual = data.get("individualUsage") or {}
    plan = individual.get("plan") or {}
    used = float(plan.get("used") or 0)
    limit = float(plan.get("limit") or 0)
    remaining_pct = 100.0
    used_pct = 0.0
    if limit > 0:
        used_pct = (used / limit) * 100.0
        remaining_pct = max(0.0, 100.0 - used_pct)
    reset = _parse_dt(data.get("billingCycleEnd"))
    windows = [
        UsageWindow(
            id="plan",
            label="PLAN",
            used_percent=used_pct,
            remaining_percent=remaining_pct,
            resets_at=reset,
        )
    ]
    ondemand = individual.get("onDemand") or {}
    if ondemand.get("enabled") and float(ondemand.get("limit") or 0) > 0:
        od_used = float(ondemand.get("used") or 0)
        od_limit = float(ondemand.get("limit") or 1)
        od_pct = (od_used / od_limit) * 100.0
        windows.append(
            UsageWindow(
                id="ondemand",
                label="ON-DEMAND",
                used_percent=od_pct,
                remaining_percent=max(0.0, 100.0 - od_pct),
                resets_at=reset,
            )
        )
    membership = data.get("membershipType") or auth.plan
    local = email_local(auth.email) or "CURSOR"
    return UsageSnapshot(
        provider="cursor",
        account_id=auth.user_id,
        display_name=local.upper(),
        plan=str(membership) if membership else None,
        windows=windows,
        status="ok",
        fetched_at=datetime.now(timezone.utc),
        source_path=auth.source,
    )


def parse_period_usage(data: dict, auth: CursorAuth) -> UsageSnapshot:
    plan = data.get("planUsage") or {}
    used = float(plan.get("totalSpend") or plan.get("includedSpend") or 0)
    limit = float(plan.get("limit") or 0)
    remaining = float(plan.get("remaining") or 0)
    if limit <= 0 and remaining:
        limit = used + remaining
    used_pct = (used / limit) * 100.0 if limit else 0.0
    reset = _parse_dt(data.get("billingCycleEnd"))
    return UsageSnapshot(
        provider="cursor",
        account_id=auth.user_id,
        display_name=(email_local(auth.email) or "CURSOR").upper(),
        plan=auth.plan,
        windows=[
            UsageWindow(
                id="plan",
                label="PLAN",
                used_percent=used_pct,
                remaining_percent=max(0.0, 100.0 - used_pct),
                resets_at=reset,
            )
        ],
        status="ok",
        fetched_at=datetime.now(timezone.utc),
        source_path=auth.source,
    )


def session_cookie(auth: CursorAuth) -> str:
    return f"WorkosCursorSessionToken={auth.user_id}%3A%3A{auth.access_token}"


def fetch_cursor(auth: CursorAuth, timeout: float = 20.0) -> UsageSnapshot:
    headers = {
        "Cookie": session_cookie(auth),
        "Accept": "application/json",
        "User-Agent": "QuotaDeck",
    }
    try:
        response = get(USAGE_SUMMARY, headers=headers, timeout=timeout)
        if response.status_code < 400:
            return parse_usage_summary(response.json(), auth)
    except httpx.HTTPError:
        pass
    rpc_headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "User-Agent": "QuotaDeck",
    }
    response = post(PERIOD_USAGE, headers=rpc_headers, json={}, timeout=timeout)
    if response.status_code in {401, 403}:
        raise RuntimeError("cursor unauthorized — sign in again in Cursor")
    response.raise_for_status()
    return parse_period_usage(response.json(), auth)
