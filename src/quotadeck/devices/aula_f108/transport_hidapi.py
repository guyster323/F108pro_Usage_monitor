from __future__ import annotations

from quotadeck.devices.aula_f108.constants import (
    LCD_ACK_TIMEOUT_S,
    LCD_PAGE_BYTES,
    PID,
    REPORT_ID,
    REPORT_LEN,
    USAGE_PAGE_CONFIG,
    USAGE_PAGE_LCD,
    VID,
)
from quotadeck.devices.aula_f108.transport import pad64


class HidapiError(RuntimeError):
    pass


def _open_usage(usage_page: int):
    try:
        import hid
    except ImportError as exc:
        raise HidapiError("hidapi is not installed") from exc
    for info in hid.enumerate(VID, PID):
        if info.get("usage_page") == usage_page:
            device = hid.device()
            path = info.get("path")
            if path:
                device.open_path(path)
            else:
                device.open(VID, PID)
            return device
    return None

class HidapiTransport:
    def __init__(self) -> None:
        self._feature = _open_usage(USAGE_PAGE_CONFIG)
        if self._feature is None:
            raise HidapiError(
                "F108 config interface not found (VID 0x0C45 PID 0x800A usage 0xFF13). "
                "Switch to USB-C mode with Fn+4 and close official AULA software."
            )
        self._lcd = _open_usage(USAGE_PAGE_LCD)
        if self._lcd is None:
            raise HidapiError(
                "F108 LCD interface not found (usage 0xFF68). Use USB-C mode (Fn+4), not Bluetooth/2.4G."
            )
        self._feature.set_nonblocking(False)
        self._lcd.set_nonblocking(False)
    def set_feature(self, data: bytes) -> None:
        packet = bytes([REPORT_ID]) + pad64(data)
        written = self._feature.send_feature_report(packet)
        if written < 0:
            raise HidapiError("HidD_SetFeature failed")
    def get_feature(self) -> bytes:
        raw = self._feature.get_feature_report(REPORT_ID, REPORT_LEN + 1)
        if not raw:
            raise HidapiError("HidD_GetFeature failed")
        payload = bytes(raw)
        if len(payload) == REPORT_LEN + 1:
            return payload[1:]
        return payload[:REPORT_LEN].ljust(REPORT_LEN, b"\x00")
    def write_lcd_page(self, data: bytes) -> None:
        if len(data) != LCD_PAGE_BYTES:
            raise HidapiError(f"LCD page must be {LCD_PAGE_BYTES} bytes")
        # Interrupt OUT via WriteFile. Never use SET_REPORT on this interface.
        written = self._lcd.write(bytes([REPORT_ID]) + data)
        if written < 0:
            raise HidapiError("LCD WriteFile failed")
        if written not in {LCD_PAGE_BYTES, LCD_PAGE_BYTES + 1}:
            raise HidapiError(f"LCD WriteFile short write ({written} bytes)")
    def read_lcd_ack(self, timeout_ms: int = 300) -> bytes:
        timeout = timeout_ms if timeout_ms else int(LCD_ACK_TIMEOUT_S * 1000)
        raw = self._lcd.read(REPORT_LEN, timeout)
        if not raw:
            raise HidapiError("LCD ACK timeout")
        return bytes(raw)[:REPORT_LEN].ljust(REPORT_LEN, b"\x00")
    def close(self) -> None:
        for handle in (self._lcd, self._feature):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                pass
        self._lcd = None
        self._feature = None
