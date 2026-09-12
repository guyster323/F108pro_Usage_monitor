from __future__ import annotations

import json
import os
import subprocess
from datetime import date, timedelta, timezone
from decimal import Decimal
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
from quotadeck.usage.cursor_export import (
    bind_generic_cursor_usage_csv,
    collect_cursor_export,
    discover_cursor_export_files,
    parse_cursor_csv,
    parse_cursor_csv_result,
    parse_cursor_reported_cost,
)
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
    ).load("claude", tmp_path, today=date(2026, 9, 11), timezone_name=timezone.utc)
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
    today = date.today()
    lines = ["date,model,input,output,cacheRead,cacheWrite,reasoning"]
    for offset in range(11, -1, -1):
        day = today - timedelta(days=offset)
        lines.append(f"{day.isoformat()},grok-4.6,30,10,0,0,2")
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


FIXTURES = Path(__file__).parent / "fixtures"

_V1_CSV = """Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you
2025-02-01,gpt-4o,10,5,0,15,30,$0.10,$0.10
2025-02-02,gpt-4o-mini,0,0,0,5,5,$0.05,$0.05
"""

_V2_CSV = """Date,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
"2025-11-13T18:36:05.846Z","Included","auto","No","28342","775","105891","21282","156290","0.19"
"2025-11-13T13:35:04.658Z","On-Demand","gpt-5-codex","No","0","8263","66964","1612","76839","0.03"
"""


def test_bind_generic_usage_csv_requires_explicit_single_account() -> None:
    assert not bind_generic_cursor_usage_csv(requested_account="work", bound_account=None)
    assert not bind_generic_cursor_usage_csv(requested_account="work", bound_account="")
    assert not bind_generic_cursor_usage_csv(
        requested_account="work", bound_account="personal"
    )
    assert bind_generic_cursor_usage_csv(
        requested_account="Work", bound_account="work"
    )


def test_cursor_dashboard_v1_csv_uses_exclusive_input_not_total(tmp_path: Path) -> None:
    path = tmp_path / "usage.work.csv"
    path.write_text(_V1_CSV, encoding="utf-8")
    attempt = parse_cursor_csv(path, account="work")
    assert attempt.usable
    assert attempt.dataset is not None
    first, second = attempt.dataset.observations
    assert first.tokens.input_tokens == 5
    assert first.tokens.cache_write_tokens == 5
    assert first.tokens.output_tokens == 15
    assert first.tokens.total_tokens == 25
    assert first.tokens.total_tokens != 30
    assert first.tokens.reasoning_output_tokens == 0
    assert second.tokens.output_tokens == 5
    assert "unknown" in " ".join(attempt.dataset.coverages[0].limitations).casefold()
    assert "Total Tokens" in " ".join(attempt.dataset.coverages[0].limitations)


def test_cursor_dashboard_v2_csv_derives_cache_write(tmp_path: Path) -> None:
    path = tmp_path / "usage.work.csv"
    path.write_text(_V2_CSV, encoding="utf-8")
    attempt = parse_cursor_csv(path, account="work")
    assert attempt.usable
    assert attempt.dataset is not None
    first, second = attempt.dataset.observations
    assert first.model == "auto"
    assert first.tokens.input_tokens == 775
    assert first.tokens.cache_write_tokens == 28342 - 775
    assert first.tokens.cached_input_tokens == 105891
    assert first.tokens.output_tokens == 21282
    assert first.tokens.total_tokens != 156290
    assert "reasoning" not in _V2_CSV.casefold().split("input")[0]
    assert first.tokens.reasoning_output_tokens == 0
    assert second.model == "gpt-5-codex"
    assert second.tokens.input_tokens == 8263
    assert second.tokens.cache_write_tokens == 0


