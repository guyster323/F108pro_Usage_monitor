from __future__ import annotations

from pathlib import Path

from quotadeck.devices.aula_f108.payload import Frame, validate_frames

def write_gif(frames: list[Frame], path: Path) -> Path:
    # Keep preview/export on the logical 20 ms / GIF-centisecond grid. Firmware
    # delay bytes use a separate 4 ms tick; validate_frames enforces both.
    validate_frames(frames)
    images = [frame.image.convert("P", palette=1, colors=64) for frame in frames]
    durations = [frame.delay_ms for frame in frames]
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    return path

def write_png(frame: Frame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(path)
    return path
