#!/usr/bin/env python3
"""Preserve the equal-slot scene budget contract."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET, LCD_SOFT_CAP
from quotadeck.renderer.budget import SceneBudgetError, allocate


def main() -> int:
    one = allocate(1)
    eight = allocate(8)
    if one.account_hold_ms != 5000 or eight.account_hold_ms != 5000:
        print("account hold is no longer 5000 ms", file=sys.stderr)
        return 1
    if sum(one.frame_delays_ms) != 5000 or sum(eight.frame_delays_ms) != 5000:
        print("frame delays no longer sum to the hold", file=sys.stderr)
        return 1
    if LCD_DEFAULT_BUDGET != LCD_SOFT_CAP or eight.total_frames > LCD_SOFT_CAP:
        print("default eight-account budget must stay at the 48-frame soft cap", file=sys.stderr)
        return 1
    try:
        allocate(8, frame_budget=32)
        print("legacy budget 32 should fail for eight 5 s accounts", file=sys.stderr)
        return 1
    except SceneBudgetError:
        pass
    try:
        allocate(10)
    except SceneBudgetError:
        print("scene budget ok: default 48 encodes eight 5 s slots and fail-closed overflow")
        return 0
    print("over-capacity budgets should fail closed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
