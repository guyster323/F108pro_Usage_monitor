from __future__ import annotations

from pathlib import Path

from quotadeck.devices.aula_f108.payload import Frame


def write_gif(frames: list[Frame], path: Path) -> Path:
    if not frames:
        raise ValueError("no frames")
    images = [frame.image.convert("P", palette=1, colors=64) for frame in frames]
    durations = [max(20, frame.delay_ms) for frame in frames]
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
