from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from quotadeck.core.mask import email_local
from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.http import get, post
from quotadeck.providers.cursor.statedb import CursorAuth

USAGE_SUMMARY = "https://cursor.com/api/usage-summary"
PERIOD_USAGE = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
_PERCENT_IN_TEXT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


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


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _percent_from_message(text: object) -> float | None:
    if not text:
        return None
    match = _PERCENT_IN_TEXT.search(str(text))
    return _finite_float(match.group(1)) if match else None


def _window(used_pct: float, *, window_id: str, label: str, reset: datetime | None) -> UsageWindow:
    used = max(0.0, float(used_pct))
    return UsageWindow(
        id=window_id,
        label=label,
        used_percent=used,
        remaining_percent=max(0.0, 100.0 - used),
        resets_at=reset,
    )


def parse_usage_summary(data: dict, auth: CursorAuth) -> UsageSnapshot:
    """Parse the same two bars the Cursor dashboard draws.

    Official UI:
    - Cursor Models (Auto / Composer / Cursor Grok) ← ``autoPercentUsed``
    - Other Models ← ``apiPercentUsed``

    ``plan.used / plan.limit`` is included-spend cents and does **not** match
    those bars (e.g. 17221/40000 ≈ 43% used while the dashboard shows 3% / 16%).
    """
    individual = data.get("individualUsage") or {}
    plan = individual.get("plan") or {}
    reset = _parse_dt(data.get("billingCycleEnd"))

    auto_used = _finite_float(plan.get("autoPercentUsed"))
    api_used = _finite_float(plan.get("apiPercentUsed"))
    if auto_used is None:
        auto_used = _percent_from_message(data.get("autoModelSelectedDisplayMessage"))
    if api_used is None:
        api_used = _percent_from_message(data.get("namedModelSelectedDisplayMessage"))

    windows: list[UsageWindow] = []
    if auto_used is not None:
        windows.append(_window(auto_used, window_id="auto", label="AUTO", reset=reset))
    if api_used is not None:
        windows.append(_window(api_used, window_id="api", label="OTHER", reset=reset))

    if not windows:
        used = _finite_float(plan.get("used")) or 0.0
        limit = _finite_float(plan.get("limit")) or 0.0
        if limit > 0:
            windows.append(_window((used / limit) * 100.0, window_id="plan", label="PLAN", reset=reset))

    ondemand = individual.get("onDemand") or {}
    od_limit = _finite_float(ondemand.get("limit")) or 0.0
    if ondemand.get("enabled") and od_limit > 0:
        od_used = _finite_float(ondemand.get("used")) or 0.0
        windows.append(_window((od_used / od_limit) * 100.0, window_id="ondemand", label="ONDEM", reset=reset))

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
    auto_used = _finite_float(plan.get("autoPercentUsed"))
    api_used = _finite_float(plan.get("apiPercentUsed"))
    reset = _parse_dt(data.get("billingCycleEnd"))
    windows: list[UsageWindow] = []
    if auto_used is not None:
        windows.append(_window(auto_used, window_id="auto", label="AUTO", reset=reset))
    if api_used is not None:
        windows.append(_window(api_used, window_id="api", label="OTHER", reset=reset))
    if not windows:
        used = float(plan.get("totalSpend") or plan.get("includedSpend") or 0)
        limit = float(plan.get("limit") or 0)
        remaining = float(plan.get("remaining") or 0)
        if limit <= 0 and remaining:
            limit = used + remaining
        used_pct = (used / limit) * 100.0 if limit else 0.0
        windows.append(_window(used_pct, window_id="plan", label="PLAN", reset=reset))
    return UsageSnapshot(
        provider="cursor",
        account_id=auth.user_id,
        display_name=(email_local(auth.email) or "CURSOR").upper(),
        plan=auth.plan,
        windows=windows,
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
