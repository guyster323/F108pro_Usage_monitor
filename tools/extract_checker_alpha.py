#!/usr/bin/env python3
"""Turn an image-generator transparency checkerboard into true alpha.

This is a narrowly scoped post-process for image-generation outputs. It flood
fills only low-saturation checker pixels connected to the canvas edge, so
enclosed white/gray character details remain foreground.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import deque
from pathlib import Path

from PIL import Image


GRID = 4
MIN_REMOVED_RATIO = 0.05
MAX_REMOVED_RATIO = 0.95


class CheckerExtractionError(ValueError):
    pass


def _looks_like_checker(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return max(pixel) - min(pixel) <= 12 and (red + green + blue) / 3 >= 115


def extract(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        raise CheckerExtractionError("source and target must be different files")
    if target.is_symlink():
        raise CheckerExtractionError(f"refusing to replace symlinked target: {target}")
    if target.exists() and not target.is_file():
        raise CheckerExtractionError(f"target is not a file: {target}")
    with Image.open(source) as opened:
        opened.load()
        original = opened.convert("RGBA")
    if original.getchannel("A").getextrema() != (255, 255):
        raise CheckerExtractionError(
            f"{source.name} already contains transparency; normalize it directly"
        )
    rgb = original.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    visited = bytearray(width * height)
    queue: deque[int] = deque()

    def enqueue(x: int, y: int) -> None:
        index = y * width + x
        if not visited[index] and _looks_like_checker(pixels[x, y]):
            visited[index] = 1
            queue.append(index)

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(height):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        index = queue.popleft()
        x = index % width
        y = index // width
        if x:
            enqueue(x - 1, y)
        if x + 1 < width:
            enqueue(x + 1, y)
        if y:
            enqueue(x, y - 1)
        if y + 1 < height:
            enqueue(x, y + 1)

    removed = sum(visited)
    removed_ratio = removed / len(visited)
    if not MIN_REMOVED_RATIO <= removed_ratio <= MAX_REMOVED_RATIO:
        raise CheckerExtractionError(
            f"suspicious checker removal ratio {removed_ratio:.1%}; "
            "inspect the input instead of accepting this extraction"
        )

    rgba = rgb.convert("RGBA")
    alpha = Image.frombytes(
        "L",
        rgb.size,
        bytes(0 if value else 255 for value in visited),
    )
    rgba.putalpha(alpha)

    for row in range(GRID):
        y0 = round(row * height / GRID)
        y1 = round((row + 1) * height / GRID)
        for column in range(GRID):
            x0 = round(column * width / GRID)
            x1 = round((column + 1) * width / GRID)
            cell_alpha = alpha.crop((x0, y0, x1, y1))
            histogram = cell_alpha.histogram()
            pixels = cell_alpha.width * cell_alpha.height
            if histogram[0] < pixels * 0.02 or histogram[255] < pixels * 0.02:
                raise CheckerExtractionError(
                    f"cell ({column}, {row}) lacks a credible foreground/background mask"
                )

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}-",
        suffix=".png",
        dir=target.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        rgba.save(temporary, format="PNG", optimize=True, compress_level=9)
        with Image.open(temporary) as written:
            written.load()
            if (
                written.format != "PNG"
                or written.mode != "RGBA"
                or written.size != rgb.size
                or written.getchannel("A").getextrema() != (0, 255)
            ):
                raise CheckerExtractionError("extracted PNG failed post-write validation")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        extract(args.source, args.target)
    except (CheckerExtractionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
