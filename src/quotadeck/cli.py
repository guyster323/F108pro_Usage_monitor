from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from quotadeck import __version__
from quotadeck.config import (
    account_config_from_ref,
    default_flash_state_path,
    default_theme_dir,
    load_config,
    save_config,
)
from quotadeck.core.flashbudget import FlashBudget, lock_flash_state
from quotadeck.core.mask import mask_text, safe_display_text
from quotadeck.devices.aula_f108.constants import LCD_MAX_FRAMES
from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok
from quotadeck.devices.aula_f108.payload import Frame, hex_to_rgb, solid_frame
from quotadeck.discovery.accounts import discover_accounts
from quotadeck.gifio import load_gif
from quotadeck.providers.base import all_providers
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.sprites import validate_theme

def _setup_logging() -> None:
    from quotadeck.diagnostics import configure_diagnostics

    configure_diagnostics("scheduler", console=sys.stderr is not None)


def _account_seconds(raw: str) -> float:
    """Argparse type for an exact, finite firmware-compatible account slot."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or not 2.0 <= value <= 20.0:
        raise argparse.ArgumentTypeError("must be a finite value from 2 to 20 seconds")
    ticks = value * 50.0
    if not math.isclose(ticks, round(ticks), abs_tol=1e-9):
        raise argparse.ArgumentTypeError("must use 0.02-second (20 ms) increments")
    return round(ticks) / 50.0

def cmd_probe(_args: argparse.Namespace) -> int:
    running = aula_software_running()
    if running:
        print(f"warning: official AULA software is running ({', '.join(running)}). Close it to avoid HID conflicts.")
    interfaces = enumerate_interfaces()
    if not interfaces:
        print("No AULA F108 Pro HID interfaces found (VID 0x0C45 PID 0x800A).")
        print("Connect USB-C and press Fn+4 for wired mode.")
        return 1
    for item in interfaces:
        print(f"usagePage=0x{item.usage_page:04x} usage=0x{item.usage:04x} product={item.product}")
    if wired_mode_ok(interfaces):
        print("wired LCD interface: ok")
        return 0
    print("LCD interface missing. Use USB-C mode (Fn+4), not Bluetooth/2.4G.")
    return 2

def cmd_clock(args: argparse.Namespace) -> int:
    from quotadeck.devices.aula_f108.device import F108Device

    if args.mock:
        from quotadeck.devices.aula_f108.transport_mock import MockTransport
        from quotadeck.devices.aula_f108.protocol import sync_clock
        when = sync_clock(MockTransport())
        print(f"clock synced (mock) {when.isoformat()}")
        return 0
    # Clock traffic does not consume the GIF wear budget, but it shares the
    # same device mutex so it cannot collide with an app/CLI frame transfer.
    with lock_flash_state(default_flash_state_path()):
        with F108Device() as device:
            when = device.sync_clock()
    print(f"Synced F108 clock to {when.isoformat()}")
    return 0

def cmd_upload(args: argparse.Namespace) -> int:
    from quotadeck.devices.aula_f108.device import F108Device
    from quotadeck.devices.aula_f108.payload import build_payload
    from quotadeck.devices.aula_f108.transport_mock import MockTransport
    from quotadeck.devices.aula_f108.protocol import upload_payload
    if args.solid:
        r, g, b = hex_to_rgb(args.solid)
        frames = [solid_frame(r, g, b, delay_ms=1000)]
    elif args.gif:
        frames = load_gif(Path(args.gif))
    else:
        print("provide --solid RRGGBB or a GIF path")
        return 2
    if len(frames) > LCD_MAX_FRAMES:
        print(f"refusing {len(frames)} frames (hard limit {LCD_MAX_FRAMES})")
        return 2

    def progress(cur: int, total: int, phase: str) -> None:
        print(f"{phase} {cur}/{total}")
    if args.mock:
        payload = build_payload(frames)
        upload_payload(MockTransport(), payload, progress)
        print(f"mock upload ok ({len(frames)} frames, {len(payload)} bytes)")
        return 0
    running = aula_software_running()
    if running:
        print(f"warning: {', '.join(running)} is running")
    config = load_config()
    state_path = default_flash_state_path()
    budget = FlashBudget(
        min_interval=timedelta(minutes=config.min_upload_minutes),
        max_age=timedelta(minutes=config.max_age_minutes),
        daily_limit=config.daily_flash_limit,
    )
    with lock_flash_state(state_path):
        if state_path.exists() and not budget.restore(state_path):
            print("refusing upload: flash state is unreadable", file=sys.stderr)
            return 1
        now = datetime.now(timezone.utc)
        allowed, reason = budget.can_upload(now, force=True)
        if not allowed:
            print(f"refusing upload: {reason}", file=sys.stderr)
            return 1
        budget.record(now)
        try:
            budget.persist(state_path)
        except (OSError, ValueError, TypeError):
            print(
                "refusing upload: flash state could not be reserved",
                file=sys.stderr,
            )
            return 1
        with F108Device() as device:
            device.upload_frames(frames, progress)
    print(f"uploaded {len(frames)} frames")
    return 0

def cmd_usage(args: argparse.Namespace) -> int:
    if getattr(args, "cumulative", False):
        return cmd_cumulative(args)
    if getattr(args, "models", False):
        print("--models requires --cumulative", file=sys.stderr)
        return 2
    print(f"{'PROVIDER':<8} {'SRC':<4} {'ACCOUNT':<16} {'PLAN':<10} {'STATUS':<12} WINDOWS")
    for provider in all_providers():
        for account in provider.discover():
            snap = provider.fetch(account)
            windows = ", ".join(
                f"{w.label} {int(round(w.remaining_percent))}%" for w in snap.windows
            ) or snap.error or "-"
            print(
                f"{snap.provider:<8} {account.source_badge:<4} {snap.display_name:<16} "
                f"{(snap.plan or '-'):<10} {snap.status:<12} {mask_text(windows)}"
            )
    return 0


def cmd_cumulative(args: argparse.Namespace) -> int:
    # Keep unrelated commands/help available even in a minimal installation
    # that does not contain the optional cumulative-history pipeline.
    try:
        from quotadeck.core.scheduler import QuotaDeckRuntime
    except ImportError as exc:
        print(f"cumulative usage unavailable: {mask_text(str(exc))}", file=sys.stderr)
        return 2

    from quotadeck.renderer.canvas import usage_percent_label
    from quotadeck.renderer.layout import (
        compact_cost_pair,
        compact_token_count,
        compact_token_pair,
    )
    from quotadeck.usage.models import CostCurrency

    config = load_config()
    runtime = QuotaDeckRuntime(config)
    snapshots = runtime.cumulative.snapshots(
        runtime.selected_accounts(),
        period=config.cumulative_period,
    )
    print(
        f"{'PROVIDER':<8} {'ACCOUNT':<16} {'PERIOD':<7} {'THIS':>8} "
        f"{'AVG':>8} {'RATIO':>6} SOURCE / LIST COST"
    )
    for snap in snapshots:
        period_label = "DAILY" if snap.period.value == "daily" else "MONTH"
        if not snap.available:
            detail = safe_display_text(
                mask_text(snap.source_label or snap.error or "N/A"),
                max_length=160,
                fallback="N/A",
            )
            print(
                f"{snap.provider:<8} {snap.display_name:<16} {period_label:<7} "
                f"{'N/A':>8} {'N/A':>8} {'N/A':>6} {detail}"
            )
            continue
        this_text, average_text = compact_token_pair(
            snap.this_tokens,
            snap.average_tokens,
        )
        ratio = snap.ratio
        ratio_text = usage_percent_label(
            None if ratio is None else ratio * 100.0
        )
        this_cost, average_cost = compact_cost_pair(
            snap.this_cost_usd,
            snap.average_cost_usd,
            currency=config.cost_currency,
            usd_to_krw_rate=config.usd_to_krw_rate,
        )
        cost_unit = (
            "KRW(10K)"
            if config.cost_currency is CostCurrency.KRW
            else config.cost_currency.value.upper()
        )
        scope = (
            f"{snap.source_label} · LIST {cost_unit} "
            f"THIS {this_cost} / AVG {average_cost}"
        )
        print(
            f"{snap.provider:<8} {snap.display_name:<16} "
            f"{period_label:<7} {this_text:>8} {average_text:>8} "
            f"{ratio_text:>6} "
            f"{safe_display_text(scope, max_length=200, fallback='N/A')}"
        )
        if getattr(args, "models", False) and snap.report is not None:
            for item in snap.report.model_totals:
                tokens = item.tokens
                print(
                    f"  MODEL {safe_display_text(mask_text(item.model)):<24} "
                    f"TOTAL={compact_token_count(tokens.total_tokens):>7} "
                    f"IN={compact_token_count(tokens.input_tokens):>7} "
                    f"UNKNOWN={compact_token_count(tokens.unclassified_input_tokens):>7} "
                    f"CACHE={compact_token_count(tokens.cached_input_tokens):>7} "
                    f"WRITE={compact_token_count(tokens.cache_write_tokens):>7} "
                    f"OUT={compact_token_count(tokens.output_tokens):>7}"
                )
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    try:
        from quotadeck.usage.pricing import DEFAULT_PRICE_CATALOG, normalize_provider
    except ImportError as exc:
        print(f"price catalog unavailable: {mask_text(str(exc))}", file=sys.stderr)
        return 2

    catalog = DEFAULT_PRICE_CATALOG
    requested = normalize_provider(args.provider) if args.provider else None
    requested_model = (
        str(args.model).strip().casefold() if getattr(args, "model", None) else None
    )
    print(
        f"API list prices · USD / {catalog.unit_tokens:,} tokens · "
        f"as of {catalog.as_of.isoformat()} · catalog {catalog.version}"
    )
    print(
        f"{'PROVIDER':<10} {'MODEL':<28} {'INPUT':>8} {'CACHED':>8} "
        f"{'WRITE':>8} {'OUTPUT':>8}"
    )
    matched = 0
    includes_base_model_only = False
    for price in catalog.models:
        if requested is not None and price.provider != requested:
            continue
        if requested_model is not None and price.model.casefold() != requested_model:
            continue
        matched += 1
        includes_base_model_only = includes_base_model_only or price.base_model_only
        write = price.rates.cache_write
        if write is None and price.rates.cache_write_5m is not None:
            write_text = f"{price.rates.cache_write_5m}/{price.rates.cache_write_1h}"
        else:
            write_text = "-" if write is None else str(write)
        print(
            f"{price.provider:<10} {price.model:<28} {price.rates.input:>8} "
            f"{str(price.rates.cached_input or '-'):>8} {write_text:>8} "
            f"{price.rates.output:>8}"
        )
    if matched == 0:
        print("No matching model price.", file=sys.stderr)
        return 1
    print("LIST only: standard API list-price equivalent, never an invoice.")
    if includes_base_model_only:
        print("Cursor rows are base-model rates only; Cursor Token Rate and other account charges are excluded.")
    return 0


def _normalized_record_payload(record) -> dict[str, object]:
    def money(value):
        return None if value is None else str(value)

    return {
        "provider": record.provider,
        "account": record.account,
        "model": record.model,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "session": record.session,
        "turn": record.turn,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cache_read_tokens": record.cache_read_tokens,
        "cache_write_tokens": record.cache_write_tokens,
        "reasoning_tokens": record.reasoning_tokens,
        "total_tokens": record.total_tokens,
        "reported_cost_usd": money(record.reported_cost_usd),
        "api_equivalent_cost_usd": money(record.api_equivalent_cost_usd),
        "api_equivalent_cost_krw": money(record.api_equivalent_cost_krw),
        "source": record.source,
        "confidence": record.confidence.value,
        "limitations": list(record.limitations),
    }


def cmd_usage_engine(args: argparse.Namespace) -> int:
    """Print the normalized usage contract without quota/rate-limit fields."""

    from dataclasses import replace
    from decimal import Decimal

    from quotadeck.discovery.accounts import select_accounts
    from quotadeck.usage.collectors import CollectorSettings
    from quotadeck.usage.engine import collect_normalized_usage
    from quotadeck.usage.fx import (
        FxFetchError,
        default_fx_cache_path,
        resolve_usd_krw_rate,
    )

    config = load_config()
    accounts = select_accounts(discover_accounts(), config)
    settings = CollectorSettings.from_env()
    if args.cursor_sync:
        settings = replace(settings, enable_cursor_sync=True, enable_token_stats=True)

    fetcher = None
    if args.no_live_fx:
        def fetcher(*_args, **_kwargs):
            raise FxFetchError("live_disabled")

    fx = resolve_usd_krw_rate(
        fetcher=fetcher,
        cache_path=default_fx_cache_path(),
        manual_rate=Decimal(str(config.usd_to_krw_rate)),
    )
    report = collect_normalized_usage(accounts, collector=settings, fx=fx)
    if args.json:
        payload = {
            "schema": "quotadeck.normalized-usage.v1",
            "records": [_normalized_record_payload(row) for row in report.records],
            "account_totals": [
                _normalized_record_payload(row) for row in report.account_totals
            ],
            "provider_totals": [
                _normalized_record_payload(row) for row in report.provider_totals
            ],
            "global_total": _normalized_record_payload(report.global_total),
            "fx": {
                "usd_to_krw": None if fx.usd_to_krw is None else str(fx.usd_to_krw),
                "source": fx.source,
                "source_url": fx.source_url,
                "as_of": fx.as_of.isoformat() if fx.as_of else None,
                "fetched_at": fx.fetched_at.isoformat() if fx.fetched_at else None,
                "stale": fx.stale,
                "fallback": fx.fallback.value if fx.fallback else None,
                "reason": fx.reason,
            },
            "issues": [
                {"provider": row.provider, "account": row.account, "code": row.code}
                for row in report.issues
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"{'SCOPE':<14} {'PROVIDER':<9} {'ACCOUNT':<18} {'TOKENS':>12} "
        f"{'REPORTED USD':>14} {'API EQUIV USD':>14} {'API EQUIV KRW':>14}"
    )
    rows = [
        *(('ACCOUNT', row) for row in report.account_totals),
        *(('PROVIDER', row) for row in report.provider_totals),
        ('GLOBAL', report.global_total),
    ]
    for scope, row in rows:
        def shown(value) -> str:
            return "N/A" if value is None else str(value)
        print(
            f"{scope:<14} {row.provider:<9} {(row.account or '-'):<18} "
            f"{shown(row.total_tokens):>12} {shown(row.reported_cost_usd):>14} "
            f"{shown(row.api_equivalent_cost_usd):>14} "
            f"{shown(row.api_equivalent_cost_krw):>14}"
        )
    if report.issues:
        print("Issues: " + ", ".join(
            f"{item.provider}/{item.account or '-'}:{item.code}"
            for item in report.issues
        ))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    accounts = discover_accounts()
    if not accounts:
        print("No signed-in AI accounts found.")
        return 1
    config = load_config()
    existing = {(a.provider, a.account_id) for a in config.accounts}
    for account in accounts:
        mark = "on" if (account.provider, account.account_id) in existing or not config.accounts else "new"
        print(
            f"[{mark}] {account.provider:7} {account.source_badge:3} "
            f"{(account.source_label or account.source_kind):<12} "
            f"{account.display_name:12} {account.account_id}  {account.plan or ''}"
        )
        if args.apply and (account.provider, account.account_id) not in existing:
            config.accounts.append(account_config_from_ref(account))
    if args.apply:
        save_config(config)
        print(f"saved {len(config.accounts)} accounts")
    return 0

def _snapshots_from_fixture(path: Path):
    from datetime import timedelta

    from quotadeck.core.models import UsageSnapshot, UsageWindow

    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("accounts") or [raw]
    now = datetime.now(timezone.utc)
    snapshots: list[UsageSnapshot] = []
    for item in items:
        windows = []
        for win in item.get("windows", []):
            reset = win.get("resetAt") or win.get("resets_at")
            resets_at = (
                datetime.fromisoformat(reset.replace("Z", "+00:00"))
                if reset
                else now + timedelta(hours=2)
            )
            windows.append(
                UsageWindow(
                    id=win.get("id", "session"),
                    label=win.get("label", "5H"),
                    used_percent=float(win.get("usedPercent", win.get("used_percent", 0))),
                    remaining_percent=float(
                        win.get("remainingPercent", win.get("remaining_percent", 100))
                    ),
                    resets_at=resets_at,
                )
            )
        snapshots.append(
            UsageSnapshot(
                provider=item.get("provider", "codex"),
                account_id=item.get("accountId", item.get("account_id", "demo")),
                display_name=item.get("displayName", item.get("display_name", "DEMO")),
                plan=item.get("plan"),
                windows=windows,
                status=item.get("status", "ok"),
                fetched_at=now,
            )
        )
    return snapshots


def cmd_render(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from quotadeck.core.scheduler import QuotaDeckRuntime
    from quotadeck.core.models import DisplayMode, MetricMode, UsageSnapshot
    from quotadeck.core.severity import snapshot_severity
    from quotadeck.renderer.scenes import render_cumulative_playlist, render_playlist
    from quotadeck.renderer.sprites import load_theme
    from quotadeck.usage.display import CumulativeSnapshot

    config = load_config()
    metric_arg = getattr(args, "metric", None)
    metric_mode = MetricMode(metric_arg) if metric_arg else config.metric_mode
    config = replace(config, metric_mode=metric_mode)
    if args.fixture:
        if metric_mode is MetricMode.CUMULATIVE:
            raise ValueError(
                "--fixture currently describes remaining-limit data; omit it for cumulative mode"
            )
        snapshots = _snapshots_from_fixture(Path(args.fixture))
    else:
        runtime = QuotaDeckRuntime(config, mock=True)
        snapshots = runtime.poll()
    theme = load_theme(Path(args.theme) if args.theme else default_theme_dir())
    hold_seconds = (
        float(args.hold_seconds)
        if args.hold_seconds is not None
        else float(config.scene_hold_seconds)
    )
    display_mode = (
        DisplayMode(args.mode)
        if getattr(args, "mode", None) is not None
        else config.display_mode
    )
    if metric_mode is MetricMode.CUMULATIVE:
        cumulative = [item for item in snapshots if isinstance(item, CumulativeSnapshot)]
        frames = render_cumulative_playlist(
            cumulative,
            theme,
            mode=display_mode,
            frame_budget=int(args.budget),
            hold_ms=int(round(hold_seconds * 1000)),
            currency=config.cost_currency,
            usd_to_krw_rate=config.usd_to_krw_rate,
        )
    else:
        quota = [item for item in snapshots if isinstance(item, UsageSnapshot)]
        severities = {snap.key: snapshot_severity(snap) for snap in quota}
        frames = render_playlist(
            quota,
            severities,
            theme,
            mode=display_mode,
            frame_budget=int(args.budget),
            hold_ms=int(round(hold_seconds * 1000)),
        )
    out = Path(args.out)
    write_gif(frames, out)
    print(f"wrote {out} ({len(frames)} frames)")
    return 0

def cmd_run(args: argparse.Namespace) -> int:
    from quotadeck.core.scheduler import QuotaDeckRuntime

    _setup_logging()
    runtime = QuotaDeckRuntime(load_config(), mock=args.mock)
    if args.once:
        print(runtime.tick(force=True))
        return 0
    runtime.run_forever()
    return 0


def cmd_theme(args: argparse.Namespace) -> int:
    errors = validate_theme(Path(args.path))
    if errors:
        print("invalid theme:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("theme ok")
    return 0

def cmd_ui(_args: argparse.Namespace) -> int:
    from quotadeck.diagnostics import configure_diagnostics

    session = configure_diagnostics(
        "ui",
        console=sys.stderr is not None and not bool(getattr(sys, "frozen", False)),
    )
    session.event("ui_command_dispatch")
    from quotadeck.app.main_window import run_app

    return run_app(session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quotadeck", description="AI usage display for AULA F108 Pro")
    parser.add_argument("--version", action="version", version=f"QuotaDeck {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("probe").set_defaults(func=cmd_probe)
    p_clock = sub.add_parser("clock")
    p_clock.add_argument("--mock", action="store_true")
    p_clock.set_defaults(func=cmd_clock)

    p_up = sub.add_parser("upload")
    p_up.add_argument("gif", nargs="?")
    p_up.add_argument("--solid")
    p_up.add_argument("--mock", action="store_true")
    p_up.set_defaults(func=cmd_upload)

    p_usage = sub.add_parser("usage")
    p_usage.add_argument(
        "--cumulative",
        action="store_true",
        help="show retained cumulative token usage instead of remaining quota",
    )
    p_usage.add_argument(
        "--models",
        action="store_true",
        help="with --cumulative, show the retained per-model token breakdown",
    )
    p_usage.set_defaults(func=cmd_usage)
    p_cumulative = sub.add_parser("cumulative")
    p_cumulative.add_argument(
        "--models",
        action="store_true",
        help="show the complete retained per-model token breakdown",
    )
    p_cumulative.set_defaults(func=cmd_cumulative)
    p_prices = sub.add_parser("prices")
    p_prices.add_argument(
        "--provider",
        choices=("openai", "codex", "anthropic", "claude", "cursor", "xai", "grok"),
    )
    p_prices.add_argument(
        "--model",
        help="show one exact model ID from the bundled catalog",
    )
    p_prices.set_defaults(func=cmd_prices)
    p_engine = sub.add_parser(
        "usage-engine",
        help="show normalized token usage and separate reported/API-equivalent costs",
    )
    p_engine.add_argument("--json", action="store_true", help="emit JSON")
    p_engine.add_argument(
        "--cursor-sync",
        action="store_true",
        help="refresh Cursor token-stats cache before reading it",
    )
    p_engine.add_argument(
        "--no-live-fx",
        action="store_true",
        help="skip the live FX request and use cache/manual fallback",
    )
    p_engine.set_defaults(func=cmd_usage_engine)
    p_det = sub.add_parser("detect")
    p_det.add_argument("--apply", action="store_true")
    p_det.set_defaults(func=cmd_detect)

    p_ren = sub.add_parser("render")
    p_ren.add_argument("--fixture")
    p_ren.add_argument("--out", default="preview.gif")
    p_ren.add_argument("--theme")
    p_ren.add_argument("--budget", default="32")
    p_ren.add_argument(
        "--metric",
        choices=("quota", "cumulative"),
        help="display metric (defaults to saved setting)",
    )
    p_ren.add_argument(
        "--hold-seconds",
        type=_account_seconds,
        help="total display time per account (defaults to saved setting)",
    )
    p_ren.add_argument(
        "--mode",
        choices=("smart", "fixed"),
        help="account order (defaults to saved setting)",
    )
    p_ren.set_defaults(func=cmd_render)
    p_run = sub.add_parser("run")
    p_run.add_argument("--once", action="store_true")
    p_run.add_argument("--mock", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_th = sub.add_parser("theme")
    p_th.add_argument("action", choices=["validate"])
    p_th.add_argument("path")
    p_th.set_defaults(func=cmd_theme)

    sub.add_parser("ui").set_defaults(func=cmd_ui)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not getattr(args, "cmd", None):
            if getattr(sys, "frozen", False):
                return cmd_ui(argparse.Namespace())
            parser.print_help()
            return 0
        result = args.func(args)
        if args.cmd != "ui":
            from quotadeck.diagnostics import current_session

            session = current_session()
            if session is not None:
                session.mark_shutdown("command_returned", exit_code=result)
        return result
    except KeyboardInterrupt:
        logging.getLogger("quotadeck").info("event=command_interrupted")
        return 130
    except Exception as exc:
        logging.getLogger("quotadeck").exception(
            "event=command_failed command=%r",
            getattr(args, "cmd", None) or "ui",
        )
        if sys.stderr is not None:
            print(mask_text(str(exc)), file=sys.stderr)
        return 1
