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
    cumulative = window.metric.findData(MetricMode.CUMULATIVE.value)
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

    window.metric.setCurrentIndex(cumulative)
    window.mode.setCurrentIndex(fixed)
    window.period.setCurrentIndex(daily)
    window.currency.setCurrentIndex(usd)
    assert not window.period.isHidden()
    assert not window.currency.isHidden()
    assert window.exchange_rate.isHidden()
    assert window.krw_unit_hint.isHidden()
    config = window.collect_config()
    assert config.metric_mode is MetricMode.CUMULATIVE
    assert config.display_mode is DisplayMode.FIXED
    assert config.cumulative_period is UsagePeriod.DAILY
    assert config.cost_currency is CostCurrency.USD
    assert not window.price_hint.isHidden()

    window.currency.setCurrentIndex(krw)
    window.exchange_rate.setValue(1555)
    assert not window.exchange_rate.isHidden()
    assert not window.krw_unit_hint.isHidden()
    assert window.krw_unit_hint.text() == "1K = 천만원 · 1M = 백억 · 1B = 10조"
    config = window.collect_config()
    assert config.cost_currency is CostCurrency.KRW
    assert config.usd_to_krw_rate == 1555.0

    quota = window.metric.findData(MetricMode.QUOTA.value)
    window.metric.setCurrentIndex(quota)
    assert window.period.isHidden()
    assert window.currency.isHidden()
    assert window.exchange_rate.isHidden()
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
    window.metric.setCurrentIndex(window.metric.findData(MetricMode.CUMULATIVE.value))
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
    assert not window.krw_unit_hint.isHidden()
    assert "not fetched" in window.exchange_rate.toolTip().lower()
    assert "not fetched automatically" in window.price_hint.text().lower()
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
    assert "LIST USD THIS 0.12 / AVG 0.08" in row.meta.text()
    assert "GPT-5.6-SOL 6.0K" in row.meta.text()
    assert "GPT-5.6-TERRA 4.0K" in row.meta.text()
    assert "GPT-5.6-LUNA" not in row.meta.text()
    assert "gpt-5.6-luna: 2,345 tokens" in row.meta.toolTip().lower()

    row.set_snapshot(
        snapshot,
        currency=CostCurrency.KRW,
        usd_to_krw_rate=1400.0,
    )
    assert "LIST KRW (만원) THIS" in row.meta.text()
    assert app is not None
