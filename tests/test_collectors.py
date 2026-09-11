from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quotadeck.core.models import AccountRef
from quotadeck.usage.ccusage import parse_ccusage_payload, validate_or_explain
from quotadeck.usage.collectors import (
    COLLECTOR_SCHEMA,
    CollectorSettings,
    CollectorStatus,
    ValidationStatus,
    parse_quotadeck_import,
    tokens_from_mapping,
    validate_daily_totals,
)
from quotadeck.usage.cursor_export import collect_cursor_export, parse_cursor_csv
from quotadeck.usage.models import UsageSourceKind
from quotadeck.usage.service import CumulativeUsageService, UsageLoadStatus, UsageService
from quotadeck.usage.token_stats import collect_token_stats, parse_token_stats_payload


def test_reasoning_is_not_added_to_output() -> None:
    usage = tokens_from_mapping(
        {
            "input": 100,
            "output": 40,
            "cacheRead": 10,
            "cacheWrite": 5,
            "reasoning": 12,
        }
    )
    assert usage is not None
    assert usage.output_tokens == 40
    assert usage.reasoning_output_tokens == 12
    assert usage.total_tokens == 155


def test_token_stats_fixture_normalizes_providers_and_categories() -> None:
    payload = {
        "groupBy": "session,model",
        "entries": [
            {
                "sessionId": "codex-1",
                "client": "codex",
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "date": "2026-09-11",
                "input": 80,
                "output": 20,
                "cacheRead": 8,
                "cacheWrite": 2,
                "reasoning": 7,
            },
            {
                "sessionId": "claude-1",
                "client": "claude",
                "model": "claude-sonnet-4-5-20250929",
                "date": "2026-09-10",
                "input": 50,
                "output": 10,
                "cacheRead": 4,
                "cacheWrite": 1,
                "reasoning": 3,
            },
            {
                "sessionId": "cursor-1",
                "client": "cursor",
                "account": "work",
                "model": "grok-4.6",
                "date": "2026-09-09",
                "input": 30,
                "output": 6,
                "cacheRead": 0,
                "cacheWrite": 0,
                "reasoning": 2,
            },
            {
                "sessionId": "grok-1",
                "client": "grok",
                "model": "grok-4.6",
                "date": "2026-09-08",
                "input": 12,
                "output": 4,
                "cacheRead": 1,
                "cacheWrite": 0,
                "reasoning": 1,
            },
        ],
    }
    for provider, model, total in (
        ("codex", "gpt-5.6-sol", 110),
        ("claude", "claude-sonnet-4-5-20250929", 65),
        ("cursor", "grok-4.6", 36),
        ("grok", "grok-4.6", 17),
    ):
        attempt = parse_token_stats_payload(payload, provider=provider)
        assert attempt.usable
        assert attempt.dataset is not None
        assert len(attempt.dataset.observations) == 1
        item = attempt.dataset.observations[0]
        assert item.model == model
        assert item.tokens.total_tokens == total
        assert item.tokens.reasoning_output_tokens <= item.tokens.output_tokens
        assert item.source_kind is UsageSourceKind.TOKEN_STATS


def test_token_stats_undated_models_json_is_unusable() -> None:
    payload = {
        "groupBy": "session,model",
        "entries": [
            {
                "sessionId": "s1",
                "client": "codex",
                "model": "gpt-5",
                "input": 10,
                "output": 2,
            }
        ],
    }
    attempt = parse_token_stats_payload(payload, provider="codex")
    assert not attempt.usable
    assert attempt.status is CollectorStatus.UNUSABLE
    assert attempt.reason and "date" in attempt.reason


