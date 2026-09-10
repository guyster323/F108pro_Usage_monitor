#!/usr/bin/env python3
"""Dependency-light acceptance checks for the LCD readability refresh.

This complements pytest and is useful while iterating on renderer/assets in a
minimal Codex environment. It never accesses provider credentials or hardware.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.config import AppConfig, _hold_seconds, load_config, save_config
from quotadeck.cli import _account_seconds
from quotadeck.core.models import (
    DisplayMode,
    Severity,
    UsageSnapshot,
    UsageWindow,
    display_windows,
)
from quotadeck.core.severity import band_for_remaining, snapshot_severity
from quotadeck.devices.aula_f108.constants import (
    LCD_DEFAULT_BUDGET,
    LCD_DELAY_TICK_MS,
    LCD_HEIGHT,
    LCD_MAX_DELAY_MS,
    LCD_MAX_FRAMES,
    LCD_PAGE_BYTES,
    LCD_WIDTH,
)
from quotadeck.devices.aula_f108.payload import (
    Frame,
    PayloadError,
    build_payload,
    delay_byte,
    solid_frame,
    validate_payload,
    validate_frames,
)
from quotadeck.devices.aula_f108.protocol import ProtocolError, upload_payload
from quotadeck.devices.aula_f108.transport_mock import MockTransport
from quotadeck.renderer.budget import SceneBudgetError, allocate
from quotadeck.renderer.canvas import (
    BAR_BG,
    BAR_TEXT_ON_EMPTY,
    BAR_TEXT_ON_FILL,
    draw_split_quota_bar,
    new_canvas,
    normalize_remaining,
    pixel_text_width,
    severity_color,
)
from quotadeck.renderer.layout import paint_account
from quotadeck.renderer.scenes import _order, _sprite_index, render_playlist
from quotadeck.renderer.sprites import REQUIRED_STATES, ThemeError, load_theme, validate_theme

THEME = ROOT / "themes" / "quotadeck-crew"
FIXTURE = ROOT / "tests" / "fixtures" / "usage.json"


def _snapshots() -> list[UsageSnapshot]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    snapshots: list[UsageSnapshot] = []
    for item in raw["accounts"]:
        windows = [
            UsageWindow(
                id=window["id"],
                label=window["label"],
                used_percent=float(window["usedPercent"]),
                remaining_percent=float(window["remainingPercent"]),
                resets_at=now,
            )
            for window in item["windows"]
        ]
        snapshots.append(
            UsageSnapshot(
                provider=item["provider"],
                account_id=item["accountId"],
                display_name=item["displayName"],
                plan=item.get("plan"),
                windows=windows,
                status=item.get("status", "ok"),
                fetched_at=now,
            )
        )
    return snapshots


def _check_config() -> None:
    assert AppConfig().scene_hold_seconds == 5
    assert _hold_seconds({}) == 5
    assert _hold_seconds({"scene_hold_seconds": 4}) == 4
    assert _hold_seconds({"scene_hold_seconds": 10}) == 10
    assert _hold_seconds({"scene_hold_seconds": 10, "config_version": 2}) == 10
    with tempfile.TemporaryDirectory(prefix="quotadeck-config-") as folder:
        path = Path(folder) / "config.json"
        save_config(AppConfig(scene_hold_seconds=7), path)
        assert load_config(path).scene_hold_seconds == 7
    assert _account_seconds("5") == 5.0
    for invalid in ("0", "-1", "nan", "inf", "5.001", "20.001"):
        try:
            _account_seconds(invalid)
        except Exception:
            pass
        else:
            raise AssertionError(f"unsafe CLI duration {invalid!r} was accepted")


def _check_theme() -> None:
    assert validate_theme(THEME) == []
    theme = load_theme(THEME)
    assert set(theme.providers) == {"codex", "claude", "cursor", "grok"}
    assert theme.sprite_size == (88, 108)
    for info in theme.providers.values():
        assert set(info["states"]) == REQUIRED_STATES
        assert all(len(files) == 2 for files in info["states"].values())
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_sprites.py"), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    with tempfile.TemporaryDirectory(prefix="quotadeck-theme-") as folder:
        path = Path(folder)
        (path / "theme.json").write_text("[]", encoding="utf-8")
        assert validate_theme(path)
        try:
            load_theme(path)
        except (ThemeError, ValueError):
            pass
        else:
            raise AssertionError("malformed theme reached the runtime renderer")


def _check_bands_and_bar() -> None:
    expected = {
        0: Severity.EXHAUSTED,
        1: Severity.CRITICAL,
        9: Severity.CRITICAL,
        10: Severity.CAUTION,
        19: Severity.CAUTION,
        20: Severity.BUSY,
        49: Severity.BUSY,
        50: Severity.HEALTHY,
        99: Severity.HEALTHY,
        100: Severity.HEALTHY,
    }
    assert {value: band_for_remaining(value) for value in expected} == expected

    image = new_canvas()
    boundary = draw_split_quota_bar(image, (0, 0, 99, 49), 50, "5H", (8, 93, 74))
    assert boundary == 50
    filled_ink = empty_ink = 0
    for y in range(2, 48):
        for x in range(2, 98):
            pixel = image.getpixel((x, y))
            filled_ink += int(x < boundary and pixel == BAR_TEXT_ON_FILL)
            empty_ink += int(x >= boundary and pixel == BAR_TEXT_ON_EMPTY)
    assert filled_ink > 0 and empty_ink > 0
    assert image.getpixel((3, 18)) != BAR_BG
    assert image.getpixel((96, 18)) == BAR_BG
    for value in (float("nan"), float("inf"), float("-inf"), True, False, 10**10000):
        assert normalize_remaining(value) is None
        assert band_for_remaining(value) == Severity.STALE
        guarded = new_canvas()
        assert draw_split_quota_bar(
            guarded,
            (0, 0, 99, 49),
            value,
            "5H",
            (8, 93, 74),
        ) == 2


def _check_account_identity_and_input_selection() -> None:
    snap = _snapshots()[0]
    snap.windows = [
        UsageWindow("primary", "5H", 10, 90),
        UsageWindow("weekly", "WEEK", 10, 90),
        UsageWindow("opus", "OPUS", 100, 0),
        UsageWindow("sonnet", "SONNET", 40, 60),
    ]
    assert [window.id for window in display_windows(snap)] == ["primary", "opus"]
    assert snapshot_severity(snap) == Severity.EXHAUSTED

    blank = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    headers = []
    for alias in ("ACCOUNT_01", "ACCOUNT_02"):
        candidate = _snapshots()[0]
        candidate.display_name = alias
        rendered = paint_account(
            candidate,
            Severity.HEALTHY,
            blank,
            "#19D79C",
            position=(10, 10),
        )
        headers.append(rendered.crop((0, 0, 240, 20)).tobytes())
    assert len(set(headers)) == 2

    patterns = []
    for severity in Severity:
        rendered = paint_account(snap, severity, blank, "#19D79C", position=(10, 10))
        color = severity_color(severity.value)
        patterns.append(
            frozenset(
                (x, y)
                for y in range(4, 16)
                for x in range(226, 237)
                if rendered.getpixel((x, y)) == color
            )
        )
    assert len(patterns) == len(set(patterns))
    index_x = 220 - pixel_text_width("10/10")
    assert index_x > 0

    assert [_sprite_index(index, 2, "idle", 2) for index in range(2)] == [0, 1]
    assert [_sprite_index(index, 2, "idle", 3) for index in range(3)] == [0, 1, 0]


def _check_rotation_and_payload() -> None:
    snapshots = _snapshots()
    severities = {item.key: snapshot_severity(item) for item in snapshots}
    theme = load_theme(THEME)
    for mode in (DisplayMode.FIXED, DisplayMode.SMART):
        ordered = _order(snapshots, severities, mode)
        assert len(ordered) == len(snapshots)
        assert {item.key for item in ordered} == {item.key for item in snapshots}
        budget = allocate(len(ordered), LCD_DEFAULT_BUDGET, hold_ms=5000)
        frames = render_playlist(
            snapshots,
            severities,
            theme,
            mode=mode,
            frame_budget=LCD_DEFAULT_BUDGET,
            hold_ms=5000,
        )
        assert len(frames) == budget.total_frames <= LCD_DEFAULT_BUDGET
        for index in range(len(ordered)):
            start = index * budget.frames_per_account
            account_frames = frames[start : start + budget.frames_per_account]
            encoded_ms = sum(
                delay_byte(frame.delay_ms) * LCD_DELAY_TICK_MS for frame in account_frames
            )
            assert encoded_ms == 5000
        assert all(frame.image.size == (LCD_WIDTH, LCD_HEIGHT) for frame in frames)
        payload = build_payload(frames)
        assert len(payload) > len(frames) * LCD_WIDTH * LCD_HEIGHT * 2
        assert validate_payload(payload) == len(payload) // 4096

    try:
        allocate(9, 8, hold_ms=5000)
    except SceneBudgetError:
        pass
    else:
        raise AssertionError("impossible budget did not raise SceneBudgetError")

    validate_frames([solid_frame(0, 0, 0, LCD_MAX_DELAY_MS)] * LCD_MAX_FRAMES)
    try:
        validate_frames([Frame(new_canvas(), 5120)])
    except PayloadError:
        pass
    else:
        raise AssertionError("unsafe frame delay was accepted")
    try:
        validate_frames([Frame(new_canvas(), 31)])
    except PayloadError:
        pass
    else:
        raise AssertionError("off-grid frame delay was accepted")

    forged = bytearray(build_payload([solid_frame(0, 0, 0, 20)]))
    forged[0] = LCD_MAX_FRAMES + 1
    transport = MockTransport()
    try:
        upload_payload(transport, bytes(forged))
    except PayloadError:
        pass
    else:
        raise AssertionError("forged raw frame count was accepted")
    assert transport.features == [] and transport.pages == []

    mutable = bytearray(build_payload([solid_frame(0, 0, 0, 20)]))
    expected = bytes(mutable)

    class MutatingTransport(MockTransport):
        def set_feature(self, packet: bytes) -> None:
            mutable[0] = LCD_MAX_FRAMES + 1
            super().set_feature(packet)

    mutating_transport = MutatingTransport()
    upload_payload(mutating_transport, mutable)
    pages = len(expected) // LCD_PAGE_BYTES
    header = mutating_transport.features[1]
    assert header[8] | (header[9] << 8) == pages
    assert b"".join(mutating_transport.pages) == expected

    class EchoNackTransport(MockTransport):
        def get_feature(self) -> bytes:
            response = bytearray(super().get_feature())
            response[3] = 0
            return bytes(response)

    nack_transport = EchoNackTransport()
    try:
        upload_payload(
            nack_transport,
            build_payload([solid_frame(0, 0, 0, 20)]),
        )
    except ProtocolError:
        pass
    else:
        raise AssertionError("echoed command prefix incorrectly bypassed a feature NACK")
    assert nack_transport.pages == []


def main() -> int:
    checks = (
        ("config preservation and custom duration", _check_config),
        ("compiled sprite/theme contract", _check_theme),
        ("severity edges and split-colour bar", _check_bands_and_bar),
        ("account identity and visible quota selection", _check_account_identity_and_input_selection),
        ("equal 5 s slots and safe payload", _check_rotation_and_payload),
    )
    for label, check in checks:
        check()
        print(f"ok: {label}")
    print("QuotaDeck UI refresh verification passed (hardware upload not performed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
