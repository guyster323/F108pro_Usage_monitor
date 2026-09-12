from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from quotadeck.core.models import AccountRef
from quotadeck.usage.collectors import CollectorSettings
from quotadeck.usage.engine import collect_normalized_usage
from quotadeck.usage.fx import FxFallback, FxRateQuote
from quotadeck.usage.grok import GrokUsageScan, grok_session_record, parse_grok_usage_payload
from quotadeck.usage.models import UsageDataset
from quotadeck.usage.normalized import UsageConfidence


def test_cursor_report_separates_reported_and_api_equivalent_cost(
    tmp_path: Path,
) -> None:
    export = tmp_path / "usage.work.csv"
    export.write_text(
        "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),"
        "Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
        f"{date.today().isoformat()},grok-4.6,1000,1000,0,500,1500,$0.25,$0.10\n",
        encoding="utf-8",
    )
    account = AccountRef(
        provider="cursor",
        account_id="work",
        display_name="WORK",
        source_path="ignored-state.vscdb",
    )
    fx = FxRateQuote(
        usd_to_krw=Decimal("1400"),
        source="unit",
        fallback=FxFallback.MANUAL,
    )
    report = collect_normalized_usage(
        (account,),
        collector=CollectorSettings(cursor_export_path=export),
        fx=fx,
    )

    assert len(report.records) == 1
    row = report.records[0]
    assert row.input_tokens == 1000
    assert row.cache_write_tokens == 0
    assert row.cache_read_tokens == 0
    assert row.reasoning_tokens is None
    assert row.total_tokens == 1500
    assert row.reported_cost_usd == Decimal("0.10")
    assert row.api_equivalent_cost_usd is not None
    assert row.api_equivalent_cost_usd != row.reported_cost_usd
    assert row.api_equivalent_cost_krw == row.api_equivalent_cost_usd * 1400

    account_total = report.account_totals[0]
    provider_total = report.provider_totals[0]
    assert account_total.total_tokens == 1500
    assert provider_total.total_tokens == 1500
    assert report.global_total.total_tokens == 1500
    assert report.global_total.reasoning_tokens is None


def test_missing_usage_is_an_issue_not_a_zero_total(tmp_path: Path) -> None:
    account = AccountRef(
        provider="cursor",
        account_id="personal",
        display_name="PERSONAL",
        source_path="ignored-state.vscdb",
    )
    report = collect_normalized_usage(
        (account,),
        collector=CollectorSettings(cursor_export_path=tmp_path / "missing.csv"),
    )

    assert report.records == ()
    assert report.account_totals == ()
    assert report.global_total.total_tokens is None
    assert report.issues[0].code in {"unsupported", "unavailable", "error"}


def _codex_cumulative_row(timestamp: str, *, raw_input: int, output: int) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": raw_input,
                    "cached_input_tokens": 0,
                    "output_tokens": output,
                    "reasoning_output_tokens": 0,
                    "total_tokens": raw_input + output,
                }
            },
        },
    }


def _grok_payload(session_id: str) -> dict[str, object]:
    summary = {
        "inputTokens": 120,
        "outputTokens": 30,
        "totalTokens": 150,
        "cachedReadTokens": 20,
        "cacheCreationTokens": 10,
        "reasoningTokens": 5,
        "modelCalls": 2,
        "turnCount": 1,
        "primaryModelId": "grok-4.6",
        "costUsdTicks": 25_000_000_000,
        "modelUsage": {
            "grok-4.6": {
                "inputTokens": 120,
                "outputTokens": 30,
                "totalTokens": 150,
                "cachedReadTokens": 20,
                "cacheCreationTokens": 10,
                "reasoningTokens": 5,
                "modelCalls": 2,
                "turnCount": 1,
                "primaryModelId": "grok-4.6",
            }
        },
    }
    return {
        "sessionId": session_id,
        "updatedAt": "2026-09-11T12:34:56Z",
        "session": summary,
        "turns": [],
    }


def test_collect_normalized_usage_reconciles_codex_cumulative_snapshots(
    tmp_path: Path,
) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": "session-1"}},
        _codex_cumulative_row("2026-09-10T12:00:00Z", raw_input=100, output=20),
        _codex_cumulative_row("2026-09-11T12:00:00Z", raw_input=160, output=30),
    ]
    path = tmp_path / "sessions" / "rollout.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    account = AccountRef(
        provider="codex",
        account_id="codex-1",
        display_name="CODEX",
        source_path=str(tmp_path),
    )
    report = collect_normalized_usage(
        (account,),
        collector=CollectorSettings(enable_token_stats=False, enable_ccusage=False),
    )

    totals = [row.total_tokens for row in report.records]
    assert totals == [120, 70]
    assert sum(totals) == 190
    assert report.account_totals[0].total_tokens == 190
    assert report.global_total.total_tokens == 190
    assert report.load_results[0].report is not None
    assert report.load_results[0].report.tokens.total_tokens == 190


def test_collect_normalized_usage_does_not_rollup_grok_sessions_as_events(
    monkeypatch,
) -> None:
    first = grok_session_record(
        parse_grok_usage_payload(_grok_payload("session-a")), account="g1"
    )
    second = grok_session_record(
        parse_grok_usage_payload(_grok_payload("session-b")), account="g1"
    )
    monkeypatch.setattr(
        "quotadeck.usage.engine.scan_grok_session_usage",
        lambda **_kwargs: GrokUsageScan(
            sessions=(),
            dataset=UsageDataset((), ()),
            session_records=(first, second),
        ),
    )
    account = AccountRef(
        provider="grok",
        account_id="g1",
        display_name="GROK",
        source_path="missing-grok-home",
    )
    report = collect_normalized_usage((account,))

    assert len(report.records) == 2
    assert {row.session for row in report.records} == {"session-a", "session-b"}
    total = report.account_totals[0]
    assert total.total_tokens == 300
    assert total.reported_cost_usd is None
    assert total.api_equivalent_cost_usd is None
    assert total.confidence is not UsageConfidence.EXACT
    assert any("not an account-wide ledger" in item for item in total.limitations)
    assert report.global_total.reported_cost_usd is None
    assert report.grok_scans
