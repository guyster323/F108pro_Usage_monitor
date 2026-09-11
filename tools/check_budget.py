#!/usr/bin/env python3
"""Preserve the equal-slot scene budget contract."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.renderer.budget import SceneBudgetError, allocate


def main() -> int:
    one = allocate(1)
    four = allocate(4)
    if one.account_hold_ms != 5000 or four.account_hold_ms != 5000:
        print("account hold is no longer 5000 ms", file=sys.stderr)
        return 1
    if sum(one.frame_delays_ms) != 5000 or sum(four.frame_delays_ms) != 5000:
        print("frame delays no longer sum to the hold", file=sys.stderr)
        return 1
    try:
        allocate(40, frame_budget=32)
    except SceneBudgetError:
        print("scene budget ok: equal 5000 ms slots and fail-closed overflow")
        return 0
    print("over-capacity budgets should fail closed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
