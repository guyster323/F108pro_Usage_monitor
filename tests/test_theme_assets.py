from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, __version__ as PILLOW_VERSION

import quotadeck.config as config_module
from quotadeck.config import bundled_theme_dir, default_theme_dir
from quotadeck.renderer.sprites import REQUIRED_STATES, ThemeError, load_theme, validate_theme
from tools.gen_sprites import (
    DEFAULT_BUNDLED_THEME,
    _byte_files,
    _compile_cell,
    _primary_alpha_box,
    _semantic_image,
    _validate_output_paths,
    compile_theme,
    sync_bundled_theme,
)
from tools.extract_checker_alpha import CheckerExtractionError, extract

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "themes" / "quotadeck-crew"
SOURCE = ROOT / "artwork" / "sprite_sources"
USAGE_SOURCE = ROOT / "artwork" / "usage_sprite_sources"
BUNDLED_THEME = ROOT / "src" / "quotadeck" / "bundled" / "themes" / "quotadeck-crew"


def test_generated_theme_contract() -> None:
    assert validate_theme(THEME) == []
    theme = load_theme(THEME)
    assert set(theme.providers) == {"codex", "claude", "cursor", "grok"}
    assert theme.sprite_size == (88, 108)
    for provider, info in theme.providers.items():
        assert set(info["states"]) == REQUIRED_STATES
        for state, names in info["states"].items():
            assert len(names) == 2
            for name in names:
                with Image.open(THEME / "provider" / provider / name) as image:
                    assert image.mode == "RGBA"
                    assert image.size == theme.sprite_size
                    alpha = image.getchannel("A")
                    assert set(alpha.getdata()) <= {0, 255}
                    assert alpha.getbbox()[3] == theme.sprite_size[1] - 2


def test_theme_records_reproducible_asset_pipeline() -> None:
    meta = json.loads((THEME / "theme.json").read_text(encoding="utf-8"))
    assert meta["hud"] == "full-bars-v2"
    assert meta["asset_pipeline"]["compiler"] == "tools/gen_sprites.py"
    assert meta["asset_pipeline"]["pillow"] == PILLOW_VERSION
    for provider in ("codex", "claude", "cursor", "grok"):
        assert (ROOT / "artwork" / "sprite_sources" / f"{provider}.png").is_file()
        assert (ROOT / "artwork" / "usage_sprite_sources" / f"{provider}.png").is_file()


def test_bundled_package_theme_matches_canonical_theme_exactly() -> None:
    assert BUNDLED_THEME == DEFAULT_BUNDLED_THEME
    assert validate_theme(BUNDLED_THEME) == []
    assert _byte_files(BUNDLED_THEME) == _byte_files(THEME)
    assert bundled_theme_dir() == BUNDLED_THEME
    assert default_theme_dir() == THEME


def test_wheel_uses_package_data_instead_of_install_scheme_data_files() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = project["tool"]["setuptools"]
    assert "data-files" not in setuptools
    patterns = setuptools["package-data"]["quotadeck"]
    assert "bundled/themes/quotadeck-crew/theme.json" in patterns
    assert "bundled/icons/*.png" in patterns
    for provider in ("codex", "claude", "cursor", "grok"):
        assert (
            f"bundled/themes/quotadeck-crew/provider/{provider}/*.png" in patterns
        )


def test_installed_layout_resolves_theme_beside_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "target" / "quotadeck"
    fake_config = package / "config.py"
    fake_config.parent.mkdir(parents=True)
    fake_config.touch()
    installed_theme = package / "bundled" / "themes" / "quotadeck-crew"
    installed_theme.mkdir(parents=True)

    monkeypatch.setattr(config_module, "__file__", str(fake_config))

    assert config_module.bundled_theme_dir() == installed_theme
    assert config_module.default_theme_dir() == installed_theme


