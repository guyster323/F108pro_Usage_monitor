from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class Theme:
    id: str
    name: str
    root: Path
    fps: int
    palette: dict[str, str]
    providers: dict[str, dict]

    def accent(self, provider: str) -> str:
        info = self.providers.get(provider) or {}
        return str(info.get("accent") or self.palette.get("accent") or "#3DDC97")

    def state_images(self, provider: str, state: str) -> list[Image.Image]:
        info = self.providers.get(provider) or self.providers.get("codex") or {}
        states = info.get("states") or {}
        names = states.get(state) or states.get("idle") or []
        folder = self.root / "provider" / provider
        if not folder.is_dir():
            folder = self.root / "provider" / "codex"
        images: list[Image.Image] = []
        for name in names:
            path = folder / name
            if path.is_file():
                images.append(Image.open(path).convert("RGBA"))
        if not images:
            images.append(Image.new("RGBA", (64, 72), (0, 0, 0, 0)))
        return images


def load_theme(path: Path) -> Theme:
    meta = json.loads((path / "theme.json").read_text(encoding="utf-8"))
    return Theme(
        id=meta.get("id", path.name),
        name=meta.get("name", path.name),
        root=path,
        fps=int(meta.get("fps", 4)),
        palette=meta.get("palette") or {},
        providers=meta.get("providers") or {},
    )


def validate_theme(path: Path) -> list[str]:
    errors: list[str] = []
    theme_json = path / "theme.json"
    if not theme_json.is_file():
        return ["theme.json missing"]
    try:
        theme = load_theme(path)
    except Exception as exc:
        return [str(exc)]
    for provider, info in theme.providers.items():
        folder = path / "provider" / provider
        if not folder.is_dir():
            errors.append(f"missing provider/{provider}")
            continue
        for state, files in (info.get("states") or {}).items():
            for name in files:
                if not (folder / name).is_file():
                    errors.append(f"{provider}/{state}: missing {name}")
    return errors
