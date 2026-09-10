from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH

REQUIRED_STATES = {
    "idle",
    "busy",
    "caution",
    "critical",
    "exhausted",
    "reset",
    "offline",
    "stale",
}
REQUIRED_PROVIDERS = {"codex", "claude", "cursor", "grok"}
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
RUNTIME_SPRITE_SIZE = (88, 108)
MIN_OPAQUE_BBOX = (48, 56)
MIN_OPAQUE_PIXELS = 1200
OPAQUE_BASELINE_EXCLUSIVE = 106


class ThemeError(ValueError):
    """Raised when a theme is unsafe or incomplete for runtime rendering."""


@dataclass
class Theme:
    id: str
    name: str
    root: Path
    fps: int
    palette: dict[str, str]
    providers: dict[str, dict]
    sprite_size: tuple[int, int] = RUNTIME_SPRITE_SIZE

    def accent(self, provider: str) -> str:
        info = self.providers.get(provider) or {}
        return str(info.get("accent") or self.palette.get("accent") or "#3DDC97")

    def state_images(self, provider: str, state: str) -> list[Image.Image]:
        resolved_provider = provider if provider in self.providers else "codex"
        info = self.providers.get(resolved_provider) or {}
        states = info.get("states") or {}
        names = states.get(state) or states.get("idle") or []
        folder = self.root / "provider" / resolved_provider
        images: list[Image.Image] = []
        for name in names:
            if not _safe_filename(name):
                continue
            path = folder / name
            if path.is_file():
                with Image.open(path) as source:
                    images.append(source.convert("RGBA"))
        if not images:
            images.append(Image.new("RGBA", self.sprite_size, (0, 0, 0, 0)))
        return images


def _safe_filename(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and Path(name).name == name
        and name.lower().endswith(".png")
    )


def _safe_provider_id(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and name not in {".", ".."}
        and Path(name).name == name
    )


def _valid_color(value: object) -> bool:
    return isinstance(value, str) and HEX_COLOR.fullmatch(value) is not None


