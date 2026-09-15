from __future__ import annotations

import os
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def test_metric_selector_persists_independently_from_account_order() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.core.models import DisplayMode, MetricMode
    from quotadeck.usage.models import CostCurrency, UsagePeriod

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config.ui_language = "ko"
    window.apply_language()
    cumulative = window.metric_combo.findData(MetricMode.CUMULATIVE.value)
    fixed = window.mode.findData(DisplayMode.FIXED.value)
    daily = window.period.findData(UsagePeriod.DAILY.value)
    monthly = window.period.findData(UsagePeriod.MONTHLY.value)
    krw = window.currency.findData(CostCurrency.KRW.value)
    usd = window.currency.findData(CostCurrency.USD.value)
    assert cumulative >= 0
    assert fixed >= 0
    assert daily >= 0 and monthly >= 0
    assert krw >= 0 and usd >= 0
    assert window.currency.itemText(krw) == "KRW (만원)"

    window.metric_combo.setCurrentIndex(cumulative)
    window.mode.setCurrentIndex(fixed)
    window.period.setCurrentIndex(daily)
    window.currency.setCurrentIndex(usd)
    assert not window.period_row.isHidden()
    assert not window.currency_row.isHidden()
    assert window.fx_row.isHidden()
    assert window.krw_unit_hint.isHidden()
    config = window.collect_config()
    assert config.metric_mode is MetricMode.CUMULATIVE
    assert config.display_mode is DisplayMode.FIXED
    assert config.cumulative_period is UsagePeriod.DAILY
    assert config.cost_currency is CostCurrency.USD
    assert window.price_hint.isHidden()
    assert window.fx_row.isHidden()

    window.currency.setCurrentIndex(krw)
    window.exchange_rate.setValue(1555)
    assert not window.fx_row.isHidden()
    assert window.krw_unit_hint.isHidden()
    assert window.krw_unit_hint.text() == "1K = 천만원 · 1M = 백억 · 1B = 10조"
    assert window.fx_auto.isChecked()
    assert config.fx_auto is True
    config = window.collect_config()
    assert config.cost_currency is CostCurrency.KRW
    assert config.usd_to_krw_rate == 1555.0

    quota = window.metric_combo.findData(MetricMode.QUOTA.value)
    window.metric_combo.setCurrentIndex(quota)
    assert window.period_row.isHidden()
    assert window.currency_row.isHidden()
    assert window.fx_row.isHidden()
    assert window.krw_unit_hint.isHidden()
    window.close()
    assert app is not None


def test_period_and_currency_labels_survive_language_refresh() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.core.models import MetricMode
    from quotadeck.usage.models import CostCurrency, UsagePeriod

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.metric_combo.setCurrentIndex(window.metric_combo.findData(MetricMode.CUMULATIVE.value))
    window.period.setCurrentIndex(window.period.findData(UsagePeriod.DAILY.value))
    window.currency.setCurrentIndex(window.currency.findData(CostCurrency.KRW.value))
    window.exchange_rate.setValue(1490)
    window.config.ui_language = "en"
    window.apply_language()

    assert window.period.currentData() == UsagePeriod.DAILY.value
    assert window.currency.currentData() == CostCurrency.KRW.value
    assert window.currency.currentText() == "KRW (10,000 won)"
    assert window.exchange_rate.value() == 1490
    assert window.krw_unit_hint.text() == (
        "1K = 10 million won · 1M = 10 billion won · 1B = 10 trillion won"
    )
    assert window.krw_unit_hint.isHidden()
    assert "treasury" in window.exchange_rate.toolTip().lower()
    assert "not a realtime spot" in window.exchange_rate.toolTip().lower()
    assert "treasury" in window.price_hint.text().lower()
    assert "not a realtime spot" in window.price_hint.text().lower()
    window.close()
    assert app is not None


