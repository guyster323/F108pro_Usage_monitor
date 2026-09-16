"""Prove 5-second account slots stay 5 seconds in Preview and firmware bytes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.core.severity import snapshot_severity
from quotadeck.devices.aula_f108.constants import (
    FIRMWARE_DELAY_TICK_MS,
    LCD_MAX_DELAY_MS,
    LOGICAL_DELAY_TICK_MS,
)
from quotadeck.devices.aula_f108.payload import (
    build_payload,
    delay_byte,
    firmware_duration_ms,
    payload_duration_ms,
)
from quotadeck.renderer.budget import allocate
from quotadeck.renderer.scenes import render_playlist
from quotadeck.renderer.sprites import load_theme

THEME = Path(__file__).resolve().parents[1] / "themes" / "quotadeck-crew"
FIXTURE = Path(__file__).parent / "fixtures" / "usage.json"


def _snapshots() -> list[UsageSnapshot]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    snaps = []
    for item in raw["accounts"]:
        windows = [
            UsageWindow(
                id=window["id"],
                label=window["label"],
                used_percent=window["usedPercent"],
                remaining_percent=window["remainingPercent"],
                resets_at=now,
            )
            for window in item["windows"]
        ]
        snaps.append(
            UsageSnapshot(
                provider=item["provider"],
                account_id=item["accountId"],
                display_name=item["displayName"],
                plan=item.get("plan"),
                windows=windows,
                status="ok",
                fetched_at=now,
            )
        )
    return snaps


def test_firmware_tick_is_four_ms_not_twenty() -> None:
    assert FIRMWARE_DELAY_TICK_MS == 4
    assert LOGICAL_DELAY_TICK_MS == 20
    assert LCD_MAX_DELAY_MS == 1020
    assert delay_byte(620) == 155
    assert firmware_duration_ms(620) == 620
    assert delay_byte(620) * 20 != 620


def test_five_second_slot_serializes_to_hardware_and_preview() -> None:
    snaps = _snapshots()[:2]
    sevs = {item.key: snapshot_severity(item) for item in snaps}
    frames = render_playlist(
        snaps, sevs, load_theme(THEME), frame_budget=32, hold_ms=5000
    )
    budget = allocate(len(snaps), 32, hold_ms=5000)
    payload = build_payload(frames)
    assert payload[0] == len(frames) <= 141
    assert sum(frame.delay_ms for frame in frames) == 5000 * len(snaps)
    assert payload_duration_ms(payload) == 5000 * len(snaps)
    for index in range(len(snaps)):
        start = index * budget.frames_per_account
        account = frames[start : start + budget.frames_per_account]
        logical_ms = sum(frame.delay_ms for frame in account)
        hardware_ms = sum(firmware_duration_ms(frame.delay_ms) for frame in account)
        encoded = [delay_byte(frame.delay_ms) for frame in account]
        assert logical_ms == 5000
        assert hardware_ms == 5000
        assert sum(encoded) * FIRMWARE_DELAY_TICK_MS == 5000
        assert max(item.delay_ms for item in account) <= 1020


def test_idle_placeholder_preview_matches_hardware_duration() -> None:
    from quotadeck.renderer.budget import IDLE_PLACEHOLDER_MS, idle_placeholder_delays
    from quotadeck.renderer.scenes import idle_placeholder_frames, render_cumulative_playlist
    from quotadeck.usage.display import CumulativeSnapshot

    delays = idle_placeholder_delays()
    assert sum(delays) == IDLE_PLACEHOLDER_MS == 2000
    assert all(delay <= LCD_MAX_DELAY_MS for delay in delays)
    empty = render_playlist([], {}, load_theme(THEME))
    hidden = render_cumulative_playlist(
        [
            CumulativeSnapshot.unsupported(
                provider="cursor",
                account_id="work",
                display_name="WORK",
            )
        ],
        THEME,
    )
    for frames in (empty, hidden, idle_placeholder_frames()):
        preview_ms = sum(frame.delay_ms for frame in frames)
        payload = build_payload(frames)
        assert preview_ms == 2000
        assert payload_duration_ms(payload) == preview_ms
        assert all(frame.delay_ms <= LCD_MAX_DELAY_MS for frame in frames)
        assert all(frame.delay_ms % LOGICAL_DELAY_TICK_MS == 0 for frame in frames)


def test_eight_account_default_budget_payload_is_forty_seconds() -> None:
    from dataclasses import replace

    from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET
    from quotadeck.renderer.budget import SceneBudgetError

    base = _snapshots()
    snaps = [
        replace(base[index % len(base)], account_id=f"{base[index % len(base)].account_id}-{index}")
        for index in range(8)
    ]
    sevs = {item.key: snapshot_severity(item) for item in snaps}
    try:
        allocate(8, 32, hold_ms=5000)
    except SceneBudgetError:
        pass
    else:
        raise AssertionError("legacy budget 32 must fail for eight 5 s accounts")
    frames = render_playlist(snaps, sevs, load_theme(THEME), hold_ms=5000)
    payload = build_payload(frames)
    assert LCD_DEFAULT_BUDGET == 48
    assert payload[0] == len(frames) <= 48
    assert sum(frame.delay_ms for frame in frames) == 40_000
    assert payload_duration_ms(payload) == 40_000
