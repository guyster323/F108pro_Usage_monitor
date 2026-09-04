from __future__ import annotations

import json
from pathlib import Path

from quotadeck.core.severity import apply_hysteresis, band_for_remaining
from quotadeck.core.models import Severity
from quotadeck.providers.claude.api import parse_usage
from quotadeck.providers.claude.credentials import ClaudeAuth
from quotadeck.providers.cursor.api import parse_usage_summary
from quotadeck.providers.cursor.statedb import CursorAuth
from quotadeck.providers.grok.api import parse_billing
from quotadeck.providers.grok.auth import GrokAuth

FIXTURES = Path(__file__).parent / "fixtures"


def test_claude_fixture_parser() -> None:
    data = json.loads((FIXTURES / "claude_usage.json").read_text(encoding="utf-8"))
    auth = ClaudeAuth("tok", None, "max", ["user:profile"], Path("."))
    snap = parse_usage(data, auth, "demo")
    assert [w.label for w in snap.windows] == ["5H", "WEEK", "OPUS"]
    assert snap.windows[0].remaining_percent == 58.0


def test_grok_fixture_parser() -> None:
    data = json.loads((FIXTURES / "grok_billing.json").read_text(encoding="utf-8"))
    auth = GrokAuth("key", "user1", "a@b.com", None, "oidc", Path("."))
    snap = parse_billing(data, auth, "SuperGrok")
    assert snap.windows[0].label == "WEEK"
    assert snap.windows[0].remaining_percent == 57.5


def test_grok_zero_percent_omitted() -> None:
    auth = GrokAuth("key", "user1", None, None, "oidc", Path("."))
    snap = parse_billing({"config": {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY"}}}, auth)
    assert snap.windows[0].used_percent == 0.0


def test_cursor_uses_dashboard_model_bars() -> None:
    data = json.loads((FIXTURES / "cursor_usage_summary.json").read_text(encoding="utf-8"))
    auth = CursorAuth("tok", "me@example.com", "ultra", "user_1", "db")
    snap = parse_usage_summary(data, auth)
    assert [w.label for w in snap.windows] == ["AUTO", "OTHER"]
    assert round(snap.windows[0].used_percent) == 3
    assert round(snap.windows[0].remaining_percent) == 97
    assert round(snap.windows[1].used_percent) == 16
    assert round(snap.windows[1].remaining_percent) == 84
    assert round(snap.critical_remaining or 0) == 84
    assert snap.plan == "ultra"


def test_cursor_ignores_spend_cents_when_bars_exist() -> None:
    data = json.loads((FIXTURES / "cursor_usage_summary.json").read_text(encoding="utf-8"))
    auth = CursorAuth("tok", "me@example.com", "ultra", "user_1", "db")
    snap = parse_usage_summary(data, auth)
    cents_remaining = 100.0 - (17221 / 40000) * 100.0
    assert abs((snap.critical_remaining or 0) - cents_remaining) > 20


def test_cursor_falls_back_to_cents_without_percent_fields() -> None:
    auth = CursorAuth("tok", "me@example.com", "ultra", "user_1", "db")
    snap = parse_usage_summary(
        {
            "membershipType": "ultra",
            "billingCycleEnd": "2026-09-16T18:13:29.000Z",
            "individualUsage": {"plan": {"used": 10788, "limit": 40000}},
        },
        auth,
    )
    assert snap.windows[0].label == "PLAN"
    assert round(snap.windows[0].used_percent) == 27


def test_cursor_reads_display_messages_without_plan() -> None:
    auth = CursorAuth("tok", "me@example.com", "ultra", "user_1", "db")
    snap = parse_usage_summary(
        {
            "membershipType": "ultra",
            "billingCycleEnd": "2026-09-16T18:13:29.000Z",
            "autoModelSelectedDisplayMessage": "You've used 3% of your included total usage",
            "namedModelSelectedDisplayMessage": "You've used 16% of your included API usage",
        },
        auth,
    )
    assert [round(w.used_percent) for w in snap.windows] == [3, 16]


def test_severity_bands() -> None:
    assert band_for_remaining(85) == Severity.HEALTHY
    assert band_for_remaining(32) == Severity.BUSY
    assert band_for_remaining(14) == Severity.CAUTION
    assert band_for_remaining(4) == Severity.CRITICAL
    assert band_for_remaining(0) == Severity.EXHAUSTED


def test_hysteresis() -> None:
    assert apply_hysteresis(Severity.HEALTHY, 48) == Severity.HEALTHY
    assert apply_hysteresis(Severity.HEALTHY, 46) == Severity.BUSY
    assert apply_hysteresis(Severity.BUSY, 51) == Severity.BUSY
    assert apply_hysteresis(Severity.BUSY, 54) == Severity.HEALTHY