def test_token_stats_timeout_and_bad_json_fall_back(tmp_path: Path) -> None:
    settings = CollectorSettings(
        token_stats_executable=tmp_path / "token-stats.exe",
        enable_token_stats=True,
        timeout_seconds=1.0,
    )
    settings.token_stats_executable.write_text("", encoding="utf-8")

    def timeout_runner(*_args, **_kwargs):
        raise __import__("subprocess").TimeoutExpired("token-stats", 1)

    timed_out = collect_token_stats(
        "codex",
        source_path=tmp_path,
        settings=settings,
        runner=timeout_runner,
    )
    assert timed_out.status is CollectorStatus.UNUSABLE
    assert timed_out.reason and "timeout" in timed_out.reason

    def huge_runner(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = b"{" + (b"x" * (9 * 1024 * 1024))

        return Result()

    oversized = collect_token_stats(
        "codex",
        source_path=tmp_path,
        settings=CollectorSettings(
            token_stats_executable=settings.token_stats_executable,
            enable_token_stats=True,
            max_json_bytes=1024,
        ),
        runner=huge_runner,
    )
    assert oversized.status is CollectorStatus.UNUSABLE

    def bad_json_runner(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = b"not-json"

        return Result()

    bad = collect_token_stats(
        "codex",
        source_path=tmp_path,
        settings=settings,
        runner=bad_json_runner,
    )
    assert bad.status is CollectorStatus.UNUSABLE


def test_native_scan_used_when_token_stats_is_absent(tmp_path: Path) -> None:
    session = tmp_path / "projects" / "one.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session",
                "timestamp": "2026-09-11T01:00:00Z",
                "message": {
                    "id": "message",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = UsageService(
        collector=CollectorSettings(enable_token_stats=False, enable_ccusage=False)
    ).load("claude", tmp_path, today=date(2026, 9, 11), timezone_name="UTC")
    assert result.status is UsageLoadStatus.OK
    assert result.report is not None
    assert result.report.tokens.total_tokens == 150


def test_ccusage_mismatch_marks_partial_without_blending(tmp_path: Path) -> None:
    import_path = tmp_path / "token-stats.json"
    import_path.write_text(
        json.dumps(
            {
                "schema": COLLECTOR_SCHEMA,
                "observations": [
                    {
                        "provider": "codex",
                        "model": "gpt-5.6-sol",
                        "observed_at": "2026-09-11T12:00:00+00:00",
                        "session_id": "s",
                        "event_id": "e1",
                        "input_tokens": 100,
                        "output_tokens": 20,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    primary = parse_token_stats_payload(
        json.loads(import_path.read_text(encoding="utf-8")),
        provider="codex",
    )
    other = parse_ccusage_payload(
        {
            "daily": [
                {
                    "date": "2026-09-11",
                    "inputTokens": 10,
                    "outputTokens": 2,
                    "cacheCreationTokens": 0,
                    "cacheReadTokens": 0,
                    "modelBreakdowns": [
                        {
                            "modelName": "gpt-5.6-sol",
                            "inputTokens": 10,
                            "outputTokens": 2,
                        }
                    ],
                }
            ]
        },
        provider="codex",
    )
    assert primary.usable and other.usable
    verdict = validate_daily_totals(primary.dataset, other.dataset)
    assert verdict.status is ValidationStatus.MISMATCH
    assert primary.dataset.observations[0].tokens.total_tokens == 120


def test_ccusage_unclear_agents_are_unusable() -> None:
    attempt = parse_ccusage_payload(
        {
            "daily": [
                {
                    "date": "2026-09-11",
                    "agents": [
                        {
                            "agent": "claude",
                            "inputTokens": 10,
                            "outputTokens": 2,
                        },
                        {
                            "agent": "codex",
                            "inputTokens": 9,
                            "outputTokens": 1,
                        },
                    ],
                    "inputTokens": 19,
                    "outputTokens": 3,
                }
            ]
        },
        provider="codex",
    )
    assert not attempt.usable


def test_cursor_export_requires_account_and_date(tmp_path: Path) -> None:
    generic = tmp_path / "usage.csv"
    generic.write_text(
        "date,input,output\n2026-09-11,10,2\n",
        encoding="utf-8",
    )
    refused = parse_cursor_csv(generic, account="work")
    assert not refused.usable
    assert refused.reason and "account" in refused.reason

    attributed = tmp_path / "usage.work.csv"
    attributed.write_text(
        "date,model,input,output,cacheRead,cacheWrite,reasoning\n"
        "2026-09-11,grok-4.6,30,10,4,1,3\n",
        encoding="utf-8",
    )
    accepted = parse_cursor_csv(attributed, account="work")
    assert accepted.usable
    assert accepted.dataset is not None
    item = accepted.dataset.observations[0]
    assert item.provider == "cursor"
    assert item.tokens.total_tokens == 45
    assert item.tokens.reasoning_output_tokens == 3
    assert item.source_kind is UsageSourceKind.CURSOR_EXPORT


def test_cursor_without_export_stays_unsupported(tmp_path: Path) -> None:
    account = AccountRef(
        provider="cursor",
        account_id="cursor",
        display_name="CURSOR",
        source_path=str(tmp_path / "state.vscdb"),
    )
    snapshot = CumulativeUsageService().snapshot(account)
    assert snapshot.status == "unsupported"
    assert snapshot.source_label == "ADMIN API REQUIRED"


def test_cursor_export_enables_cumulative_card(tmp_path: Path) -> None:
    export = tmp_path / "usage.work.csv"
    lines = ["date,model,input,output,cacheRead,cacheWrite,reasoning"]
    for day in range(1, 12):
        lines.append(f"2026-09-{day:02d},grok-4.6,30,10,0,0,2")
    export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    settings = CollectorSettings(cursor_export_path=export)
    account = AccountRef(
        provider="cursor",
        account_id="work",
        display_name="WORK",
        source_path=str(tmp_path / "state.vscdb"),
    )
    snapshot = CumulativeUsageService()
    snapshot.loader.collector = settings
    card = snapshot.snapshot(account)
    assert card.available
    assert card.source_label == "CURSOR EXPORT"
    assert card.this_tokens == 40


def test_import_schema_rejects_body_only_rows() -> None:
    attempt = parse_quotadeck_import(
        {
            "schema": COLLECTOR_SCHEMA,
            "observations": [
                {
                    "provider": "codex",
                    "prompt": "secret question",
                    "response": "secret answer",
                }
            ],
        },
        provider="codex",
    )
    assert not attempt.usable


def test_missing_collectors_do_not_crash_service(tmp_path: Path) -> None:
    result = UsageService(
        collector=CollectorSettings(enable_token_stats=True, enable_ccusage=True)
    ).load("codex", tmp_path)
    assert result.status in {
        UsageLoadStatus.UNAVAILABLE,
        UsageLoadStatus.UNSUPPORTED,
        UsageLoadStatus.ERROR,
    }
