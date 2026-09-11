#!/usr/bin/env python3
"""Confirm RGB565 frame bytes stay at the F108 compressed payload size."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quotadeck.devices.aula_f108.constants import (
    LCD_FRAME_BYTES,
    LCD_HEIGHT,
    LCD_WIDTH,
)
from quotadeck.devices.aula_f108.payload import (
    Frame,
    build_payload,
    image_to_rgb565,
    validate_payload,
)
from quotadeck.renderer.layout import paint_empty


def main() -> int:
    image = paint_empty()
    if image.size != (LCD_WIDTH, LCD_HEIGHT):
        print("empty card is not 240x135", file=sys.stderr)
        return 1
    rgb565 = image_to_rgb565(image)
    if len(rgb565) != LCD_FRAME_BYTES:
        print(f"RGB565 size {len(rgb565)} != {LCD_FRAME_BYTES}", file=sys.stderr)
        return 1
    payload = build_payload([Frame(image=image.convert("RGB"), delay_ms=5000)])
    validate_payload(payload)
    print(f"compressed payload ok: {LCD_FRAME_BYTES} RGB565 bytes per frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
