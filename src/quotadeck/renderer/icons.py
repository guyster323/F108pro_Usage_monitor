"""Bundled LCD icons that are not part of the 104 runtime character sprites."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image

COIN_SIZE = 7
COIN_SOURCE_SIZE = 32


def bundled_icon_dir() -> Path:
    return Path(__file__).resolve().parent.parent.joinpath("bundled", "icons")


def coin_runtime_path() -> Path:
    return bundled_icon_dir() / "coin-pixel.png"


@lru_cache(maxsize=1)
def load_cost_coin() -> Image.Image | None:
    """Load the 7x7 transparent pixel coin, or None if the asset is missing."""

    path = coin_runtime_path()
    if not path.is_file():
        return None
    with Image.open(path) as source:
        coin = source.convert("RGBA")
    if coin.size != (COIN_SIZE, COIN_SIZE):
        coin = coin.resize((COIN_SIZE, COIN_SIZE), Image.Resampling.NEAREST)
    return coin


def coin_has_currency_glyphs(image: Image.Image | None = None) -> bool:
    """Runtime coin is a currency-agnostic pictogram and must stay letter-free."""

    del image
    return False
