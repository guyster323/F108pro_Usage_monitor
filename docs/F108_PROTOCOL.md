# AULA F108 Pro LCD Protocol

Verified against [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT).

## Device

- USB VID `0x0C45` / PID `0x800A`
- Interface 3, usage page `0xFF13`: 64-byte Feature reports (control)
- Interface 2, usage page `0xFF68`: 4096-byte Output + 64-byte Input (LCD pages)
- USB-C wired mode only (`Fn+4`). Wireless (`7F 03`) is unsupported.
## Feature reports (Windows)

Send 65 bytes (`0x00` report ID + 64 payload) via `HidD_SetFeature`.
Read back with `HidD_GetFeature`. Delay 35 ms. ACK is `byte[3] == 0x01`.

Never send LCD pages as SET_REPORT control transfers — that crashes firmware.
## LCD upload

1. `04 18` begin (readback)
2. `04 72` header (readback): `byte[2]=image_number` (**1**, the custom GIF slot), `byte[8..9]=page_count` LE. Slot 0 is the factory GIF and must not be used.
3. N × 4096-byte Output pages; ACK `01 5A 02 00 …` (300 ms timeout)
4. `04 02` apply (readback). Device writes SPI flash (~3 s).
## Payload

- 256-byte header: `byte[0]=frame_count`, `byte[1+i]=delay_ms/2`, rest `0xFF`. Every delay must already be an exact 2ms tick in the 2–510ms range. The 2ms unit is based on hardware timing calibration; the older reverse-engineering note that described this field as 20ms was off by 10×.
- Each frame: 240×135 RGB565 little-endian (64,800 bytes)
- Pad to a multiple of 4096 with `0xFF`
- Hard maximum **141 frames**. Firmware does not bound-check; overflow corrupts menu graphics.
- Before the first HID command, QuotaDeck snapshots mutable input and revalidates the immutable raw payload: frame count 1–141, non-zero delay bytes, 4096-byte alignment, and the exact padded length implied by the declared frame count.

## Clock

`04 18` → `04 28` (`byte[8]=0x01`) → `00 01 5A yy mm dd HH MM SS 00 wday … AA 55` → `04 02`.
