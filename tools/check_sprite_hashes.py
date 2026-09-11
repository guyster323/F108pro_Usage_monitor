#!/usr/bin/env python3
"""Verify the 104 runtime sprites and bundled mirrors were not rewritten.

A full ``tools/gen_sprites.py --check`` recompile can drift across Pillow
builds. v0.2.3 must keep the committed theme bytes and the package-data
mirror identical without touching those character sprites.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.gen_sprites import DEFAULT_BUNDLED_THEME, _byte_files

THEME = ROOT / "themes" / "quotadeck-crew"
EXPECTED = 104


def main() -> int:
    sprites = sorted((THEME / "provider").glob("*/*.png"))
    if len(sprites) != EXPECTED:
        print(f"expected {EXPECTED} runtime sprites, found {len(sprites)}", file=sys.stderr)
        return 1
    actual = _byte_files(THEME)
    bundled = _byte_files(DEFAULT_BUNDLED_THEME)
    if actual != bundled:
        print("bundled package-data theme does not match the canonical theme", file=sys.stderr)
        return 1
    print(f"sprite hashes ok: {EXPECTED} runtime files match the bundled mirror")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
