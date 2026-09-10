from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from PIL import Image

from quotadeck.devices.aula_f108.constants import (
    LCD_FRAME_BYTES,
    LCD_HEADER_BYTES,
    LCD_HEIGHT,
    LCD_MAX_DELAY_MS,
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
    # Keyboard stores delay directly in 20 ms units, min 1, max 255.
    return max(1, min(255, quantize_delay_ms(delay_ms) // 20))


def quantize_delay_ms(delay_ms: int | float) -> int:
    """Round a finite duration to the nearest firmware tick (ties round up)."""
    value = float(delay_ms)
    if not isfinite(value):
        raise PayloadError(f"invalid frame delay {delay_ms!r}")
    return max(20, int((value + 10.0) // 20.0) * 20)

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
        if frame.delay_ms < 20 or frame.delay_ms > LCD_MAX_DELAY_MS:
            raise PayloadError(
                f"frame {i} delay is {frame.delay_ms} ms; expected 20..{LCD_MAX_DELAY_MS} ms"
            )
        if quantize_delay_ms(frame.delay_ms) != frame.delay_ms:
            raise PayloadError(
                f"frame {i} delay is {frame.delay_ms} ms; expected an exact 20 ms tick"
            )


def validate_payload(payload: bytes) -> int:
    """Validate a serialized LCD payload again at the hardware boundary."""
    pages = page_count(payload)
    if len(payload) < LCD_HEADER_BYTES:
        raise PayloadError("payload is shorter than the LCD header")
    frame_count = payload[0]
    if not 1 <= frame_count <= LCD_MAX_FRAMES:
        raise PayloadError(
            f"payload declares {frame_count} frames; expected 1..{LCD_MAX_FRAMES}"
        )
    raw_size = LCD_HEADER_BYTES + LCD_FRAME_BYTES * frame_count
    expected_size = (
        (raw_size + LCD_PAGE_BYTES - 1) // LCD_PAGE_BYTES
    ) * LCD_PAGE_BYTES
    if len(payload) != expected_size:
        raise PayloadError(
            f"payload length {len(payload)} does not match {frame_count} frames "
            f"({expected_size} bytes expected)"
        )
    if any(value == 0 for value in payload[1 : frame_count + 1]):
        raise PayloadError("payload contains a zero frame-delay byte")
    return pages

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


def rgb565_to_image(pixels: bytes) -> Image.Image:
    """Decode one LCD frame for a deterministic hardware-palette preview."""
    if len(pixels) != LCD_FRAME_BYTES:
        raise PayloadError(
            f"RGB565 frame is {len(pixels)} bytes; expected {LCD_FRAME_BYTES}"
        )
    rgb = bytearray(LCD_WIDTH * LCD_HEIGHT * 3)
    dst = 0
    for src in range(0, len(pixels), 2):
        value = pixels[src] | (pixels[src + 1] << 8)
        red = (value >> 11) & 0x1F
        green = (value >> 5) & 0x3F
        blue = value & 0x1F
        rgb[dst] = (red << 3) | (red >> 2)
        rgb[dst + 1] = (green << 2) | (green >> 4)
        rgb[dst + 2] = (blue << 3) | (blue >> 2)
        dst += 3
    return Image.frombytes("RGB", (LCD_WIDTH, LCD_HEIGHT), bytes(rgb))

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