def test_bundled_theme_sync_is_exact_and_refuses_unmanaged_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "package" / "bundled" / "themes" / "quotadeck-crew"
    sync_bundled_theme(THEME, target)
    assert validate_theme(target) == []
    assert _byte_files(target) == _byte_files(THEME)

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ValueError, match="unmanaged bundled theme"):
        sync_bundled_theme(THEME, unmanaged)
    assert (unmanaged / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_source_and_output_paths_must_not_overlap(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        compile_theme(SOURCE, SOURCE)
    with pytest.raises(ValueError, match="preview must not be inside"):
        _validate_output_paths(SOURCE, tmp_path / "theme", SOURCE / "codex.png")
    with pytest.raises(ValueError, match="bundled theme directories must not overlap"):
        _validate_output_paths(
            SOURCE,
            tmp_path / "theme",
            tmp_path / "preview.png",
            bundled_theme_dir=SOURCE / "bundled",
        )
    with pytest.raises(ValueError, match="generated and bundled.*must not overlap"):
        _validate_output_paths(
            SOURCE,
            tmp_path / "theme",
            tmp_path / "preview.png",
            usage_source_dir=USAGE_SOURCE,
            bundled_theme_dir=tmp_path / "theme",
        )


def test_checker_recovery_refuses_to_destroy_existing_alpha(tmp_path: Path) -> None:
    source = tmp_path / "already-transparent.png"
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((8, 8, 31, 31), fill=(20, 200, 150, 255))
    image.save(source)
    with pytest.raises(CheckerExtractionError, match="already contains transparency"):
        extract(source, tmp_path / "recovered.png")


def test_detached_particle_does_not_move_character_baseline() -> None:
    clean = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(clean)
    draw.rectangle((20, 20, 79, 89), fill=(24, 216, 160, 255))
    decorated = clean.copy()
    ImageDraw.Draw(decorated).rectangle((4, 108, 7, 111), fill=(255, 255, 255, 255))
    common_box = (0, 0, 100, 120)
    clean_compiled = _compile_cell(clean, common_box)
    decorated_compiled = _compile_cell(decorated, common_box)
    assert _primary_alpha_box(clean_compiled.getchannel("A"))[3] == 106
    assert _primary_alpha_box(decorated_compiled.getchannel("A"))[3] == 106


def test_semantic_png_hash_ignores_invisible_rgb_but_tracks_format(tmp_path: Path) -> None:
    first = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    second = first.copy()
    second.putpixel((0, 0), (255, 1, 3, 0))
    png_a = tmp_path / "a.png"
    png_b = tmp_path / "b.png"
    disguised_tiff = tmp_path / "tiff.png"
    first.save(png_a, format="PNG")
    second.save(png_b, format="PNG")
    first.save(disguised_tiff, format="TIFF")
    assert _semantic_image(png_a) == _semantic_image(png_b)
    assert _semantic_image(png_a) != _semantic_image(disguised_tiff)


def _copy_theme(tmp_path: Path) -> Path:
    target = tmp_path / "theme"
    shutil.copytree(THEME, target)
    return target


def test_theme_validator_returns_errors_for_malformed_structures(tmp_path: Path) -> None:
    mutations = (
        [],
        {"canvas": [], "providers": {}},
        {"canvas": {"width": 240, "height": 135}, "providers": []},
    )
    for index, meta in enumerate(mutations):
        target = tmp_path / f"malformed-{index}"
        target.mkdir()
        (target / "theme.json").write_text(json.dumps(meta), encoding="utf-8")
        assert validate_theme(target)


def test_theme_loader_wraps_invalid_numeric_metadata(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    path = target / "theme.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["fps"] = "fast"
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ThemeError, match="fps must be an integer"):
        load_theme(target)


def test_theme_validator_rejects_bad_accent_and_missing_provider(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    path = target / "theme.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["providers"]["codex"]["accent"] = "red"
    del meta["providers"]["grok"]
    path.write_text(json.dumps(meta), encoding="utf-8")
    errors = validate_theme(target)
    assert any("#RRGGBB" in error for error in errors)

    meta["providers"]["codex"]["accent"] = "#19D79C"
    path.write_text(json.dumps(meta), encoding="utf-8")
    assert any("missing providers" in error for error in validate_theme(target))
    with pytest.raises(ThemeError, match="invalid theme"):
        load_theme(target)


def test_every_state_requires_two_distinct_frames(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    path = target / "theme.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["providers"]["codex"]["states"]["caution"] = ["caution_00.png"]
    path.write_text(json.dumps(meta), encoding="utf-8")
    assert any("exactly 2" in error for error in validate_theme(target))

    meta["providers"]["codex"]["states"]["caution"] = [
        "caution_00.png",
        "caution_00.png",
    ]
    path.write_text(json.dumps(meta), encoding="utf-8")
    assert any("identical" in error for error in validate_theme(target))


def test_runtime_sprite_size_and_visible_footprint_are_strict(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    manifest = target / "theme.json"
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    meta["sprite_size"] = {"w": 2, "h": 2}
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    assert any("88x108" in error for error in validate_theme(target))

    meta["sprite_size"] = {"w": 88, "h": 108}
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    tiny = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    tiny.putpixel((44, 105), (255, 255, 255, 255))
    tiny.save(target / "provider" / "codex" / "idle_00.png")
    errors = validate_theme(target)
    assert any("visible bbox" in error for error in errors)
    assert any("too few visible pixels" in error for error in errors)


def test_runtime_palette_is_small_and_rgb565_snapped(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    path = target / "provider" / "codex" / "idle_00.png"
    with Image.open(path) as source:
        image = source.convert("RGBA")
    index = 0
    for y in range(image.height):
        for x in range(image.width):
            if image.getpixel((x, y))[3] == 255:
                image.putpixel(
                    (x, y),
                    ((index % 32) * 8, ((index // 32) % 64) * 4, 1, 255),
                )
                index += 1
    image.save(path)
    errors = validate_theme(target)
    assert any("maximum is 48" in error for error in errors)
    assert any("RGB565-snapped" in error for error in errors)


def test_transparent_rgb_does_not_fake_a_distinct_pose(tmp_path: Path) -> None:
    target = _copy_theme(tmp_path)
    folder = target / "provider" / "codex"
    with Image.open(folder / "idle_00.png") as source:
        duplicate = source.convert("RGBA")
    duplicate.putpixel((0, 0), (255, 1, 3, 0))
    duplicate.save(folder / "idle_01.png")
    assert any("identical" in error for error in validate_theme(target))


def test_theme_validator_contains_pillow_decode_errors(monkeypatch) -> None:
    def bomb(*_args, **_kwargs):
        raise Image.DecompressionBombError("test bomb")

    monkeypatch.setattr(Image, "open", bomb)
    errors = validate_theme(THEME)
    assert errors
    assert all("cannot open" in error for error in errors)