def load_theme(path: Path, *, validate: bool = True) -> Theme:
    if path.is_symlink():
        raise ThemeError(f"theme root must not be a symlink: {path}")
    theme_json = path / "theme.json"
    if not theme_json.is_file():
        raise ThemeError(f"theme.json missing in {path}")
    if theme_json.is_symlink():
        raise ThemeError(f"theme.json must not be a symlink: {theme_json}")
    try:
        meta = json.loads(theme_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThemeError(f"cannot read {theme_json}: {exc}") from exc
    if not isinstance(meta, Mapping):
        raise ThemeError("theme.json root must be an object")
    size = meta.get("sprite_size") or {}
    if not isinstance(size, Mapping):
        raise ThemeError("sprite_size must be an object")
    palette = meta.get("palette") or {}
    if not isinstance(palette, Mapping):
        raise ThemeError("palette must be an object")
    for key in ("bg", "text", "accent"):
        if key in palette and not _valid_color(palette[key]):
            raise ThemeError(f"palette.{key} must be #RRGGBB")
    providers = meta.get("providers") or {}
    if not isinstance(providers, Mapping):
        raise ThemeError("providers must be an object")
    for provider, info in providers.items():
        if not _safe_provider_id(provider):
            raise ThemeError(f"unsafe provider id {provider!r}")
        if not isinstance(info, Mapping):
            raise ThemeError(f"{provider}: provider entry must be an object")
        if "accent" in info and not _valid_color(info["accent"]):
            raise ThemeError(f"{provider}.accent must be #RRGGBB")
        states = info.get("states") or {}
        if not isinstance(states, Mapping):
            raise ThemeError(f"{provider}.states must be an object")
    theme_id = meta.get("id", path.name)
    theme_name = meta.get("name", path.name)
    if not isinstance(theme_id, str) or not theme_id:
        raise ThemeError("theme id must be a non-empty string")
    if not isinstance(theme_name, str) or not theme_name:
        raise ThemeError("theme name must be a non-empty string")
    fps = meta.get("fps", 4)
    width = size.get("w", RUNTIME_SPRITE_SIZE[0])
    height = size.get("h", RUNTIME_SPRITE_SIZE[1])
    for label, value in (("fps", fps), ("sprite_size.w", width), ("sprite_size.h", height)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ThemeError(f"{label} must be an integer")
    theme = Theme(
        id=theme_id,
        name=theme_name,
        root=path,
        fps=fps,
        palette=dict(palette),
        providers=dict(providers),
        sprite_size=(width, height),
    )
    if validate:
        errors = validate_theme(path)
        if errors:
            detail = "; ".join(errors[:8])
            if len(errors) > 8:
                detail += f"; and {len(errors) - 8} more"
            raise ThemeError(f"invalid theme {path}: {detail}")
    return theme


def validate_theme(path: Path) -> list[str]:
    errors: list[str] = []
    if path.is_symlink():
        return [f"theme root must not be a symlink: {path}"]
    theme_json = path / "theme.json"
    if not theme_json.is_file():
        return ["theme.json missing"]
    if theme_json.is_symlink():
        return [f"theme.json must not be a symlink: {theme_json}"]
    try:
        meta = json.loads(theme_json.read_text(encoding="utf-8"))
        theme = load_theme(path, validate=False)
    except Exception as exc:
        return [str(exc)]

    canvas = meta.get("canvas") or {}
    if not isinstance(canvas, Mapping):
        return ["canvas must be an object"]
    if (canvas.get("width"), canvas.get("height")) != (LCD_WIDTH, LCD_HEIGHT):
        errors.append(f"canvas must be {LCD_WIDTH}x{LCD_HEIGHT}")
    if meta.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    if meta.get("hud") != "full-bars-v2":
        errors.append("hud must be full-bars-v2")
    if not 1 <= theme.fps <= 30:
        errors.append("fps must be from 1 to 30")
    if theme.sprite_size != RUNTIME_SPRITE_SIZE:
        errors.append(
            f"sprite_size must be {RUNTIME_SPRITE_SIZE[0]}x{RUNTIME_SPRITE_SIZE[1]}"
        )
    if not theme.providers:
        errors.append("providers missing")
    missing_providers = sorted(REQUIRED_PROVIDERS - set(theme.providers))
    if missing_providers:
        errors.append(f"missing providers {', '.join(missing_providers)}")

    for provider, info in theme.providers.items():
        folder = path / "provider" / provider
        if not folder.is_dir():
            errors.append(f"missing provider/{provider}")
            continue
        if folder.is_symlink():
            errors.append(f"provider/{provider} must not be a symlink")
            continue
        states = info.get("states") or {}
        missing_states = sorted(REQUIRED_STATES - set(states))
        if missing_states:
            errors.append(f"{provider}: missing states {', '.join(missing_states)}")
        used_names: set[str] = set()
        for state, files in states.items():
            if not isinstance(files, list) or len(files) != 2:
                errors.append(f"{provider}/{state}: expected exactly 2 frames")
                continue
            hashes: list[str] = []
            for name in files:
                if not _safe_filename(name):
                    errors.append(f"{provider}/{state}: unsafe filename {name!r}")
                    continue
                if name in used_names:
                    errors.append(f"{provider}: duplicate filename {name}")
                used_names.add(name)
                file_path = folder / name
                if not file_path.is_file():
                    errors.append(f"{provider}/{state}: missing {name}")
                    continue
                if file_path.is_symlink():
                    errors.append(f"{provider}/{state}: {name} must not be a symlink")
                    continue
                try:
                    with Image.open(file_path) as source:
                        if source.format != "PNG":
                            errors.append(f"{provider}/{state}: {name} is not PNG")
                        if getattr(source, "n_frames", 1) != 1:
                            errors.append(f"{provider}/{state}: {name} must be a static PNG")
                        if source.mode != "RGBA":
                            errors.append(f"{provider}/{state}: {name} must be RGBA")
                        if source.size != theme.sprite_size:
                            errors.append(
                                f"{provider}/{state}: {name} is "
                                f"{source.size[0]}x{source.size[1]}, expected "
                                f"{theme.sprite_size[0]}x{theme.sprite_size[1]}"
                            )
                            # Do not decode an unexpectedly large asset.
                            continue
                        source.load()
                        rgba = source.convert("RGBA")
                        alpha = rgba.getchannel("A")
                        extrema = alpha.getextrema()
                        if extrema == (0, 0):
                            errors.append(f"{provider}/{state}: {name} is blank")
                        elif extrema[0] != 0 or extrema[1] != 255:
                            errors.append(
                                f"{provider}/{state}: {name} needs transparent margins "
                                "and opaque character pixels"
                            )
                        if any(value not in {0, 255} for value in alpha.getdata()):
                            errors.append(f"{provider}/{state}: {name} alpha must be binary")
                        opaque_colors = {
                            (red, green, blue)
                            for red, green, blue, alpha_value in rgba.getdata()
                            if alpha_value == 255
                        }
                        if len(opaque_colors) > 48:
                            errors.append(
                                f"{provider}/{state}: {name} uses "
                                f"{len(opaque_colors)} visible colours; maximum is 48"
                            )
                        if any(
                            red % 8 or green % 4 or blue % 8
                            for red, green, blue in opaque_colors
                        ):
                            errors.append(
                                f"{provider}/{state}: {name} colours must be RGB565-snapped"
                            )
                        alpha_box = alpha.getbbox()
                        if alpha_box:
                            opaque_width = alpha_box[2] - alpha_box[0]
                            opaque_height = alpha_box[3] - alpha_box[1]
                            if (
                                opaque_width < MIN_OPAQUE_BBOX[0]
                                or opaque_height < MIN_OPAQUE_BBOX[1]
                            ):
                                errors.append(
                                    f"{provider}/{state}: {name} visible bbox is "
                                    f"{opaque_width}x{opaque_height}; expected at least "
                                    f"{MIN_OPAQUE_BBOX[0]}x{MIN_OPAQUE_BBOX[1]}"
                                )
                            if alpha.histogram()[255] < MIN_OPAQUE_PIXELS:
                                errors.append(
                                    f"{provider}/{state}: {name} has too few visible pixels"
                                )
                            if alpha_box[3] != OPAQUE_BASELINE_EXCLUSIVE:
                                errors.append(
                                    f"{provider}/{state}: {name} baseline is y={alpha_box[3]}; "
                                    f"expected {OPAQUE_BASELINE_EXCLUSIVE}"
                                )
                        visible = Image.alpha_composite(
                            Image.new("RGBA", rgba.size, (0, 0, 0, 0)),
                            rgba,
                        )
                        hashes.append(hashlib.sha256(visible.tobytes()).hexdigest())
                except Exception as exc:
                    errors.append(f"{provider}/{state}: cannot open {name}: {exc}")
            if len(hashes) == 2 and len(set(hashes)) == 1:
                errors.append(f"{provider}/{state}: animation frames are identical")
    return errors
