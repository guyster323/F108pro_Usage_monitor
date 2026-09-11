#!/usr/bin/env python3
"""Create the authorized pixel-coin source and its 7x7 runtime derivative.

The coin is a KRW/USD-agnostic pictogram: no letters and no currency signs.
The 7x7 map is the source of truth. The 32x32 artwork is a nearest-neighbor
scale of that map so both files stay deterministic.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artwork" / "coin-pixel-source.png"
RUNTIME = ROOT / "src" / "quotadeck" / "bundled" / "icons" / "coin-pixel.png"

# 0 transparent, 1 rim, 2 body, 3 highlight, 4 shade
COIN_MAP = (
    "0111110",
    "1222221",
    "1233221",
    "1232241",
    "1222441",
    "1224421",
    "0111110",
)
PALETTE = {
    "1": (118, 70, 14, 255),
    "2": (232, 176, 48, 255),
    "3": (255, 228, 140, 255),
    "4": (186, 118, 22, 255),
}
RUNTIME_SIZE = 7
SOURCE_SIZE = 32


def render_runtime() -> Image.Image:
    image = Image.new("RGBA", (RUNTIME_SIZE, RUNTIME_SIZE), (0, 0, 0, 0))
    pixels = image.load()
    assert pixels is not None
    for y, row in enumerate(COIN_MAP):
        for x, cell in enumerate(row):
            if cell in PALETTE:
                pixels[x, y] = PALETTE[cell]
    return image


def render_source(runtime: Image.Image) -> Image.Image:
    scaled = runtime.resize((28, 28), Image.Resampling.NEAREST)
    image = Image.new("RGBA", (SOURCE_SIZE, SOURCE_SIZE), (0, 0, 0, 0))
    image.paste(scaled, (2, 2), scaled)
    return image


def write_assets(source: Path = SOURCE, runtime: Path = RUNTIME) -> None:
    coin = render_runtime()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    coin.save(runtime, format="PNG")
    render_source(coin).save(source, format="PNG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--runtime", type=Path, default=RUNTIME)
    args = parser.parse_args(argv)
    write_assets(args.source, args.runtime)
    print(f"wrote {args.source}")
    print(f"wrote {args.runtime}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
