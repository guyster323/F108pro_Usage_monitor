#!/usr/bin/env python3
"""Preserve the equal-slot scene budget contract."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET, LCD_MAX_FRAMES, LCD_SOFT_CAP
from quotadeck.devices.aula_f108.payload import delay_byte, payload_padded_size
from quotadeck.renderer.budget import SceneBudgetError, allocate, required_playlist_frames


def main() -> int:
    one = allocate(1)
    eight = allocate(8)
    if one.account_hold_ms != 5000 or eight.account_hold_ms != 5000:
        print("account hold is no longer 5000 ms", file=sys.stderr)
        return 1
    if one.frames_per_account != 10 or eight.frames_per_account != 10:
        print("5 s slots must use 10 frames after the 500 ms encode cap", file=sys.stderr)
        return 1
    if one.frame_delays_ms != (500,) * 10 or eight.frame_delays_ms != (500,) * 10:
        print("default 5 s slot is no longer ten 500 ms frames", file=sys.stderr)
        return 1
    encoded = [delay_byte(delay) for delay in eight.frame_delays_ms]
    if encoded != [250] * 10 or sum(encoded) != 2500:
        print("default 5 s slot delay bytes must be 250 × 10 = 2500", file=sys.stderr)
        return 1
    if LCD_DEFAULT_BUDGET != 80 or LCD_SOFT_CAP != LCD_MAX_FRAMES or eight.total_frames != 80:
        print("default eight-account budget must stay 80; soft cap equals hard 141", file=sys.stderr)
        return 1
    if allocate(8, frame_budget=48).total_frames != 80:
        print("legacy v7 budget 48 must auto-expand to 80 for eight 5 s accounts", file=sys.stderr)
        return 1
    if required_playlist_frames(8, 6000) != 96 or allocate(8, 80, hold_ms=6000).total_frames != 96:
        print("8 accounts × 6 s must use 96 frames", file=sys.stderr)
        return 1
    if allocate(10).total_frames != 100:
        print("10 accounts × 5 s must auto-expand to 100 frames", file=sys.stderr)
        return 1
    if payload_padded_size(80) != 5_185_536 or payload_padded_size(96) != 6_221_824:
        print("padded payload sizes for 80/96 frames drifted", file=sys.stderr)
        return 1
    try:
        allocate(8, hold_ms=20_000)
    except SceneBudgetError:
        print(
            "scene budget ok: default 80 encodes eight 5 s slots; "
            "81-141 combos auto-expand; overflow fails closed"
        )
        return 0
    print("over-capacity budgets should fail closed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
