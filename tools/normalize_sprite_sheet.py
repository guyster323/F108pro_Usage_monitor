#!/usr/bin/env python3
"""Normalize a 4x4 generated sprite sheet onto a clean transparent grid.

The two poses for each state share one scale and baseline. This keeps generated
art editable at source resolution while guaranteeing a safe cell gutter before
``tools/gen_sprites.py`` produces the small runtime assets.
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
CELL_SIZE = 320
SHEET_SIZE = GRID * CELL_SIZE
CONTENT_SIZE = (288, 288)
BOTTOM_GUTTER = 16
ALPHA_BBOX_THRESHOLD = 32

# State pairs, in source-sheet reading order.
PAIRS = (
    ((0, 0), (1, 0)),
    ((2, 0), (3, 0)),
    ((0, 1), (1, 1)),
    ((2, 1), (3, 1)),
    ((0, 2), (1, 2)),
    ((2, 2), (3, 2)),
    ((0, 3), (1, 3)),
    ((2, 3), (3, 3)),
)


class SourceNormalizeError(ValueError):
    pass


def _cell(sheet: Image.Image, column: int, row: int) -> Image.Image:
    x0 = round(column * sheet.width / GRID)
    x1 = round((column + 1) * sheet.width / GRID)
    y0 = round(row * sheet.height / GRID)
    y1 = round((row + 1) * sheet.height / GRID)
    return sheet.crop((x0, y0, x1, y1)).convert("RGBA")


def _visible_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A").point(
        lambda value: 255 if value >= ALPHA_BBOX_THRESHOLD else 0
    )
    return alpha.getbbox()


def _primary_alpha_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A").point(
        lambda value: 255 if value >= ALPHA_BBOX_THRESHOLD else 0
    )
    width, height = alpha.size
    opaque = alpha.tobytes()
    seen = bytearray(width * height)
    best_count = 0
    best_box: tuple[int, int, int, int] | None = None
    for start, value in enumerate(opaque):
        if not value or seen[start]:
            continue
        seen[start] = 1
        queue = deque((start,))
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while queue:
            index = queue.popleft()
            x = index % width
            y = index // width
            count += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for dy in (-1, 0, 1):
                next_y = y + dy
                if not 0 <= next_y < height:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    next_x = x + dx
                    if not 0 <= next_x < width:
                        continue
                    next_index = next_y * width + next_x
                    if opaque[next_index] and not seen[next_index]:
                        seen[next_index] = 1
                        queue.append(next_index)
        if count > best_count:
            best_count = count
            best_box = (min_x, min_y, max_x + 1, max_y + 1)
    return best_box


def normalize(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        raise SourceNormalizeError("source and target must be different files")
    if source.is_symlink():
        raise SourceNormalizeError(f"source sheet must not be a symlink: {source}")
    if target.is_symlink():
        raise SourceNormalizeError(f"refusing to replace symlinked target: {target}")
    if target.exists() and not target.is_file():
        raise SourceNormalizeError(f"target is not a file: {target}")
    with Image.open(source) as opened:
        if opened.format != "PNG":
            raise SourceNormalizeError(f"{source.name} is not a PNG")
        if getattr(opened, "n_frames", 1) != 1:
            raise SourceNormalizeError(f"{source.name} must be a static PNG")
        opened.load()
        sheet = opened.convert("RGBA")
    if sheet.width < GRID or sheet.height < GRID:
        raise SourceNormalizeError(f"{source.name} is too small for a 4x4 grid")
    if sheet.getchannel("A").getextrema() == (255, 255):
        raise SourceNormalizeError(f"{source.name} has no transparent alpha")

    result = Image.new("RGBA", (SHEET_SIZE, SHEET_SIZE), (0, 0, 0, 0))
    for pair in PAIRS:
        cells = [_cell(sheet, column, row) for column, row in pair]
        boxes = [_visible_box(cell) for cell in cells]
        if any(box is None for box in boxes):
            raise SourceNormalizeError(f"{source.name} contains a blank pose")
        visible_boxes = [box for box in boxes if box is not None]
        max_width = max(box[2] - box[0] for box in visible_boxes)
        max_height = max(box[3] - box[1] for box in visible_boxes)
        scale = min(CONTENT_SIZE[0] / max_width, CONTENT_SIZE[1] / max_height)

        for (column, row), cell, box in zip(pair, cells, visible_boxes, strict=True):
            cropped = cell.crop(box)
            resized = cropped.resize(
                (
                    max(1, round(cropped.width * scale)),
                    max(1, round(cropped.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
            primary_box = _primary_alpha_box(resized)
            if primary_box is None:
                raise SourceNormalizeError(f"{source.name} contains a blank pose")
            local = Image.new("RGBA", (CELL_SIZE, CELL_SIZE), (0, 0, 0, 0))
            x = (CELL_SIZE - resized.width) // 2
            y = CELL_SIZE - BOTTOM_GUTTER - primary_box[3]
            local.alpha_composite(resized, (x, y))
            # Decorative particles may extend below the body anchor. Preserve
            # the character baseline and the cell gutter, clipping only that
            # out-of-cell decoration.
            alpha = local.getchannel("A")
            alpha_pixels = alpha.load()
            for edge_y in range(CELL_SIZE - BOTTOM_GUTTER, CELL_SIZE):
                for edge_x in range(CELL_SIZE):
                    alpha_pixels[edge_x, edge_y] = 0
            local.putalpha(alpha)
            result.alpha_composite(local, (column * CELL_SIZE, row * CELL_SIZE))

    for row in range(GRID):
        for column in range(GRID):
            cell = result.crop(
                (
                    column * CELL_SIZE,
                    row * CELL_SIZE,
                    (column + 1) * CELL_SIZE,
                    (row + 1) * CELL_SIZE,
                )
            )
            box = _visible_box(cell)
            primary = _primary_alpha_box(cell)
            if box is None or primary is None:
                raise SourceNormalizeError(f"normalized cell ({column}, {row}) is blank")
            margins = (
                box[0],
                box[1],
                CELL_SIZE - box[2],
                CELL_SIZE - box[3],
            )
            if min(margins) < BOTTOM_GUTTER:
                raise SourceNormalizeError(
                    f"normalized cell ({column}, {row}) has unsafe margins {margins}"
                )
            if abs(primary[3] - (CELL_SIZE - BOTTOM_GUTTER)) > 1:
                raise SourceNormalizeError(
                    f"normalized cell ({column}, {row}) has unstable body baseline"
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
        result.save(temporary, format="PNG", optimize=True, compress_level=9)
        with Image.open(temporary) as written:
            written.load()
            if (
                written.format != "PNG"
                or written.mode != "RGBA"
                or written.size != (SHEET_SIZE, SHEET_SIZE)
                or written.getchannel("A").getextrema() != (0, 255)
            ):
                raise SourceNormalizeError("normalized PNG failed post-write validation")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        normalize(args.source, args.target)
    except (SourceNormalizeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
