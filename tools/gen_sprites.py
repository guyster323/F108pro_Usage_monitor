#!/usr/bin/env python3
"""Compile image-generated 4x4 source sheets into QuotaDeck runtime sprites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.renderer.sprites import validate_theme

DEFAULT_SOURCE = ROOT / "artwork" / "sprite_sources"
DEFAULT_THEME = ROOT / "themes" / "quotadeck-crew"
DEFAULT_PREVIEW = ROOT / "artwork" / "sprite_previews" / "runtime_contact_sheet.png"

TARGET_SIZE = (88, 108)
CONTENT_SIZE = (84, 101)
SOURCE_SIZE = (1280, 1280)
ALPHA_THRESHOLD = 72
SOURCE_GUTTER_RATIO = 0.05
SOURCE_MIN_FOOTPRINT_RATIO = 0.25
SOURCE_ANCHOR_TOLERANCE_RATIO = 0.03
PROVIDERS = {
    "codex": "#19D79C",
    "claude": "#EF9A5A",
    "cursor": "#4AB6F7",
    "grok": "#AD82EF",
}
# Source layout, left-to-right then top-to-bottom.
CELL_MAP = {
    "idle": ((0, 0), (1, 0)),
    "busy": ((2, 0), (3, 0)),
    "caution": ((0, 1), (1, 1)),
    "critical": ((2, 1), (3, 1)),
    "exhausted": ((0, 2), (1, 2)),
    "offline": ((2, 2), (3, 2)),
    "stale": ((0, 3), (1, 3)),
    "reset": ((2, 3), (3, 3)),
}


class SpriteCompileError(ValueError):
    pass


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return (
        left_resolved == right_resolved
        or left_resolved.is_relative_to(right_resolved)
        or right_resolved.is_relative_to(left_resolved)
    )


def _validate_output_paths(source_dir: Path, theme_dir: Path, preview: Path) -> None:
    if _paths_overlap(source_dir, theme_dir):
        raise SpriteCompileError("source and theme directories must not overlap")
    preview_resolved = preview.resolve()
    for label, directory in (("source", source_dir), ("theme", theme_dir)):
        if preview_resolved.is_relative_to(directory.resolve()):
            raise SpriteCompileError(
                f"preview must not be inside the {label} directory: {preview}"
            )
    if preview.is_symlink():
        raise SpriteCompileError(f"refusing to replace symlinked preview: {preview}")
    if preview.exists() and not preview.is_file():
        raise SpriteCompileError(f"preview target is not a file: {preview}")


def _is_managed_theme(path: Path) -> bool:
    if not any(path.iterdir()):
        return True
    manifest = path / "theme.json"
    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    pipeline = meta.get("asset_pipeline") if isinstance(meta, dict) else None
    return (
        isinstance(pipeline, dict)
        and pipeline.get("compiler") == "tools/gen_sprites.py"
    )


def _cell(sheet: Image.Image, column: int, row: int) -> Image.Image:
    x0 = round(column * sheet.width / 4)
    x1 = round((column + 1) * sheet.width / 4)
    y0 = round(row * sheet.height / 4)
    y1 = round((row + 1) * sheet.height / 4)
    return sheet.crop((x0, y0, x1, y1)).convert("RGBA")


def _binary_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").point(
        lambda value: 255 if value >= ALPHA_THRESHOLD else 0
    )
    rgba.putalpha(alpha)
    return rgba


def _primary_alpha_box(alpha: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the largest 8-connected opaque component, normally the body."""
    width, height = alpha.size
    opaque = alpha.tobytes()
    seen = bytearray(width * height)
    largest_count = 0
    largest_box: tuple[int, int, int, int] | None = None
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
        if count > largest_count:
            largest_count = count
            largest_box = (min_x, min_y, max_x + 1, max_y + 1)
    return largest_box


