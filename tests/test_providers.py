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


def test_cursor_plan_from_cents() -> None:
    data = json.loads((FIXTURES / "cursor_usage_summary.json").read_text(encoding="utf-8"))
    auth = CursorAuth("tok", "me@example.com", "ultra", "user_1", "db")
    snap = parse_usage_summary(data, auth)
    assert snap.windows[0].label == "PLAN"
    assert round(snap.windows[0].used_percent) == 27
    assert snap.plan == "ultra"


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
