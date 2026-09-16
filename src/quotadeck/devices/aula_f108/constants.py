VID = 0x0C45
PID = 0x800A

USAGE_PAGE_CONFIG = 0xFF13
USAGE_CONFIG = 0x0001
USAGE_PAGE_LCD = 0xFF68
USAGE_LCD = 0x0061

REPORT_LEN = 64
REPORT_ID = 0x00
COMMAND_DELAY_S = 0.035
LCD_ACK_TIMEOUT_S = 0.30
APPLY_SETTLE_S = 3.0
LCD_WIDTH = 240
LCD_HEIGHT = 135
LCD_FRAME_BYTES = LCD_WIDTH * LCD_HEIGHT * 2
LCD_HEADER_BYTES = 256
LCD_PAGE_BYTES = 4096
LCD_MAX_FRAMES = 141
LCD_SOFT_CAP = 48
# 8 accounts × 5 s need ≥5 frames each (40) after the 4 ms firmware tick.
LCD_DEFAULT_BUDGET = 48
LEGACY_HIDDEN_FRAME_BUDGET = 32
LCD_IMAGE_NUMBER = 1
# Official mkimage (parsiya/f108-pro) writes delay_byte = GIF_cs / 2,
# which equals logical_ms / 20. That packing is not the LCD timer quantum.
# Connected F108 Pro playback of those bytes is 5× faster than a 20 ms
# interpretation (5 s configured → ~1 s on the LCD). 20 / 5 = 4 ms, a
# typical 250 Hz panel/USB tick. Hardware duration = delay_byte * 4 ms.
FIRMWARE_DELAY_TICK_MS = 4
# GUI, QTimer preview, and GIF89a keep millisecond durations. GIF stores
# centiseconds (10 ms). QuotaDeck quantizes logical delays to 20 ms so
# preview, GIF, and firmware bytes stay exact together (lcm of 4 and 10).
LOGICAL_DELAY_TICK_MS = 20
LCD_MAX_DELAY_MS = 255 * FIRMWARE_DELAY_TICK_MS

CMD_BEGIN = bytes([0x04, 0x18])
CMD_APPLY = bytes([0x04, 0x02])
CMD_LCD_HEADER = bytes([0x04, 0x72])
CMD_CLOCK_INIT = bytes([0x04, 0x28])
AULA_PROCESS_NAMES = ("DeviceDriver.exe", "AULA F108Pro.exe", "aula_driver.exe")
