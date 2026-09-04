from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from quotadeck.devices.aula_f108.constants import (
    APPLY_SETTLE_S,
    CMD_APPLY,
    CMD_BEGIN,
    CMD_CLOCK_INIT,
    CMD_LCD_HEADER,
    COMMAND_DELAY_S,
    LCD_IMAGE_NUMBER,
    LCD_PAGE_BYTES,
    REPORT_LEN,
)
from quotadeck.devices.aula_f108.payload import page_count
from quotadeck.devices.aula_f108.transport import Transport, pad64

Progress = Callable[[int, int, str], None]


class ProtocolError(RuntimeError):
    pass


def _packet(prefix: bytes, **fields: int) -> bytes:
    data = bytearray(REPORT_LEN)
    data[: len(prefix)] = prefix
    for index, value in fields.items():
        # fields keys are "b2", "b8", ...
        if index.startswith("b"):
            data[int(index[1:])] = value & 0xFF
    return bytes(data)


def send_feature(transport: Transport, name: str, payload: bytes, *, readback: bool = True) -> bytes:
    transport.set_feature(payload)
    time.sleep(COMMAND_DELAY_S)
    if not readback:
        return b""
    response = transport.get_feature()
    if len(response) < 4:
        raise ProtocolError(f"{name}: short readback")
    if response[3] != 0x01 and response[:2] != payload[:2]:
        raise ProtocolError(f"{name}: device did not ACK ({response[:8].hex()})")
    return response


def upload_payload(transport: Transport, payload: bytes, progress: Progress | None = None) -> None:
    pages = page_count(payload)
    send_feature(transport, "begin", pad64(CMD_BEGIN))
    header = bytearray(REPORT_LEN)
    header[0:2] = CMD_LCD_HEADER
    header[2] = LCD_IMAGE_NUMBER
    header[8] = pages & 0xFF
    header[9] = (pages >> 8) & 0xFF
    response = send_feature(transport, "lcd-header", bytes(header))
    if len(response) > 2 and response[2] not in {LCD_IMAGE_NUMBER, 0x01}:
        raise ProtocolError(f"lcd-header: unexpected image slot {response[2]:02x}")
    for i in range(pages):
        page = payload[i * LCD_PAGE_BYTES : (i + 1) * LCD_PAGE_BYTES]
        transport.write_lcd_page(page)
        ack = transport.read_lcd_ack(300)
        if len(ack) < 2 or ack[0] != 0x01 or ack[1] != 0x5A:
            raise ProtocolError(f"LCD page {i + 1}/{pages} unexpected ACK {ack[:8].hex()}")
        if progress:
            progress(i + 1, pages, "upload")
    send_feature(transport, "apply", pad64(CMD_APPLY))
    if progress:
        progress(pages, pages, "flash")
    time.sleep(APPLY_SETTLE_S)


def sync_clock(transport: Transport, when: datetime | None = None) -> datetime:
    when = when or datetime.now().astimezone()
    send_feature(transport, "begin", pad64(CMD_BEGIN))
    clock_init = bytearray(REPORT_LEN)
    clock_init[0:2] = CMD_CLOCK_INIT
    clock_init[8] = 0x01
    send_feature(transport, "clock-init", bytes(clock_init))
    data = bytearray(REPORT_LEN)
    data[0] = 0x00
    data[1] = 0x01
    data[2] = 0x5A
    data[3] = when.year % 2000
    data[4] = when.month
    data[5] = when.day
    data[6] = when.hour
    data[7] = when.minute
    data[8] = when.second
    data[10] = (when.weekday() + 1) % 7  # 0=Sunday
    data[62] = 0xAA
    data[63] = 0x55
    send_feature(transport, "clock-data", bytes(data))
    send_feature(transport, "apply", pad64(CMD_APPLY))
    return when
