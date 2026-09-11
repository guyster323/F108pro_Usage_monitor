#!/usr/bin/env python3
"""Require collector, Cursor-export, and coin documentation in the 0.2.3 docs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    ROOT / "README.md": (
        "token-stats",
        "ccusage",
        "native",
        "COLLECTORS.md",
        "픽셀 코인",
    ),
    ROOT / "README.en.md": (
        "token-stats",
        "ccusage",
        "native",
        "COLLECTORS.md",
        "pixel coin",
    ),
    ROOT / "docs" / "COLLECTORS.md": (
        "token-stats",
        "ccusage",
        "quotadeck.collector.v1",
        "shell=False",
        "usage.<account>.csv",
        "state.vscdb",
    ),
    ROOT / "docs" / "CUMULATIVE_USAGE.md": ("token-stats", "픽셀 코인", "COLLECTORS.md"),
    ROOT / "docs" / "PROVIDERS.md": ("token-stats", "COLLECTORS.md"),
    ROOT / "docs" / "SECURITY.md": ("QUOTADECK_TOKEN_STATS", "state.vscdb"),
}


def main() -> int:
    failed = False
    for path, needles in REQUIRED.items():
        text = path.read_text(encoding="utf-8")
        missing = [item for item in needles if item not in text]
        if missing:
            failed = True
            print(f"{path.relative_to(ROOT)} missing {missing}", file=sys.stderr)
    if failed:
        return 1
    print("docs ok: collector priority, Cursor export boundary, and coin noted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
