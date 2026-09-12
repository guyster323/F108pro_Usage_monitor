from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from tools.gen_sprites import (
    DEFAULT_SOURCE,
    DEFAULT_USAGE_SOURCE,
    TARGET_SIZE,
    USAGE_CELL_MAP,
    CELL_MAP,
    _OctreeBucket,
    _cell,
    _common_alpha_box,
    _compile_cell,
    _octree_sort_buckets,
    _quantize_rgba_octree,
)

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "themes" / "quotadeck-crew"

# Windows CI 34668676370 / 34677982956 reported these 21 frames out of date
# because Pillow FASTOCTREE qsort tie-breaks palettes differently than the
# committed (stable occupancy+index) assets.
FLAKY_FRAMES = (
    ("claude", "idle", 0),
    ("claude", "idle", 1),
    ("claude", "usage_150", 0),
    ("claude", "usage_300", 0),
    ("claude", "usage_300", 1),
    ("codex", "exhausted", 1),
    ("codex", "offline", 0),
    ("codex", "reset", 1),
    ("codex", "stale", 0),
    ("codex", "usage_150", 0),
    ("cursor", "offline", 0),
    ("cursor", "offline", 1),
    ("cursor", "usage_below", 0),
    ("grok", "caution", 0),
    ("grok", "caution", 1),
    ("grok", "critical", 0),
    ("grok", "idle", 0),
    ("grok", "idle", 1),
    ("grok", "offline", 1),
    ("grok", "usage_200", 1),
    ("grok", "usage_below", 1),
)


def _state_cells(provider: str, state: str) -> tuple[list[Image.Image], tuple[int, int, int, int]]:
    usage = state.startswith("usage_")
    source = (DEFAULT_USAGE_SOURCE if usage else DEFAULT_SOURCE) / f"{provider}.png"
    cell_map = USAGE_CELL_MAP if usage else CELL_MAP
    with Image.open(source) as opened:
        opened.load()
        sheet = opened.copy()
    cells = [_cell(sheet, column, row) for column, row in cell_map[state]]
    return cells, _common_alpha_box(cells)


def test_octree_equal_occupancy_keeps_spatial_index_order() -> None:
    low = _OctreeBucket()
    low.count = 4
    first_high = _OctreeBucket()
    first_high.count = 9
    empty = _OctreeBucket()
    second_high = _OctreeBucket()
    second_high.count = 9
    ordered = _octree_sort_buckets([low, first_high, empty, second_high])
    assert ordered[0] is first_high
    assert ordered[1] is second_high
    assert ordered[2] is low
    assert ordered[3] is empty


def test_compile_cell_does_not_call_pillow_quantize(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("Pillow FASTOCTREE qsort is not a portable palette")

    monkeypatch.setattr(Image.Image, "quantize", boom)
    image = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 20, 79, 89), fill=(24, 216, 160, 255))
    compiled = _compile_cell(image, (0, 0, 100, 120))
    assert compiled.size == TARGET_SIZE
    assert _quantize_rgba_octree(image).size == image.size


def test_observed_fastoctree_mismatch_frames_match_committed_theme() -> None:
    ours_mismatches = []
    for provider, state, pose in FLAKY_FRAMES:
        cells, box = _state_cells(provider, state)
        committed_path = THEME / "provider" / provider / f"{state}_{pose:02d}.png"
        with Image.open(committed_path) as source:
            committed = source.convert("RGBA").tobytes()
        if _compile_cell(cells[pose], box).tobytes() != committed:
            ours_mismatches.append(f"{provider}/{state}_{pose:02d}")
    assert ours_mismatches == []


def test_export_png_keeps_committed_bytes_when_pixels_match(tmp_path: Path) -> None:
    from tools.export_crew import _write_png

    image = Image.new("RGB", (8, 4), (7, 12, 20))
    dest = tmp_path / "preview.png"
    image.save(dest, format="PNG")
    original = dest.read_bytes()
    _write_png(image.copy(), dest)
    assert dest.read_bytes() == original


def test_compile_cell_independent_of_python_hash_seed_and_cwd() -> None:
    helper = r"""
import hashlib, sys
from pathlib import Path
from PIL import Image
ROOT = Path(r"%s")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tools.gen_sprites import (
    CELL_MAP, DEFAULT_SOURCE, _cell, _common_alpha_box, _compile_cell,
)
with Image.open(DEFAULT_SOURCE / "claude.png") as opened:
    opened.load()
    sheet = opened.copy()
cells = [_cell(sheet, *loc) for loc in CELL_MAP["idle"]]
box = _common_alpha_box(cells)
sys.stdout.write(hashlib.sha256(_compile_cell(cells[0], box).tobytes()).hexdigest())
""" % ROOT.as_posix()
    hashes = []
    other_cwd = tempfile.gettempdir()
    if Path(other_cwd).resolve() == ROOT.resolve():
        other_cwd = str(ROOT.parent)
    for seed, cwd in (("0", str(ROOT)), ("1", other_cwd)):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", helper],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        hashes.append(completed.stdout.strip())
    assert hashes[0] == hashes[1]
    with Image.open(THEME / "provider" / "claude" / "idle_00.png") as source:
        committed = hashlib.sha256(source.convert("RGBA").tobytes()).hexdigest()
    assert hashes[0] == committed
