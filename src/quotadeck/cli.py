from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from quotadeck import __version__
from quotadeck.config import account_config_from_ref, default_theme_dir, load_config, save_config
from quotadeck.core.mask import mask_text
from quotadeck.core.scheduler import QuotaDeckRuntime
from quotadeck.devices.aula_f108.constants import LCD_MAX_FRAMES
from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok
from quotadeck.devices.aula_f108.payload import Frame, hex_to_rgb, solid_frame
from quotadeck.discovery.accounts import discover_accounts
from quotadeck.gifio import load_gif
from quotadeck.providers.base import all_providers
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.sprites import validate_theme


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
    with F108Device() as device:
        device.upload_frames(frames, progress)
    print(f"uploaded {len(frames)} frames")
    return 0


def cmd_usage(_args: argparse.Namespace) -> int:
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


def cmd_render(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from quotadeck.core.models import UsageSnapshot, UsageWindow
    from quotadeck.core.severity import snapshot_severity
    from quotadeck.renderer.scenes import render_playlist
    from quotadeck.renderer.sprites import load_theme

    snapshots: list[UsageSnapshot] = []
    if args.fixture:
        import json

        raw = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("accounts") or [raw]
        now = datetime.now(timezone.utc)
        for item in items:
            windows = []
            for win in item.get("windows", []):
                reset = win.get("resetAt") or win.get("resets_at")
                resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00")) if reset else now + timedelta(hours=2)
                windows.append(
                    UsageWindow(
                        id=win.get("id", "session"),
                        label=win.get("label", "5H"),
                        used_percent=float(win.get("usedPercent", win.get("used_percent", 0))),
                        remaining_percent=float(win.get("remainingPercent", win.get("remaining_percent", 100))),
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
    else:
        runtime = QuotaDeckRuntime(load_config(), mock=True)
        snapshots = runtime.poll()
    theme = load_theme(Path(args.theme) if args.theme else default_theme_dir())
    severities = {snap.key: snapshot_severity(snap) for snap in snapshots}
    frames = render_playlist(snapshots, severities, theme, frame_budget=int(args.budget))
    out = Path(args.out)
    write_gif(frames, out)
    print(f"wrote {out} ({len(frames)} frames)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
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
    from quotadeck.app.main_window import run_app

    return run_app()


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

    sub.add_parser("usage").set_defaults(func=cmd_usage)

    p_det = sub.add_parser("detect")
    p_det.add_argument("--apply", action="store_true")
    p_det.set_defaults(func=cmd_detect)

    p_ren = sub.add_parser("render")
    p_ren.add_argument("--fixture")
    p_ren.add_argument("--out", default="preview.gif")
    p_ren.add_argument("--theme")
    p_ren.add_argument("--budget", default="32")
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
    if not getattr(args, "cmd", None):
        if getattr(sys, "frozen", False):
            return cmd_ui(argparse.Namespace())
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(mask_text(str(exc)), file=sys.stderr)
        return 1