def test_cursor_dashboard_v3_fixture_parses_kind_and_included_cost(tmp_path: Path) -> None:
    path = tmp_path / "usage.work.csv"
    path.write_text(
        (FIXTURES / "cursor_usage_events_v3.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    attempt = parse_cursor_csv(path, account="work")
    assert attempt.usable
    assert attempt.dataset is not None
    rows = attempt.dataset.observations
    assert len(rows) == 3
    assert rows[0].model == "composer-2"
    assert rows[0].tokens.input_tokens == 343446
    assert rows[0].tokens.cache_write_tokens == 0
    assert rows[0].tokens.cached_input_tokens == 29045760
    assert rows[0].tokens.output_tokens == 915201
    assert rows[0].tokens.total_tokens == 343446 + 29045760 + 915201
    assert rows[0].tokens.input_tokens != 30304407
    assert rows[0].tokens.reasoning_output_tokens == 0
    assert rows[2].tokens.input_tokens == 104504
    assert rows[2].tokens.output_tokens == 3666


def test_generic_usage_csv_binds_only_when_explicit(tmp_path: Path) -> None:
    generic = tmp_path / "usage.csv"
    generic.write_text(_V1_CSV, encoding="utf-8")
    refused = parse_cursor_csv(generic, account="work")
    assert not refused.usable
    assert refused.reason and "account" in refused.reason.casefold()

    bound = parse_cursor_csv(generic, account="work", bind_generic_account="work")
    assert bound.usable
    mismatched = parse_cursor_csv(generic, account="work", bind_generic_account="personal")
    assert not mismatched.usable


def test_cursor_cache_isolates_named_accounts_and_skips_archive(tmp_path: Path) -> None:
    cache = tmp_path / "cursor-cache"
    archive = cache / "archive"
    archive.mkdir(parents=True)
    (cache / "usage.work.csv").write_text(_V1_CSV, encoding="utf-8")
    (cache / "usage.personal.csv").write_text(_V2_CSV, encoding="utf-8")
    (archive / "usage.work.csv").write_text(
        "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
        "2026-01-01,secret-model,99,99,99,99,99,$9.99\n",
        encoding="utf-8",
    )
    found_work = discover_cursor_export_files(account="work", cache_dirs=(cache,))
    assert found_work == [cache / "usage.work.csv"]
    work = collect_cursor_export(
        account="work",
        settings=CollectorSettings(),
        cache_dirs=(cache,),
    )
    personal = collect_cursor_export(
        account="personal",
        settings=CollectorSettings(),
        cache_dirs=(cache,),
    )
    assert work.usable and personal.usable
    assert work.dataset is not None and personal.dataset is not None
    assert {item.model for item in work.dataset.observations} == {"gpt-4o", "gpt-4o-mini"}
    assert {item.model for item in personal.dataset.observations} == {"auto", "gpt-5-codex"}
    assert all(item.model != "secret-model" for item in work.dataset.observations)


def test_cursor_sync_timeout_falls_back_to_stale_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cursor-cache"
    cache.mkdir()
    (cache / "usage.work.csv").write_text(_V1_CSV, encoding="utf-8")
    executable = tmp_path / "token-stats.exe"
    executable.write_text("", encoding="utf-8")
    os.utime(cache / "usage.work.csv", (1_000.0, 1_000.0))
    calls: list[list[str]] = []

    def timeout_runner(argv, **kwargs):
        calls.append(list(argv))
        assert kwargs.get("stdin") is subprocess.DEVNULL
        assert kwargs.get("stderr") is subprocess.DEVNULL
        raise subprocess.TimeoutExpired("token-stats", 1)

    settings = CollectorSettings(
        token_stats_executable=executable,
        enable_cursor_sync=True,
        cursor_sync_ttl_seconds=300.0,
        timeout_seconds=1.0,
    )
    attempt = collect_cursor_export(
        account="work",
        settings=settings,
        runner=timeout_runner,
        clock=lambda: 10_000.0,
        cache_dirs=(cache,),
    )
    assert calls and calls[0][1:] == ["cursor", "sync", "--json"]
    assert attempt.usable
    assert attempt.dataset is not None
    assert attempt.dataset.observations[0].tokens.input_tokens == 5


def test_cursor_sync_respects_refresh_ttl(tmp_path: Path) -> None:
    cache = tmp_path / "cursor-cache"
    cache.mkdir()
    (cache / "usage.work.csv").write_text(_V1_CSV, encoding="utf-8")
    executable = tmp_path / "token-stats.exe"
    executable.write_text("", encoding="utf-8")
    os.utime(cache / "usage.work.csv", (10_000.0, 10_000.0))

    def boom_runner(*_args, **_kwargs):
        raise AssertionError("sync must not run while the cache is fresh")

    settings = CollectorSettings(
        token_stats_executable=executable,
        enable_cursor_sync=True,
        cursor_sync_ttl_seconds=300.0,
    )
    attempt = collect_cursor_export(
        account="work",
        settings=settings,
        runner=boom_runner,
        clock=lambda: 10_100.0,
        cache_dirs=(cache,),
    )
    assert attempt.usable


def test_legacy_cursor_csv_and_json_imports_still_work(tmp_path: Path) -> None:
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
    assert item.tokens.total_tokens == 45
    assert item.tokens.reasoning_output_tokens == 3

    payload = {
        "schema": COLLECTOR_SCHEMA,
        "observations": [
            {
                "provider": "cursor",
                "account": "work",
                "model": "grok-4.6",
                "observed_at": "2026-09-11T01:00:00Z",
                "input_tokens": 8,
                "output_tokens": 2,
            }
        ],
    }
    imported = collect_cursor_export(account="work", payload=payload)
    assert imported.usable
    assert imported.dataset is not None
    assert imported.dataset.observations[0].tokens.total_tokens == 10


def test_cursor_reported_cost_is_optional_and_not_list_price(tmp_path: Path) -> None:
    assert parse_cursor_reported_cost("Included") == Decimal("0")
    assert parse_cursor_reported_cost("-") == Decimal("0")
    assert parse_cursor_reported_cost("$0.10") == Decimal("0.10")
    assert parse_cursor_reported_cost("not-a-price") is None
    assert parse_cursor_reported_cost("") is None
    assert parse_cursor_reported_cost(None) is None

    v1 = tmp_path / "usage.work.csv"
    v1.write_text(_V1_CSV, encoding="utf-8")
    v1_rows = parse_cursor_csv_result(v1, account="work")
    assert v1_rows.usable
    assert v1_rows.rows[0].reported_cost is not None
    assert v1_rows.rows[0].reported_cost.source == "cost_to_you"
    assert v1_rows.rows[0].reported_cost.usd == Decimal("0.10")
    assert v1_rows.attempt.dataset is not None
    assert not hasattr(v1_rows.attempt.dataset.observations[0], "reported_cost")

    v2 = tmp_path / "usage.api.csv"
    v2.write_text(_V2_CSV, encoding="utf-8")
    v2_rows = parse_cursor_csv_result(v2, account="api")
    assert v2_rows.rows[0].reported_cost is not None
    assert v2_rows.rows[0].reported_cost.usd == Decimal("0.19")

    v3 = tmp_path / "usage.v3acct.csv"
    v3.write_text(
        (FIXTURES / "cursor_usage_events_v3.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    v3_rows = parse_cursor_csv_result(v3, account="v3acct")
    assert [row.reported_cost.usd if row.reported_cost else None for row in v3_rows.rows] == [
        Decimal("0"),
        Decimal("0.11"),
        Decimal("0"),
    ]

    legacy = tmp_path / "usage.legacy.csv"
    legacy.write_text(
        "date,model,input,output,cacheRead,cacheWrite,reasoning\n"
        "2026-09-11,grok-4.6,30,10,4,1,3\n",
        encoding="utf-8",
    )
    legacy_rows = parse_cursor_csv_result(legacy, account="legacy")
    assert legacy_rows.usable
    assert legacy_rows.rows[0].reported_cost is None
