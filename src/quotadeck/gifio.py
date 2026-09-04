from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_MAX_FRAMES, LCD_WIDTH
from quotadeck.devices.aula_f108.payload import Frame, PayloadError


def load_gif(path: Path) -> list[Frame]:
    image = Image.open(path)
    if image.format != "GIF":
        raise PayloadError(f"{path} is not a GIF")
    frames: list[Frame] = []
    canvas = Image.new("RGB", image.size, (0, 0, 0))
    for frame in ImageSequence.Iterator(image):
        delay = int(frame.info.get("duration") or 100)
        rgba = frame.convert("RGBA")
        canvas.paste(rgba, (0, 0), rgba)
        fitted = canvas.copy()
        if fitted.size != (LCD_WIDTH, LCD_HEIGHT):
            raise PayloadError(
                f"GIF frame is {fitted.size[0]}x{fitted.size[1]}; expected {LCD_WIDTH}x{LCD_HEIGHT}"
            )
        frames.append(Frame(image=fitted.copy(), delay_ms=max(20, delay)))
        if len(frames) > LCD_MAX_FRAMES:
            raise PayloadError(f"GIF has more than {LCD_MAX_FRAMES} frames")
    return frames
