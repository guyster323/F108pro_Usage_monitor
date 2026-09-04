from __future__ import annotations

import pytest
from PIL import Image

from quotadeck.devices.aula_f108.constants import (
    LCD_FRAME_BYTES,
    LCD_HEADER_BYTES,
    LCD_HEIGHT,
    LCD_MAX_FRAMES,
    LCD_PAGE_BYTES,
    LCD_WIDTH,
)
from quotadeck.devices.aula_f108.payload import (
    Frame,
    PayloadError,
    build_payload,
    delay_byte,
    rgb888_to_rgb565,
    solid_frame,
    validate_frames,
)
from quotadeck.devices.aula_f108.protocol import upload_payload
from quotadeck.devices.aula_f108.transport_mock import MockTransport


def test_rgb565_packing() -> None:
    assert rgb888_to_rgb565(255, 0, 0) == 0xF800
    assert rgb888_to_rgb565(0, 255, 0) == 0x07E0
    assert rgb888_to_rgb565(0, 0, 255) == 0x001F


def test_delay_byte_units() -> None:
    assert delay_byte(20) == 1
    assert delay_byte(400) == 20
    assert delay_byte(5100) == 255
    assert delay_byte(1) == 1


def test_build_payload_header_and_padding() -> None:
    frame = solid_frame(255, 0, 0, delay_ms=400)
    payload = build_payload([frame])
    assert payload[0] == 1
    assert payload[1] == delay_byte(400)
    assert payload[2] == 0xFF
    assert len(payload) % LCD_PAGE_BYTES == 0
    first = payload[LCD_HEADER_BYTES] | (payload[LCD_HEADER_BYTES + 1] << 8)
    assert first == 0xF800
    expected_pages = (LCD_HEADER_BYTES + LCD_FRAME_BYTES + LCD_PAGE_BYTES - 1) // LCD_PAGE_BYTES
    assert len(payload) == expected_pages * LCD_PAGE_BYTES


def test_rejects_too_many_frames() -> None:
    img = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    frames = [Frame(image=img, delay_ms=100) for _ in range(LCD_MAX_FRAMES + 1)]
    with pytest.raises(PayloadError, match="141"):
        validate_frames(frames)


def test_rejects_wrong_size() -> None:
    frames = [Frame(image=Image.new("RGB", (10, 10)), delay_ms=100)]
    with pytest.raises(PayloadError, match="240x135"):
        validate_frames(frames)


def test_mock_upload() -> None:
    payload = build_payload([solid_frame(0, 255, 0)])
    transport = MockTransport()
    upload_payload(transport, payload)
    assert transport.features[0][:2] == b"\x04\x18"
    assert transport.features[1][:2] == b"\x04\x72"
    assert transport.features[-1][:2] == b"\x04\x02"
    assert len(transport.pages) == len(payload) // LCD_PAGE_BYTES
    assert transport.closed is False
