from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from quotadeck.core.severity import apply_hysteresis, band_for_remaining, worst_severity
from quotadeck.core.models import Severity
from quotadeck.providers.claude.api import parse_usage
from quotadeck.providers.claude.credentials import ClaudeAuth
from quotadeck.providers.cursor.api import parse_usage_summary
from quotadeck.providers.cursor.statedb import CursorAuth
from quotadeck.providers.grok.api import parse_billing
from quotadeck.providers.grok.auth import GrokAuth
from quotadeck.providers.codex.appserver_rpc import (
    CodexRPCError,
    _JsonLineReader,
    _app_server_environment,
)

FIXTURES = Path(__file__).parent / "fixtures"

def test_claude_fixture_parser() -> None:
    data = json.loads((FIXTURES / "claude_usage.json").read_text(encoding="utf-8"))
    auth = ClaudeAuth("tok", None, "max", ["user:profile"], Path("."))
    snap = parse_usage(data, auth, "demo")
    assert [w.label for w in snap.windows] == ["5H", "WEEK", "OPUS"]
    assert snap.windows[0].remaining_percent == 58.0


def test_claude_parser_normalizes_all_reset_timestamps_to_utc() -> None:
    auth = ClaudeAuth("tok", None, "max", ["user:profile"], Path("."))
    snap = parse_usage(
        {
            "five_hour": {
                "utilization": 10,
                "resets_at": "2026-09-11T12:30:00",
            },
            "seven_day": {
                "utilization": 20,
                "resets_at": "2026-09-11T21:30:00+09:00",
            },
        },
        auth,
    )

    assert [window.resets_at for window in snap.windows] == [
        datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
    ]
    assert all(
        window.resets_at is not None
        and window.resets_at.tzinfo is timezone.utc
        and window.resets_at.utcoffset() == timedelta(0)
        for window in snap.windows
    )

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


def test_cursor_auth_repr_never_contains_access_token() -> None:
    secret = "cursor-secret-do-not-log"
    auth = CursorAuth(secret, "me@example.com", "ultra", "user_1", "db")

    assert secret not in repr(auth)

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


def test_worst_severity_uses_urgency_not_enum_declaration_order() -> None:
    assert worst_severity([Severity.HEALTHY, Severity.CRITICAL]) is Severity.CRITICAL
    assert worst_severity([Severity.OFFLINE, Severity.ERROR]) is Severity.ERROR
    assert worst_severity([]) is None


def test_codex_rpc_reader_skips_noise_and_reads_json() -> None:
    reader = _JsonLineReader(io.StringIO('not-json\n\n{"id": 2, "result": {"ok": true}}\n'))
    assert reader.get(0.5) == {"id": 2, "result": {"ok": True}}
    assert reader.join(1)


def test_codex_rpc_pipe_reader_preserves_normal_jsonl_behavior() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r", encoding="utf-8")
    reader = _JsonLineReader(stream)
    try:
        os.write(write_fd, b'not-json\r\n{"id": 2, "result": {"ok": true}}\r\n')
        assert reader.get(0.5) == {"id": 2, "result": {"ok": True}}
    finally:
        reader.stop()
        assert reader.join(1)
        os.close(write_fd)
        stream.close()


def test_codex_rpc_reader_timeout_is_bounded() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r", encoding="utf-8")
    reader = _JsonLineReader(stream)
    started = time.monotonic()
    try:
        with pytest.raises(CodexRPCError, match="timeout"):
            reader.get(0.05)
        assert time.monotonic() - started < 0.5
    finally:
        # stop() must wake an idle pipe reader even while another process still
        # owns the write handle (for example, an app-server grandchild).
        reader.stop()
        assert reader.join(1)
        os.close(write_fd)
        stream.close()


def test_codex_rpc_reader_rejects_oversized_line_without_reading_it_whole() -> None:
    class RecordingStream(io.StringIO):
        requested_sizes: list[int]

        def __init__(self, value: str) -> None:
            super().__init__(value)
            self.requested_sizes = []

        def readline(self, size: int = -1) -> str:
            self.requested_sizes.append(size)
            return super().readline(size)

    stream = RecordingStream("x" * 10_000 + "\n")
    reader = _JsonLineReader(stream, max_line_chars=64, max_queue_items=2)

    with pytest.raises(CodexRPCError, match="line exceeds safety limit"):
        reader.get(0.5)

    assert reader.join(1)
    assert stream.requested_sizes == [66]


def test_codex_rpc_pipe_reader_rejects_oversized_unterminated_line() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r", encoding="utf-8")
    reader = _JsonLineReader(stream, max_line_chars=64, max_queue_items=2)
    try:
        os.write(write_fd, b"x" * 66)
        with pytest.raises(CodexRPCError, match="line exceeds safety limit"):
            reader.get(0.5)
        assert reader.join(1)
    finally:
        reader.stop()
        os.close(write_fd)
        stream.close()


def test_codex_rpc_reader_queue_is_bounded_and_stop_releases_producer() -> None:
    stream = io.StringIO("".join('{"method": "notification"}\n' for _ in range(100)))
    reader = _JsonLineReader(stream, max_line_chars=128, max_queue_items=2)
    try:
        deadline = time.monotonic() + 0.5
        while reader._items.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        assert reader._items.maxsize == 2
        assert reader._items.qsize() == 2
        assert not reader.join(0.01)
    finally:
        reader.stop()
        assert reader.join(1)


def test_codex_app_server_environment_withholds_unrelated_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-forward")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-forward")
    monkeypatch.setenv("CI_JOB_TOKEN", "do-not-forward")

    env = _app_server_environment(tmp_path)

    assert env["CODEX_HOME"] == str(tmp_path)
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CI_JOB_TOKEN" not in env
