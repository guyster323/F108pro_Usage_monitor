from __future__ import annotations

from quotadeck.devices.aula_f108.constants import LCD_PAGE_BYTES, REPORT_LEN
from quotadeck.devices.aula_f108.transport import pad64


class MockTransport:
    def __init__(self) -> None:
        self.features: list[bytes] = []
        self.pages: list[bytes] = []
        self._last: bytes = bytes(REPORT_LEN)
        self.closed = False

    def set_feature(self, data: bytes) -> None:
        packet = pad64(data)
        self.features.append(packet)
        ack = bytearray(packet)
        ack[3] = 0x01
        self._last = bytes(ack)

    def get_feature(self) -> bytes:
        return self._last

    def write_lcd_page(self, data: bytes) -> None:
        if len(data) != LCD_PAGE_BYTES:
            raise ValueError(f"LCD page must be {LCD_PAGE_BYTES} bytes, got {len(data)}")
        self.pages.append(data)

    def read_lcd_ack(self, timeout_ms: int = 300) -> bytes:
        _ = timeout_ms
        ack = bytearray(REPORT_LEN)
        ack[0] = 0x01
        ack[1] = 0x5A
        ack[2] = 0x02
        return bytes(ack)

    def close(self) -> None:
        self.closed = True
