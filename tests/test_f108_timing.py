"""Prove 5-second account slots stay 5 seconds in Preview and encode bytes."""

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


def test_observed_playback_unit_is_two_ms_not_four_or_twenty() -> None:
    assert FIRMWARE_DELAY_TICK_MS == 2
    assert LOGICAL_DELAY_TICK_MS == 20
    assert LCD_MAX_DELAY_MS == 500
    assert delay_byte(500) == 250
    assert firmware_duration_ms(500) == 500
    assert delay_byte(500) * 4 != 500
    assert delay_byte(500) * 20 != 500


def test_five_second_slot_serializes_explicit_delay_bytes() -> None:
    snaps = _snapshots()[:2]
    sevs = {item.key: snapshot_severity(item) for item in snaps}
    frames = render_playlist(
        snaps, sevs, load_theme(THEME), frame_budget=32, hold_ms=5000
    )
    budget = allocate(len(snaps), 32, hold_ms=5000)
    payload = build_payload(frames)
    assert budget.frames_per_account == 10
    assert budget.frame_delays_ms == (500,) * 10
    assert payload[0] == len(frames) == 20
    assert sum(frame.delay_ms for frame in frames) == 5000 * len(snaps)
    for index in range(len(snaps)):
        start = index * budget.frames_per_account
        account = frames[start : start + budget.frames_per_account]
        encoded = [delay_byte(frame.delay_ms) for frame in account]
        assert [frame.delay_ms for frame in account] == [500] * 10
        assert encoded == [250] * 10
        assert sum(encoded) == 2500
        assert max(item.delay_ms for item in account) <= 500


def test_idle_placeholder_preview_matches_hardware_duration() -> None:
    from quotadeck.renderer.budget import IDLE_PLACEHOLDER_MS, idle_placeholder_delays
    from quotadeck.renderer.scenes import idle_placeholder_frames, render_cumulative_playlist
    from quotadeck.usage.display import CumulativeSnapshot

    delays = idle_placeholder_delays()
    assert delays == (500, 500, 500, 500)
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
        encoded = list(payload[1 : 1 + payload[0]])
        assert len(frames) == 4
        assert preview_ms == 2000
        assert encoded == [250, 250, 250, 250]
        assert payload_duration_ms(payload) == preview_ms
        assert all(frame.delay_ms <= LCD_MAX_DELAY_MS for frame in frames)
        assert all(frame.delay_ms % LOGICAL_DELAY_TICK_MS == 0 for frame in frames)


def test_eight_account_default_budget_payload_is_forty_seconds() -> None:
    from dataclasses import replace

    from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET

    base = _snapshots()
    snaps = [
        replace(base[index % len(base)], account_id=f"{base[index % len(base)].account_id}-{index}")
        for index in range(8)
    ]
    sevs = {item.key: snapshot_severity(item) for item in snaps}
    assert allocate(8, 48, hold_ms=5000).total_frames == 80
    frames = render_playlist(snaps, sevs, load_theme(THEME), hold_ms=5000)
    payload = build_payload(frames)
    encoded = list(payload[1 : 1 + payload[0]])
    assert LCD_DEFAULT_BUDGET == 80
    assert payload[0] == len(frames) == 80
    assert encoded == [250] * 80
    assert sum(encoded) == 20_000
    assert sum(frame.delay_ms for frame in frames) == 40_000
    assert payload_duration_ms(payload) == 40_000


def test_long_hold_combinations_auto_expand_or_fail_at_hard_limit() -> None:
    from quotadeck.devices.aula_f108.payload import payload_padded_size
    from quotadeck.renderer.budget import SceneBudgetError, required_playlist_frames

    assert required_playlist_frames(8, 6000) == 96
    assert required_playlist_frames(9, 5000) == 90
    assert required_playlist_frames(3, 20_000) == 120
    six = allocate(8, 80, hold_ms=6000)
    assert six.total_frames == 96
    assert six.frames_per_account == 12
    assert sum(six.frame_delays_ms) == 6000
    assert allocate(3, 80, hold_ms=20_000).total_frames == 120
    assert payload_padded_size(96) == 6_221_824
    assert payload_padded_size(141) == 9_138_176
    try:
        allocate(8, 80, hold_ms=20_000)
    except SceneBudgetError as exc:
        assert exc.required == 320
        assert exc.limit == 141
    else:
        raise AssertionError("8 accounts × 20 s must exceed the 141-frame hard limit")
