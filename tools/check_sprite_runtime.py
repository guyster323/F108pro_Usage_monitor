#!/usr/bin/env python3
"""Count and validate the 104 runtime character sprites without modifying them."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.renderer.sprites import REQUIRED_PROVIDERS, REQUIRED_STATES, validate_theme

THEME = ROOT / "themes" / "quotadeck-crew"
EXPECTED_SPRITES = 104


def main() -> int:
    errors = validate_theme(THEME)
    if errors:
        print("runtime theme is invalid:", file=sys.stderr)
        for error in errors[:12]:
            print(f"  - {error}", file=sys.stderr)
        return 1
    sprites = sorted((THEME / "provider").glob("*/*.png"))
    if len(sprites) != EXPECTED_SPRITES:
        print(
            f"expected {EXPECTED_SPRITES} runtime sprites, found {len(sprites)}",
            file=sys.stderr,
        )
        return 1
    providers = {path.parent.name for path in sprites}
    if providers != REQUIRED_PROVIDERS:
        print(f"unexpected providers: {sorted(providers)}", file=sys.stderr)
        return 1
    print(
        f"runtime sprites ok: {len(sprites)} files, "
        f"{len(REQUIRED_PROVIDERS)} providers, {len(REQUIRED_STATES)} states"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
