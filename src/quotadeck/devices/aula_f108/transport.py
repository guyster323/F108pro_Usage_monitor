from __future__ import annotations

from typing import Protocol

from quotadeck.devices.aula_f108.constants import REPORT_LEN


class Transport(Protocol):
    def set_feature(self, data: bytes) -> None: ...
    def get_feature(self) -> bytes: ...
    def write_lcd_page(self, data: bytes) -> None: ...
    def read_lcd_ack(self, timeout_ms: int = 300) -> bytes: ...
    def close(self) -> None: ...


def pad64(data: bytes) -> bytes:
    if len(data) > REPORT_LEN:
        raise ValueError("feature report longer than 64 bytes")
    return data + bytes(REPORT_LEN - len(data))
