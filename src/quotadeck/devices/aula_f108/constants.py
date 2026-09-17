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
# Default hidden budget covers 8 accounts × 5 s (10 frames × 500 ms).
# Soft cap equals the firmware hard limit so 81–141 frame combos can persist.
LCD_SOFT_CAP = 141
LCD_DEFAULT_BUDGET = 80
LEGACY_HIDDEN_FRAME_BUDGET = 32
V7_DEFAULT_FRAME_BUDGET = 48
LCD_IMAGE_NUMBER = 1
# Official mkimage (parsiya/f108-pro) writes delay_byte = GIF_cs / 2
# (logical_ms / 20). That is a packing formula, not a measured timer.
# On this user's connected F108 Pro, the previous /20 packing played a
# configured 5 s slot in about 1 s, and the later /4 packing played the
# same slot in about 2.5 s. QuotaDeck therefore encodes with an observed
# 2 ms playback unit (twice the /4 byte) so a 5 s setting targets 5 s.
# This is a device-observation calibration, not a general HID/firmware
# specification. Stopwatch confirmation after flashing is still required.
FIRMWARE_DELAY_TICK_MS = 2
# GUI, QTimer preview, and GIF89a keep millisecond durations. GIF stores
# centiseconds (10 ms). Logical delays stay on a 20 ms grid so Preview/GIF
# remain exact; 20 ms is an integer number of 2 ms observed units.
LOGICAL_DELAY_TICK_MS = 20
# 255 × 2 ms = 510 ms, which is not on the 20 ms logical grid.
LCD_MAX_DELAY_MS = (
    (255 * FIRMWARE_DELAY_TICK_MS) // LOGICAL_DELAY_TICK_MS
) * LOGICAL_DELAY_TICK_MS

CMD_BEGIN = bytes([0x04, 0x18])
CMD_APPLY = bytes([0x04, 0x02])
CMD_LCD_HEADER = bytes([0x04, 0x72])
CMD_CLOCK_INIT = bytes([0x04, 0x28])
AULA_PROCESS_NAMES = ("DeviceDriver.exe", "AULA F108Pro.exe", "aula_driver.exe")
