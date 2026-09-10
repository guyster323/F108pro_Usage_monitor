from __future__ import annotations

from pathlib import Path

from quotadeck.cli import _snapshots_from_fixture
from quotadeck.config import default_theme_dir
from quotadeck.core.models import DisplayMode
from quotadeck.core.severity import snapshot_severity
from quotadeck.devices.aula_f108.payload import image_to_rgb565, rgb565_to_image
from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.scenes import render_playlist
from quotadeck.renderer.sprites import load_theme

ROOT = Path(__file__).resolve().parents[1]
OUT_GIF = ROOT / "docs" / "hero-preview.gif"
OUT_PNG = ROOT / "docs" / "hud-preview.png"

def main() -> None:
    OUT_GIF.parent.mkdir(parents=True, exist_ok=True)
    snapshots = _snapshots_from_fixture(ROOT / "tests" / "fixtures" / "usage.json")
    severities = {snapshot.key: snapshot_severity(snapshot) for snapshot in snapshots}
    frames = render_playlist(
        snapshots,
        severities,
        load_theme(default_theme_dir()),
        mode=DisplayMode.FIXED,
        frame_budget=LCD_DEFAULT_BUDGET,
        hold_ms=5000,
    )
    write_gif(frames, OUT_GIF)
    hardware_preview = rgb565_to_image(image_to_rgb565(frames[0].image))
    hardware_preview.save(OUT_PNG)
    print(
        f"wrote {OUT_GIF} ({len(frames)} frames) and RGB565 preview "
        f"{OUT_PNG} size={hardware_preview.size}"
    )


if __name__ == "__main__":
    main()