def _compile_cell(cell: Image.Image, common_box: tuple[int, int, int, int]) -> Image.Image:
    cropped = cell.crop(common_box)
    scale = min(
        CONTENT_SIZE[0] / max(1, cropped.width),
        CONTENT_SIZE[1] / max(1, cropped.height),
    )
    resized = cropped.resize(
        (
            max(1, int(round(cropped.width * scale))),
            max(1, int(round(cropped.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", TARGET_SIZE, (0, 0, 0, 0))
    x = (TARGET_SIZE[0] - resized.width) // 2
    y = TARGET_SIZE[1] - resized.height - 2
    canvas.alpha_composite(resized, (x, max(1, y)))
    canvas = _binary_alpha(canvas)

    # Align the main connected silhouette rather than the bottommost decorative
    # spark. This keeps the character's feet/powered-down body on one baseline.
    primary_box = _primary_alpha_box(canvas.getchannel("A"))
    if primary_box:
        anchor_bottom = TARGET_SIZE[1] - 2  # exclusive y; leaves two clear rows
        shift_y = anchor_bottom - primary_box[3]
        if shift_y:
            aligned = Image.new("RGBA", TARGET_SIZE, (0, 0, 0, 0))
            aligned.alpha_composite(canvas, (0, shift_y))
            canvas = aligned
        alpha = canvas.getchannel("A")
        ImageDraw.Draw(alpha).rectangle(
            (0, anchor_bottom, TARGET_SIZE[0] - 1, TARGET_SIZE[1] - 1),
            fill=0,
        )
        canvas.putalpha(alpha)

    # One compact per-frame palette, then snap RGB to exactly representable
    # RGB565 channel values. Dithering is intentionally disabled.
    quantized = canvas.quantize(
        colors=48,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    ).convert("RGBA")
    quantized.putalpha(canvas.getchannel("A"))
    red, green, blue, alpha = quantized.split()
    red = red.point(lambda value: value & 0xF8)
    green = green.point(lambda value: value & 0xFC)
    blue = blue.point(lambda value: value & 0xF8)
    return Image.merge("RGBA", (red, green, blue, alpha))


def _common_alpha_box(cells: list[Image.Image]) -> tuple[int, int, int, int]:
    width = min(image.width for image in cells)
    height = min(image.height for image in cells)
    boxes = []
    for image in cells:
        normalized = image.crop((0, 0, width, height))
        box = _binary_alpha(normalized).getchannel("A").getbbox()
        if box:
            boxes.append(box)
    if not boxes:
        raise SpriteCompileError("source sheet contains no opaque character pixels")
    x0 = max(0, min(box[0] for box in boxes) - 3)
    y0 = max(0, min(box[1] for box in boxes) - 3)
    x1 = min(width, max(box[2] for box in boxes) + 3)
    y1 = min(height, max(box[3] for box in boxes) + 3)
    return x0, y0, x1, y1


def _validate_source_cell(
    cell: Image.Image,
    *,
    source: Path,
    state: str,
    pose: int,
) -> tuple[int, int, int, int]:
    alpha_box = _binary_alpha(cell).getchannel("A").getbbox()
    label = f"{source.name} {state}_{pose:02d}"
    if not alpha_box:
        raise SpriteCompileError(f"{label} is blank")
    gutter = max(3, int(round(min(cell.size) * SOURCE_GUTTER_RATIO)))
    margins = (
        alpha_box[0],
        alpha_box[1],
        cell.width - alpha_box[2],
        cell.height - alpha_box[3],
    )
    if min(margins) < gutter:
        raise SpriteCompileError(
            f"{label} needs at least {gutter}px transparent gutter on every edge; "
            f"got left/top/right/bottom={margins}"
        )
    opaque_width = alpha_box[2] - alpha_box[0]
    opaque_height = alpha_box[3] - alpha_box[1]
    if (
        opaque_width < cell.width * SOURCE_MIN_FOOTPRINT_RATIO
        or opaque_height < cell.height * SOURCE_MIN_FOOTPRINT_RATIO
    ):
        raise SpriteCompileError(
            f"{label} visible footprint {opaque_width}x{opaque_height} is too small"
        )
    primary_box = _primary_alpha_box(_binary_alpha(cell).getchannel("A"))
    if primary_box is None:
        raise SpriteCompileError(f"{label} has no connected character component")
    primary_width = primary_box[2] - primary_box[0]
    primary_height = primary_box[3] - primary_box[1]
    if (
        primary_width < cell.width * SOURCE_MIN_FOOTPRINT_RATIO
        or primary_height < cell.height * SOURCE_MIN_FOOTPRINT_RATIO
    ):
        raise SpriteCompileError(
            f"{label} primary character component {primary_width}x{primary_height} "
            "is too small"
        )
    return primary_box


def compile_provider(source: Path) -> dict[str, list[Image.Image]]:
    if not source.is_file():
        raise SpriteCompileError(f"missing source sheet: {source}")
    if source.is_symlink():
        raise SpriteCompileError(f"source sheet must not be a symlink: {source}")
    try:
        with Image.open(source) as opened:
            if opened.format != "PNG":
                raise SpriteCompileError(f"{source.name} is not a PNG")
            if getattr(opened, "n_frames", 1) != 1:
                raise SpriteCompileError(f"{source.name} must be a static PNG")
            if opened.mode != "RGBA":
                raise SpriteCompileError(f"{source.name} must use RGBA mode")
            if opened.size != SOURCE_SIZE:
                raise SpriteCompileError(
                    f"{source.name} must be {SOURCE_SIZE[0]}x{SOURCE_SIZE[1]}; "
                    "run tools/normalize_sprite_sheet.py"
                )
            opened.load()
            sheet = opened.copy()
    except SpriteCompileError:
        raise
    except Exception as exc:
        raise SpriteCompileError(f"cannot decode {source}: {exc}") from exc
    if sheet.getchannel("A").getextrema() == (255, 255):
        raise SpriteCompileError(f"{source.name} has no transparent alpha")
    compiled: dict[str, list[Image.Image]] = {}
    for state, locations in CELL_MAP.items():
        cells = [_cell(sheet, column, row) for column, row in locations]
        primary_boxes = []
        for pose, cell in enumerate(cells):
            primary_boxes.append(
                _validate_source_cell(
                    cell,
                    source=source,
                    state=state,
                    pose=pose,
                )
            )
        primary_bottoms = [box[3] for box in primary_boxes]
        tolerance = max(1, round(cells[0].height * SOURCE_ANCHOR_TOLERANCE_RATIO))
        if max(primary_bottoms) - min(primary_bottoms) > tolerance:
            raise SpriteCompileError(
                f"{source.name} {state} poses have mismatched body baselines "
                f"{primary_bottoms}; tolerance is {tolerance}px"
            )
        # Keep the two animation poses perfectly aligned while allowing each
        # state to use the available 88x108 bay. A large reset swirl should not
        # shrink every idle and busy pose in the provider sheet.
        common_box = _common_alpha_box(cells)
        compiled[state] = [_compile_cell(cell, common_box) for cell in cells]
    return compiled


def _theme_manifest(providers: dict[str, dict]) -> dict:
    return {
        "schema_version": 2,
        "name": "QuotaDeck Crew",
        "id": "quotadeck-crew",
        "fps": 4,
        "canvas": {"width": 240, "height": 135},
        "hud": "full-bars-v2",
        "sprite_size": {"w": TARGET_SIZE[0], "h": TARGET_SIZE[1]},
        "anchor": {"x": 44, "y": 103},
        "asset_pipeline": {
            "source": "artwork/sprite_sources",
            "compiler": "tools/gen_sprites.py",
            "pillow": PILLOW_VERSION,
            "layout": "4x4-state-pairs",
        },
        "palette": {
            "bg": "#081019",
            "text": "#F7FBF7",
            "accent": "#19D79C",
        },
        "providers": providers,
    }


def _write_compiled_theme(
    compiled_providers: dict[str, dict[str, list[Image.Image]]],
    theme_dir: Path,
) -> list[Image.Image]:
    theme_dir.mkdir(parents=True, exist_ok=False)
    providers_meta: dict[str, dict] = {}
    preview_columns: list[Image.Image] = []
    for provider, accent in PROVIDERS.items():
        compiled = compiled_providers[provider]
        folder = theme_dir / "provider" / provider
        folder.mkdir(parents=True, exist_ok=True)
        states_meta: dict[str, list[str]] = {}
        provider_frames: list[Image.Image] = []
        expected_names: set[str] = set()
        for state, frames in compiled.items():
            names = []
            for index, frame in enumerate(frames):
                name = f"{state}_{index:02d}.png"
                frame.save(folder / name, format="PNG", optimize=True, compress_level=9)
                names.append(name)
                expected_names.add(name)
                provider_frames.append(frame)
            states_meta[state] = names
        for stale in folder.glob("*.png"):
            if stale.name not in expected_names:
                stale.unlink()
        providers_meta[provider] = {"accent": accent, "states": states_meta}

        column = Image.new(
            "RGBA",
            (TARGET_SIZE[0] * 2, TARGET_SIZE[1] * len(CELL_MAP)),
            (0, 0, 0, 0),
        )
        for index, frame in enumerate(provider_frames):
            row, col = divmod(index, 2)
            column.alpha_composite(frame, (col * TARGET_SIZE[0], row * TARGET_SIZE[1]))
        preview_columns.append(column)

    manifest = _theme_manifest(providers_meta)
    (theme_dir / "theme.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return preview_columns


def compile_theme(
    source_dir: Path,
    theme_dir: Path,
    *,
    replace_unmanaged: bool = False,
) -> list[Image.Image]:
    if _paths_overlap(source_dir, theme_dir):
        raise SpriteCompileError("source and theme directories must not overlap")
    if theme_dir.is_symlink():
        raise SpriteCompileError(f"refusing to replace symlinked theme: {theme_dir}")
    if theme_dir.exists() and not theme_dir.is_dir():
        raise SpriteCompileError(f"theme target is not a directory: {theme_dir}")
    if theme_dir.exists() and not replace_unmanaged and not _is_managed_theme(theme_dir):
        raise SpriteCompileError(
            f"refusing to replace unmanaged non-empty directory: {theme_dir}; "
            "choose a new target or pass --force"
        )
    # Decode and validate every source cell before touching an existing theme.
    compiled_providers = {
        provider: compile_provider(source_dir / f"{provider}.png")
        for provider in PROVIDERS
    }

    theme_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".quotadeck-theme-",
        dir=theme_dir.parent,
    ) as temporary:
        temporary_dir = Path(temporary)
        staged_theme = temporary_dir / "theme"
        preview_columns = _write_compiled_theme(compiled_providers, staged_theme)
        errors = validate_theme(staged_theme)
        if errors:
            raise SpriteCompileError(
                "compiled theme is invalid: " + "; ".join(errors[:8])
            )

        previous_theme = theme_dir.with_name(
            f".{theme_dir.name}.backup-{uuid.uuid4().hex}"
        )
        had_previous = theme_dir.exists()
        try:
            if had_previous:
                theme_dir.rename(previous_theme)
            staged_theme.rename(theme_dir)
        except BaseException as exc:
            if had_previous and previous_theme.exists() and not theme_dir.exists():
                try:
                    previous_theme.rename(theme_dir)
                except BaseException as restore_error:
                    raise SpriteCompileError(
                        "theme replacement and rollback failed; previous theme remains at "
                        f"{previous_theme}"
                    ) from restore_error
            raise
        if previous_theme.exists():
            shutil.rmtree(previous_theme)
    return preview_columns


def write_preview(columns: list[Image.Image], target: Path) -> None:
    if target.is_symlink():
        raise SpriteCompileError(f"refusing to replace symlinked preview: {target}")
    if target.exists() and not target.is_file():
        raise SpriteCompileError(f"preview target is not a file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    label_width = 64
    header_height = 24
    preview = Image.new(
        "RGBA",
        (
            label_width + sum(column.width for column in columns),
            header_height + max(column.height for column in columns),
        ),
        (8, 16, 25, 255),
    )
    draw = ImageDraw.Draw(preview)
    preview_font = ImageFont.load_default()
    grid = (41, 65, 74, 255)
    ink = (247, 251, 247, 255)
    muted = (142, 168, 168, 255)

    x = label_width
    for provider, column in zip(PROVIDERS, columns, strict=True):
        preview.alpha_composite(column, (x, header_height))
        draw.text((x + 4, 3), provider.upper(), fill=ink, font=preview_font)
        draw.text((x + TARGET_SIZE[0] // 2 - 6, 13), "00", fill=muted, font=preview_font)
        draw.text(
            (x + TARGET_SIZE[0] + TARGET_SIZE[0] // 2 - 6, 13),
            "01",
            fill=muted,
            font=preview_font,
        )
        draw.line((x, 0, x, preview.height - 1), fill=grid)
        x += column.width
    draw.line((x, 0, x, preview.height - 1), fill=grid)

    for row, state in enumerate(CELL_MAP):
        y = header_height + row * TARGET_SIZE[1]
        draw.text((4, y + 49), state.upper(), fill=muted, font=preview_font)
        draw.line((0, y, preview.width - 1, y), fill=grid)
    draw.line((0, preview.height - 1, preview.width - 1, preview.height - 1), fill=grid)
    draw.line((0, header_height, preview.width - 1, header_height), fill=grid)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}-",
        suffix=".png",
        dir=target.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        preview.save(temporary, format="PNG", optimize=True, compress_level=9)
        with Image.open(temporary) as written:
            written.load()
            if (
                written.format != "PNG"
                or written.mode != "RGBA"
                or written.size != preview.size
            ):
                raise SpriteCompileError("contact sheet failed post-write validation")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _semantic_image(path: Path) -> str:
    with Image.open(path) as source:
        source.load()
        image_format = source.format
        source_mode = source.mode
        rgba = source.convert("RGBA")
    # Alpha-compositing onto transparent black canonicalizes invisible RGB.
    visible = Image.alpha_composite(
        Image.new("RGBA", rgba.size, (0, 0, 0, 0)),
        rgba,
    )
    prefix = f"image:{image_format}:{source_mode}:{rgba.width}x{rgba.height}:"
    return prefix + hashlib.sha256(visible.tobytes()).hexdigest()


def _semantic_files(path: Path) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for file in path.rglob("*"):
        if not file.is_file():
            continue
        relative = file.relative_to(path)
        if file.suffix.lower() == ".json":
            value = json.loads(file.read_text(encoding="utf-8"))
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
            result[relative] = "json:" + hashlib.sha256(canonical.encode()).hexdigest()
        elif file.suffix.lower() == ".png":
            result[relative] = _semantic_image(file)
        else:
            result[relative] = "bytes:" + hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def check_theme(source_dir: Path, theme_dir: Path, preview: Path = DEFAULT_PREVIEW) -> int:
    errors = validate_theme(theme_dir)
    if errors:
        print("committed theme is invalid:", file=sys.stderr)
        for error in errors[:12]:
            print(f"  - {error}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="quotadeck-sprites-") as temp:
        generated = Path(temp) / "theme"
        generated_preview = Path(temp) / "runtime_contact_sheet.png"
        columns = compile_theme(source_dir, generated)
        write_preview(columns, generated_preview)
        actual = _semantic_files(theme_dir)
        expected = _semantic_files(generated)
        preview_matches = (
            preview.is_file()
            and _semantic_image(preview) == _semantic_image(generated_preview)
        )
    changed = sorted(set(actual) ^ set(expected))
    changed.extend(
        path for path in sorted(set(actual) & set(expected)) if actual[path] != expected[path]
    )
    if changed:
        print("sprite assets are out of date:", file=sys.stderr)
        for path in dict.fromkeys(changed):
            print(f"  - {path.as_posix()}", file=sys.stderr)
    if not preview_matches:
        print(f"sprite contact sheet is out of date: {preview}", file=sys.stderr)
    if changed or not preview_matches:
        return 1
    print("sprite assets are up to date")
    return 0


def write_theme() -> None:
    """Backward-compatible entry point used by local tooling."""
    columns = compile_theme(DEFAULT_SOURCE, DEFAULT_THEME)
    write_preview(columns, DEFAULT_PREVIEW)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed assets")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--theme-dir", type=Path, default=DEFAULT_THEME)
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace a non-empty target not previously managed by this compiler",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _validate_output_paths(args.source_dir, args.theme_dir, args.preview)
        if args.check:
            return check_theme(args.source_dir, args.theme_dir, args.preview)
        columns = compile_theme(
            args.source_dir,
            args.theme_dir,
            replace_unmanaged=args.force,
        )
        write_preview(columns, args.preview)
    except (SpriteCompileError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.theme_dir}")
    print(f"wrote {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
