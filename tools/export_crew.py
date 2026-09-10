from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "themes" / "quotadeck-crew" / "provider"
OUT = ROOT / "docs" / "assets"
SCALE = 3
LABELS = (
    ("codex", "CODEX", (0, 245, 180)),
    ("claude", "CLAUDE", (255, 159, 90)),
    ("cursor", "CURSOR", (80, 180, 255)),
    ("grok", "GROK", (180, 140, 255)),
)

def _font(size: int) -> ImageFont.ImageFont:
    _ = size
    # Pillow's bundled font is identical on Windows and CI; system Consolas is
    # not, and made documentation previews drift across platforms.
    return ImageFont.load_default()

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    portraits: list[tuple[Image.Image, str, tuple[int, int, int]]] = []
    for name, label, color in LABELS:
        sprite = Image.open(THEME / name / "idle_00.png").convert("RGBA")
        big = sprite.resize((sprite.width * SCALE, sprite.height * SCALE), Image.Resampling.NEAREST)
        card = Image.new("RGBA", (big.width + 24, big.height + 48), (7, 12, 20, 255))
        card.paste(big, (12, 8), big)
        draw = ImageDraw.Draw(card)
        draw.text((12, big.height + 16), label, fill=color, font=_font(18))
        dest = OUT / f"crew-{name}.png"
        card.convert("RGB").save(dest)
        portraits.append((card, label, color))
    gap = 16
    width = sum(item[0].width for item in portraits) + gap * (len(portraits) + 1)
    height = max(item[0].height for item in portraits) + 32
    lineup = Image.new("RGB", (width, height), (7, 12, 20))
    x = gap
    for card, _label, _color in portraits:
        lineup.paste(card.convert("RGB"), (x, 16))
        x += card.width + gap
    dest = OUT / "crew-lineup.png"
    lineup.save(dest)
    print(f"wrote {dest} and {len(portraits)} portraits")


if __name__ == "__main__":
    main()
