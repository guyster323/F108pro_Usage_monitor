from __future__ import annotations

from pathlib import Path

from PIL import Image

from quotadeck.cli import cmd_render
from argparse import Namespace

ROOT = Path(__file__).resolve().parents[1]
OUT_GIF = ROOT / "docs" / "hero-preview.gif"
OUT_PNG = ROOT / "docs" / "hud-preview.png"


def main() -> None:
    OUT_GIF.parent.mkdir(parents=True, exist_ok=True)
    cmd_render(
        Namespace(
            fixture=str(ROOT / "tests" / "fixtures" / "usage.json"),
            out=str(OUT_GIF),
            theme=None,
            budget="32",
        )
    )
    image = Image.open(OUT_GIF)
    image.seek(0)
    image.convert("RGB").save(OUT_PNG)
    print(f"wrote {OUT_GIF} and {OUT_PNG} size={image.size}")


if __name__ == "__main__":
    main()
