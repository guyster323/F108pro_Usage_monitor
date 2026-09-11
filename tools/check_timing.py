#!/usr/bin/env python3
"""Preserve Flash uncapped daily-write examples from the v0.2.2 contract."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.core.flashbudget import FlashBudget


def main() -> int:
    expected = {1: 960, 7: 138, 10: 96, 40: 24, 90: 11}
    for minutes, writes in expected.items():
        budget = FlashBudget(min_interval=timedelta(minutes=minutes))
        actual = budget.estimated_uncapped_daily_writes()
        if actual != writes:
            print(
                f"{minutes} min interval -> {actual} writes, expected {writes}",
                file=sys.stderr,
            )
            return 1
    print("flash timing ok: 1/7/10/40/90 min -> 960/138/96/24/11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
