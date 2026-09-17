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

- 256-byte header: `byte[0]=frame_count`, `byte[1+i]=logical_ms/2`, rest `0xFF`.
- Official `parsiya/f108-pro` mkimage writes `GIF_centiseconds / 2`, which equals `logical_ms / 20`. That is a GIF packing formula, not a measured LCD timer. There is no independent playback measurement of that packing in this repository.
- On this user's connected F108 Pro, `/20` packing of a configured 5 s slot played in about 1 s, and the later `/4` packing played in about 2.5 s. QuotaDeck therefore encodes with an **observed 2 ms playback unit** (`delay_byte = logical_ms / 2`). This is a device-observation calibration, not a general HID/firmware specification, and still needs a stopwatch re-check after flashing.
- Hardware-oriented duration = `delay_byte × 2 ms`. The 1-byte max is 255, so 255 × 2 = 510 ms; logical delays stay on the 20 ms grid, so the per-frame max is **500 ms**.
- GUI preview and GIF export keep the configured millisecond durations on an exact **20 ms** logical grid. Encode is the only path that applies the 2 ms observation.
- Every logical delay must already be an exact 20 ms tick in the 20–500 ms range. Never exceed **141 frames**; slot 0 is the factory GIF.
- Empty quota playlists and all-hidden cumulative playlists use a repeated idle card whose Preview total stays **2,000 ms**. A single 2,000 ms frame cannot encode (max 500 ms), so the renderer emits four 500 ms frames. Preview milliseconds and observed `delay_byte × 2` both sum to 2,000 ms.
- Each frame: 240×135 RGB565 little-endian (64,800 bytes)
- Pad to a multiple of 4096 with `0xFF`
- Hard maximum **141 frames**. Firmware does not bound-check; overflow corrupts menu graphics. The hidden default budget is 80 frames (8×5 s). Feasible 81–141 combinations raise that hidden budget before save; combinations over 141 are rejected before the file is written. Padded payload size grows with frame count (80≈5.2MB, 96≈6.2MB, 141≈9.1MB), so longer holds make HID upload and flash writes slower.
- Before the first HID command, QuotaDeck snapshots mutable input and revalidates the immutable raw payload: frame count 1–141, non-zero delay bytes, 4096-byte alignment, and the exact padded length implied by the declared frame count.

## Clock

`04 18` → `04 28` (`byte[8]=0x01`) → `00 01 5A yy mm dd HH MM SS 00 wday … AA 55` → `04 02`.
