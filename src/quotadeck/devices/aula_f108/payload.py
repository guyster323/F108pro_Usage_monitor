from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from quotadeck.devices.aula_f108.constants import (
    LCD_FRAME_BYTES,
    LCD_HEADER_BYTES,
    LCD_HEIGHT,
    LCD_MAX_FRAMES,
    LCD_PAGE_BYTES,
    LCD_WIDTH,
)


class PayloadError(ValueError):
    """Raised when a GIF/frame list is unsafe to upload."""


@dataclass(frozen=True, slots=True)
class Frame:
    image: Image.Image
    delay_ms: int = 400


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def delay_byte(delay_ms: int) -> int:
    # Keyboard stores delay as centiseconds / 2 (20 ms units), min 1, max 255 (~5.1 s).
    centiseconds = max(1, int(round(delay_ms / 10.0)))
    value = max(1, min(255, centiseconds // 2))
    return value


def validate_frames(frames: list[Frame]) -> None:
    if not frames:
        raise PayloadError("no frames")
    if len(frames) > LCD_MAX_FRAMES:
        raise PayloadError(
            f"refusing {len(frames)} frames; firmware hard limit is {LCD_MAX_FRAMES}. "
            "Extra frames overwrite onboard menu graphics permanently."
        )
    for i, frame in enumerate(frames):
        w, h = frame.image.size
        if w != LCD_WIDTH or h != LCD_HEIGHT:
            raise PayloadError(
                f"frame {i} is {w}x{h}; expected {LCD_WIDTH}x{LCD_HEIGHT}"
            )


def image_to_rgb565(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    if rgb.size != (LCD_WIDTH, LCD_HEIGHT):
        raise PayloadError(f"image is {rgb.size[0]}x{rgb.size[1]}; expected {LCD_WIDTH}x{LCD_HEIGHT}")
    pixels = rgb.tobytes()
    out = bytearray(LCD_FRAME_BYTES)
    src = 0
    dst = 0
    while src < len(pixels):
        r = pixels[src]
        g = pixels[src + 1]
        b = pixels[src + 2]
        value = rgb888_to_rgb565(r, g, b)
        out[dst] = value & 0xFF
        out[dst + 1] = (value >> 8) & 0xFF
        src += 3
        dst += 2
    return bytes(out)


def build_payload(frames: list[Frame]) -> bytes:
    validate_frames(frames)
    raw_size = LCD_HEADER_BYTES + LCD_FRAME_BYTES * len(frames)
    page_count = (raw_size + LCD_PAGE_BYTES - 1) // LCD_PAGE_BYTES
    buf = bytearray(b"\xff" * (page_count * LCD_PAGE_BYTES))
    buf[0] = len(frames)
    for i, frame in enumerate(frames):
        buf[1 + i] = delay_byte(frame.delay_ms)
        pix = image_to_rgb565(frame.image)
        start = LCD_HEADER_BYTES + i * LCD_FRAME_BYTES
        buf[start : start + LCD_FRAME_BYTES] = pix
    return bytes(buf)


def page_count(payload: bytes) -> int:
    if len(payload) % LCD_PAGE_BYTES != 0:
        raise PayloadError("payload is not padded to 4096-byte pages")
    return len(payload) // LCD_PAGE_BYTES


def solid_frame(r: int, g: int, b: int, delay_ms: int = 1000) -> Frame:
    image = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (r, g, b))
    return Frame(image=image, delay_ms=delay_ms)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    raw = color.strip().lstrip("#")
    if len(raw) != 6:
        raise PayloadError(f"invalid hex color {color!r}")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
