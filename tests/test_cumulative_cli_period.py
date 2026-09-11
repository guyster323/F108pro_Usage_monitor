from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quotadeck import cli
from quotadeck.config import AppConfig
from quotadeck.core.models import MetricMode
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import CostCurrency, UsagePeriod


@pytest.mark.parametrize(
    "period,period_label",
    (
        (UsagePeriod.DAILY, "DAILY"),
        (UsagePeriod.MONTHLY, "MONTH"),
    ),
)
def test_cumulative_cli_passes_saved_period_and_labels_this_average_ratio(
    period: UsagePeriod,
    period_label: str,
    monkeypatch,
    capsys,
) -> None:
    """The CLI must not silently fall back to its old daily/TOTAL semantics."""

    config = AppConfig(
        metric_mode=MetricMode.CUMULATIVE,
        cumulative_period=period,
        cost_currency=CostCurrency.USD,
        usd_to_krw_rate=1555,
    )
    observed: dict[str, object] = {}

    class FakeCumulativeService:
        def snapshots(self, accounts, *, period):
            observed["accounts"] = accounts
            observed["period"] = period
            return [
                SimpleNamespace(
                    provider="codex",
                    display_name="WORK",
                    period=period,
                    available=True,
                    this_tokens=600_000_000,
                    average_tokens=1_200_000_000,
                    ratio=0.5,
                    this_cost_usd=Decimal("400"),
                    average_cost_usd=Decimal("800"),
                    source_label="LOCAL OBSERVED",
                    report=None,
                )
            ]

    class FakeRuntime:
        def __init__(self, loaded_config):
            observed["config"] = loaded_config
            self.cumulative = FakeCumulativeService()

        def selected_accounts(self):
            return ("selected-account",)

    import quotadeck.core.scheduler as scheduler

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(scheduler, "QuotaDeckRuntime", FakeRuntime)

    assert cli.cmd_cumulative(argparse.Namespace(models=False)) == 0

    assert observed["config"] is config
    assert observed["accounts"] == ("selected-account",)
    assert observed["period"] is period
    output = capsys.readouterr().out
    header, row = output.splitlines()
    assert "THIS" in header
    assert "AVG" in header
    assert "RATIO" in header
    assert "TOTAL" not in header
    assert period_label in row
    assert "0.6B" in row
    assert "1.2B" in row
    assert "50%" in row
    assert "0.4K" in row
    assert "0.8K" in row


def test_render_passes_saved_currency_and_exchange_rate_to_cumulative_renderer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """A preview render must use the same saved money presentation as the LCD."""

    config = AppConfig(
        metric_mode=MetricMode.CUMULATIVE,
        cumulative_period=UsagePeriod.MONTHLY,
        cost_currency=CostCurrency.KRW,
        usd_to_krw_rate=1555,
        scene_hold_seconds=7,
    )
    snapshot = CumulativeSnapshot.unsupported(
        provider="codex",
        account_id="work",
        display_name="WORK",
        period=UsagePeriod.MONTHLY,
    )
    observed: dict[str, object] = {}
    frames = [object()]
    theme = object()

    class FakeRuntime:
        def __init__(self, loaded_config, *, mock=False):
            observed["runtime_config"] = loaded_config
            observed["mock"] = mock

        def poll(self):
            observed["polled"] = True
            return [snapshot]

    def fake_render_cumulative(snapshots, loaded_theme, **kwargs):
        observed["snapshots"] = snapshots
        observed["theme"] = loaded_theme
        observed["render_kwargs"] = kwargs
        return frames

    def fail_quota_render(*_args, **_kwargs):
        raise AssertionError("quota renderer must not run in cumulative mode")

    def fake_write_gif(rendered_frames, out):
        observed["written_frames"] = rendered_frames
        observed["out"] = out

    import quotadeck.core.scheduler as scheduler
    import quotadeck.renderer.scenes as scenes
    import quotadeck.renderer.sprites as sprites

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "default_theme_dir", lambda: tmp_path / "theme")
    monkeypatch.setattr(cli, "write_gif", fake_write_gif)
    monkeypatch.setattr(scheduler, "QuotaDeckRuntime", FakeRuntime)
    monkeypatch.setattr(scenes, "render_cumulative_playlist", fake_render_cumulative)
    monkeypatch.setattr(scenes, "render_playlist", fail_quota_render)
    monkeypatch.setattr(sprites, "load_theme", lambda _path: theme)

    out = tmp_path / "preview.gif"
    args = argparse.Namespace(
        fixture=None,
        metric=None,
        theme=None,
        hold_seconds=None,
        mode=None,
        budget=12,
        out=str(out),
    )
    assert cli.cmd_render(args) == 0

    assert observed["mock"] is True
    assert observed["polled"] is True
    assert observed["snapshots"] == [snapshot]
    assert observed["theme"] is theme
    kwargs = observed["render_kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["currency"] is CostCurrency.KRW
    assert kwargs["usd_to_krw_rate"] == 1555
    assert kwargs["hold_ms"] == 7000
    assert observed["written_frames"] is frames
    assert observed["out"] == out
    assert f"wrote {out} (1 frames)" in capsys.readouterr().out


def test_cumulative_cli_labels_krw_values_as_ten_thousand_won_units(
    monkeypatch,
    capsys,
) -> None:
    config = AppConfig(
        metric_mode=MetricMode.CUMULATIVE,
        cumulative_period=UsagePeriod.MONTHLY,
        cost_currency=CostCurrency.KRW,
        usd_to_krw_rate=1000,
    )

    class FakeCumulativeService:
        def snapshots(self, accounts, *, period):
            return [
                SimpleNamespace(
                    provider="codex",
                    display_name="WORK",
                    period=period,
                    available=True,
                    this_tokens=1,
                    average_tokens=2,
                    ratio=0.5,
                    this_cost_usd=Decimal("10000"),
                    average_cost_usd=Decimal("20000"),
                    source_label="LOCAL OBSERVED",
                    report=None,
                )
            ]

    class FakeRuntime:
        def __init__(self, loaded_config):
            assert loaded_config is config
            self.cumulative = FakeCumulativeService()

        def selected_accounts(self):
            return ()

    import quotadeck.core.scheduler as scheduler

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(scheduler, "QuotaDeckRuntime", FakeRuntime)

    assert cli.cmd_cumulative(argparse.Namespace(models=False)) == 0
    output = capsys.readouterr().out
    assert "LIST KRW(10K) THIS 1K / AVG 2K" in output
    assert "천" not in output
