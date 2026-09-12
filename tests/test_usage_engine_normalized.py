from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from quotadeck.core.models import AccountRef
from quotadeck.usage.collectors import CollectorSettings
from quotadeck.usage.engine import collect_normalized_usage
from quotadeck.usage.fx import FxFallback, FxRateQuote


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
