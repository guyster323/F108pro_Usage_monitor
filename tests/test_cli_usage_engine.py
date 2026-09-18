from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from quotadeck import cli
from quotadeck.config import AccountConfig, AppConfig, CursorUsageBinding
from quotadeck.core.models import AccountRef
from quotadeck.usage.engine import UsageEngineIssue
from quotadeck.usage.normalized import NormalizedUsageRecord, UsageConfidence


def test_usage_engine_json_uses_configured_cursor_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    export = tmp_path / "configured.csv"
    export.write_text(
        "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),"
        "Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
        f"{date.today().isoformat()},grok-4.6,1000,1000,0,500,1500,$0.25,$0.10\n",
        encoding="utf-8",
    )
    config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "WORK")],
        cursor_bindings=[
            CursorUsageBinding(
                account_id="work",
                source="csv",
                csv_path=str(export),
            )
        ],
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        cli,
        "discover_accounts",
        lambda: [
            AccountRef(
                provider="cursor",
                account_id="work",
                display_name="discovered",
                source_path=str(tmp_path / "state.vscdb"),
            )
        ],
    )

    args = argparse.Namespace(cursor_sync=False, no_live_fx=True, json=True)
    assert cli.cmd_usage_engine(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["account_totals"]) == 1
    total = payload["account_totals"][0]
    assert total["account"] == "work"
    assert total["total_tokens"] == 1500
    assert payload["records"][0]["source"] == "cursor_export"


def test_usage_engine_cli_exposes_stale_loads_in_json_and_text(
    monkeypatch,
    capsys,
) -> None:
    config = AppConfig()
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "discover_accounts", lambda: [])
    report = SimpleNamespace(
        records=(),
        account_totals=(),
        provider_totals=(),
        global_total=NormalizedUsageRecord(
            provider="all",
            source="global_total",
            confidence=UsageConfidence.PARTIAL,
        ),
        issues=(
            UsageEngineIssue(
                "cursor",
                "work",
                "stale",
                "Cursor Admin API connected; no current-user token events yet.",
                True,
            ),
        ),
    )
    monkeypatch.setattr(
        "quotadeck.usage.engine.collect_normalized_usage",
        lambda *_args, **_kwargs: report,
    )

    args = argparse.Namespace(cursor_sync=False, no_live_fx=True, json=True)
    assert cli.cmd_usage_engine(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"] == [
        {
            "provider": "cursor",
            "account": "work",
            "code": "stale",
            "reason": "Cursor Admin API connected; no current-user token events yet.",
            "stale": True,
        }
    ]

    args.json = False
    assert cli.cmd_usage_engine(args) == 0
    assert "cursor/work:stale (Cursor Admin API connected; no current-user token events yet.)" in capsys.readouterr().out
