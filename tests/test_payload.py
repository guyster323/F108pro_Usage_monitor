from __future__ import annotations

import pytest
from PIL import Image
from quotadeck.devices.aula_f108.constants import (
    LCD_FRAME_BYTES,
    LCD_HEADER_BYTES,
    LCD_HEIGHT,
    LCD_DELAY_TICK_MS,
    LCD_MAX_FRAMES,
    LCD_MAX_DELAY_MS,
    LCD_PAGE_BYTES,
    LCD_WIDTH,
)
from quotadeck.devices.aula_f108.payload import (
    Frame,
    PayloadError,
    build_payload,
    delay_byte,
    image_to_rgb565,
    rgb565_to_image,
    rgb888_to_rgb565,
    solid_frame,
    validate_payload,
    validate_frames,
)
from quotadeck.devices.aula_f108.protocol import ProtocolError, upload_payload
from quotadeck.devices.aula_f108.transport_mock import MockTransport

def test_rgb565_packing() -> None:
    assert rgb888_to_rgb565(255, 0, 0) == 0xF800
    assert rgb888_to_rgb565(0, 255, 0) == 0x07E0
    assert rgb888_to_rgb565(0, 0, 255) == 0x001F


def test_rgb565_preview_round_trip() -> None:
    image = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (255, 0, 0))
    decoded = rgb565_to_image(image_to_rgb565(image))
    assert decoded.size == image.size
    assert decoded.getpixel((0, 0)) == (255, 0, 0)


def test_delay_byte_units() -> None:
    assert delay_byte(LCD_DELAY_TICK_MS) == 1
    assert delay_byte(400) == 200
    assert delay_byte(LCD_MAX_DELAY_MS) == 255
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


def test_accepts_exact_hard_frame_limit() -> None:
    img = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    frames = [Frame(image=img, delay_ms=LCD_DELAY_TICK_MS) for _ in range(LCD_MAX_FRAMES)]
    validate_frames(frames)
    assert build_payload(frames)[0] == LCD_MAX_FRAMES


def test_rejects_delay_above_firmware_limit() -> None:
    img = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    with pytest.raises(PayloadError, match="510"):
        validate_frames([Frame(image=img, delay_ms=LCD_MAX_DELAY_MS + LCD_DELAY_TICK_MS)])


def test_rejects_delay_between_firmware_ticks() -> None:
    img = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    with pytest.raises(PayloadError, match="2 ms tick"):
        validate_frames([Frame(image=img, delay_ms=3)])


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
    assert transport.features[1][2] == 1
    assert transport.features[-1][:2] == b"\x04\x02"
    assert len(transport.pages) == len(payload) // LCD_PAGE_BYTES
    assert transport.closed is False


@pytest.mark.parametrize(
    ("nack_read", "pages_written"),
    ((1, False), (2, False), (3, True)),
)
def test_echoed_feature_command_with_zero_status_is_a_nack(
    nack_read: int,
    pages_written: bool,
) -> None:
    class EchoNackTransport(MockTransport):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def get_feature(self) -> bytes:
            self.reads += 1
            response = bytearray(super().get_feature())
            if self.reads == nack_read:
                response[3] = 0
            return bytes(response)

    transport = EchoNackTransport()
    payload = build_payload([solid_frame(0, 255, 0)])
    with pytest.raises(ProtocolError, match="did not ACK"):
        upload_payload(transport, payload)
    assert bool(transport.pages) is pages_written


@pytest.mark.parametrize("mutation", ["count", "length", "zero-delay"])
def test_raw_upload_revalidates_before_hid(mutation: str) -> None:
    payload = bytearray(build_payload([solid_frame(0, 255, 0)]))
    if mutation == "count":
        payload[0] = LCD_MAX_FRAMES + 1
    elif mutation == "length":
        payload.extend(b"\xff" * LCD_PAGE_BYTES)
    else:
        payload[1] = 0
    transport = MockTransport()
    with pytest.raises(PayloadError):
        upload_payload(transport, bytes(payload))
    assert transport.features == []
    assert transport.pages == []


def test_upload_snapshots_mutable_payload_before_hid() -> None:
    payload = bytearray(build_payload([solid_frame(0, 255, 0)]))
    expected = bytes(payload)

    class MutatingTransport(MockTransport):
        def set_feature(self, packet: bytes) -> None:
            payload[0] = LCD_MAX_FRAMES + 1
            super().set_feature(packet)

    transport = MutatingTransport()
    upload_payload(transport, payload)

    pages = len(expected) // LCD_PAGE_BYTES
    assert transport.features[1][8] | (transport.features[1][9] << 8) == pages
    assert b"".join(transport.pages) == expected


def test_validate_payload_returns_exact_page_count() -> None:
    payload = build_payload([solid_frame(0, 255, 0)])
    assert validate_payload(payload) == len(payload) // LCD_PAGE_BYTES