def test_v4_config_migrates_period_currency_and_manual_rate(tmp_path) -> None:
    import json

    from quotadeck.config import CONFIG_VERSION, load_config, save_config
    from quotadeck.usage.models import CostCurrency, UsagePeriod

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"config_version": 4, "metric_mode": "cumulative"}),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.cumulative_period is UsagePeriod.MONTHLY
    assert config.cost_currency is CostCurrency.KRW
    assert config.usd_to_krw_rate == 1400.0
    assert config.fx_auto is True

    config.usd_to_krw_rate = float("inf")
    save_config(config, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config_version"] == CONFIG_VERSION == 5
    assert saved["cumulative_period"] == "monthly"
    assert saved["cost_currency"] == "krw"
    assert saved["usd_to_krw_rate"] == 1400.0


def test_account_row_switches_from_percent_to_cumulative_tokens() -> None:
    from datetime import date, timedelta

    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import AccountRow
    from quotadeck.config import AccountConfig
    from quotadeck.usage.display import CumulativeSnapshot
    from quotadeck.usage.models import (
        CostCurrency,
        DailyUsage,
        ModelUsage,
        TokenUsage,
        UsageComparison,
        UsageIntensity,
        UsageReport,
    )

    app = QApplication.instance() or QApplication([])
    row = AccountRow(AccountConfig("codex", "one", "ONE"))
    today = date(2026, 9, 11)
    first = ModelUsage("codex", "gpt-5.6-sol", TokenUsage(input_tokens=6_000))
    second = ModelUsage("codex", "gpt-5.6-terra", TokenUsage(input_tokens=4_000))
    third = ModelUsage("codex", "gpt-5.6-luna", TokenUsage(input_tokens=2_345))
    models = (first, second, third)
    tokens = TokenUsage.sum(item.tokens for item in models)
    report = UsageReport(
        start_day=today - timedelta(days=7),
        end_day=today,
        requested_days=365,
        daily=(DailyUsage(today, tokens, models),),
        model_totals=models,
        tokens=tokens,
        today_comparison=UsageComparison(
            today_tokens=12_345,
            prior_daily_average=8_230,
            ratio=1.5,
            intensity=UsageIntensity.ABOVE_1_5X,
            history_days=7,
            minimum_history_days=7,
        ),
    )
    snapshot = CumulativeSnapshot(
        provider="codex",
        account_id="one",
        display_name="ONE",
        plan="api",
        report=report,
        source_label="THIS DEVICE",
        this_cost_usd=Decimal("0.12"),
        average_cost_usd=Decimal("0.08"),
    )
    row.set_snapshot(snapshot)
    assert row.remaining.text() == "12K"
    assert "%" not in row.remaining.text()
    assert "1.5X" in row.meta.text()
    assert "12K / 8.2K" in row.meta.text()
    assert "LIST USD THIS 0.12 / AVG 0.08" in row.meta.toolTip()
    assert "GPT-5.6-SOL" in row.meta.toolTip().upper()
    assert "GPT-5.6-TERRA" in row.meta.toolTip().upper()
    assert "gpt-5.6-luna: 2,345 tokens" in row.meta.toolTip().lower()

    row.set_snapshot(
        snapshot,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400.0,
    )
    assert "LIST KRW (만원) THIS" in row.meta.toolTip()
    assert app is not None


def test_quota_cumulative_round_trip_restores_cached_rows() -> None:
    from datetime import date, datetime, timedelta, timezone
    from decimal import Decimal

    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig
    from quotadeck.core.models import MetricMode, UsageSnapshot, UsageWindow
    from quotadeck.usage.display import CumulativeSnapshot
    from quotadeck.usage.models import (
        DailyUsage,
        ModelUsage,
        TokenUsage,
        UsageComparison,
        UsageIntensity,
        UsagePeriod,
        UsageReport,
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("codex", "one", "ONE", True, source_label="Codex CLI")]
    )
    window.reload_accounts()
    window.metric_combo.setCurrentIndex(window.metric_combo.findData(MetricMode.QUOTA.value))
    window.period.setCurrentIndex(window.period.findData(UsagePeriod.DAILY.value))
    quota = UsageSnapshot(
        "codex",
        "one",
        "ONE",
        "plus",
        [UsageWindow("session", "5H", 40, 60)],
        "ok",
        datetime.now(timezone.utc),
    )
    today = date(2026, 9, 11)
    tokens = TokenUsage(input_tokens=12_345)
    report = UsageReport(
        start_day=today - timedelta(days=7),
        end_day=today,
        requested_days=365,
        daily=(DailyUsage(today, tokens, (ModelUsage("codex", "gpt-5.6-sol", tokens),)),),
        model_totals=(ModelUsage("codex", "gpt-5.6-sol", tokens),),
        tokens=tokens,
        today_comparison=UsageComparison(
            today_tokens=12_345,
            prior_daily_average=8_230,
            ratio=1.5,
            intensity=UsageIntensity.ABOVE_1_5X,
            history_days=7,
            minimum_history_days=7,
        ),
    )
    cumulative = CumulativeSnapshot(
        provider="codex",
        account_id="one",
        display_name="ONE",
        plan="api",
        report=report,
        source_label="THIS DEVICE",
        period=UsagePeriod.DAILY,
        this_cost_usd=Decimal("0.12"),
        average_cost_usd=Decimal("0.08"),
    )
    window.runtime.remember_snapshots([quota])
    window.runtime.remember_snapshots([cumulative])
    window._restore_cached_snapshots()
    assert window.rows[0].remaining.text() == "60%"

    window.metric_combo.setCurrentIndex(window.metric_combo.findData(MetricMode.CUMULATIVE.value))
    assert window.rows[0].remaining.text() == "12K"

    window.metric_combo.setCurrentIndex(window.metric_combo.findData(MetricMode.QUOTA.value))
    assert window.rows[0].remaining.text() == "60%"
    window.close()
    assert app is not None


def test_detect_restores_cached_rows_before_save(monkeypatch) -> None:
    from datetime import datetime, timezone

    from PySide6.QtWidgets import QApplication

    import quotadeck.app.main_window as main_window
    from quotadeck.config import AccountConfig, AppConfig
    from quotadeck.core.models import AccountRef, MetricMode, UsageSnapshot, UsageWindow

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("codex", "one", "OLD", True, source_label="Codex CLI")]
    )
    window.reload_accounts()
    window.metric_combo.setCurrentIndex(window.metric_combo.findData(MetricMode.QUOTA.value))
    window.rows[0].alias.setText("KEEP")
    window.runtime.remember_snapshots(
        [
            UsageSnapshot(
                "codex",
                "one",
                "ONE",
                "plus",
                [UsageWindow("session", "5H", 10, 90)],
                "ok",
                datetime.now(timezone.utc),
            )
        ]
    )
    saved: list[object] = []
    monkeypatch.setattr(main_window, "save_config", lambda config: saved.append(config))
    monkeypatch.setattr(
        main_window,
        "discover_accounts",
        lambda: [
            AccountRef(
                provider="codex",
                account_id="one",
                display_name="ONE",
                source_path="codex",
                source_kind="cli",
                source_label="Codex CLI",
            )
        ],
    )
    window.detect()
    assert saved == []
    assert window.rows[0].alias.text() == "KEEP"
    assert window.rows[0].remaining.text() == "90%"
    window.close()
    assert app is not None
