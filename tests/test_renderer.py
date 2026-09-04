from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.core.scheduler import render_hash
from quotadeck.core.severity import snapshot_severity
from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.renderer.budget import allocate
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.scenes import render_playlist
from quotadeck.renderer.sprites import load_theme, validate_theme

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "themes" / "quotadeck-crew"
FIXTURE = Path(__file__).parent / "fixtures" / "usage.json"


def _snapshots() -> list[UsageSnapshot]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    snaps = []
    for item in raw["accounts"]:
        windows = [
            UsageWindow(
                id=w["id"],
                label=w["label"],
                used_percent=w["usedPercent"],
                remaining_percent=w["remainingPercent"],
                resets_at=now,
            )
            for w in item["windows"]
        ]
        snaps.append(
            UsageSnapshot(
                provider=item["provider"],
                account_id=item["accountId"],
                display_name=item["displayName"],
                plan=item.get("plan"),
                windows=windows,
                status="ok",
                fetched_at=now,
            )
        )
    return snaps


def test_theme_valid() -> None:
    assert THEME.joinpath("theme.json").is_file()
    assert validate_theme(THEME) == []


def test_render_stays_in_budget(tmp_path: Path) -> None:
    snaps = _snapshots()
    theme = load_theme(THEME)
    sevs = {s.key: snapshot_severity(s) for s in snaps}
    frames = render_playlist(snaps, sevs, theme, frame_budget=32)
    assert frames
    assert len(frames) <= 32
    for frame in frames:
        assert frame.image.size == (LCD_WIDTH, LCD_HEIGHT)
    out = tmp_path / "preview.gif"
    write_gif(frames, out)
    assert out.is_file()


def test_budget_shrinks_for_many_accounts() -> None:
    many = allocate(8, 32)
    assert many.anim_frames <= 1 or many.group_by_provider


def test_pixel_number_is_arm_length_size() -> None:
    from quotadeck.renderer.canvas import pixel_number_width

    assert pixel_number_width("72%", scale=5) >= 50
    assert pixel_number_width("4%", scale=5) >= 30


def test_reset_parts_use_weekday_and_local_time() -> None:
    from quotadeck.renderer.layout import reset_parts

    snaps = _snapshots()
    parts = reset_parts(snaps[0])
    assert parts is not None
    day, clock = parts
    assert day in {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
    assert ":" in clock
    assert len(clock) == 5


def test_hybrid_hud_keeps_character_left_and_reset_panel() -> None:
    from quotadeck.core.severity import snapshot_severity
    from quotadeck.renderer.layout import CHAR_BAY, RATE_BAY, paint_account
    from quotadeck.renderer.sprites import load_theme

    snap = _snapshots()[0]
    theme = load_theme(THEME)
    sprite = theme.state_images("codex", "idle")[0]
    image = paint_account(snap, snapshot_severity(snap), sprite, theme.accent("codex"))
    assert image.size == (LCD_WIDTH, LCD_HEIGHT)
    assert CHAR_BAY[2] < RATE_BAY[0]
    left = image.getpixel((47, 60))
    right_number = image.getpixel((210, 36))
    assert left != (7, 12, 20)
    assert right_number != (7, 12, 20)


def test_oversized_sprite_stays_out_of_rate_bay() -> None:
    from PIL import Image

    from quotadeck.core.severity import snapshot_severity
    from quotadeck.renderer.layout import RATE_BAY, paint_account

    marker = (255, 0, 255, 255)
    sprite = Image.new("RGBA", (120, 120), marker)
    snap = _snapshots()[0]
    image = paint_account(snap, snapshot_severity(snap), sprite, "#00F5B4")
    x0, y0, x1, y1 = RATE_BAY
    leaked = 0
    for x in range(x0, x1 + 1, 4):
        for y in range(y0, y1 + 1, 4):
            if image.getpixel((x, y))[:3] == marker[:3]:
                leaked += 1
    assert leaked == 0


def test_empty_playlist_renders_idle_card() -> None:
    from quotadeck.renderer.layout import paint_empty
    from quotadeck.renderer.scenes import render_playlist
    from quotadeck.renderer.sprites import load_theme

    frames = render_playlist([], {}, load_theme(THEME), frame_budget=8)
    assert len(frames) == 1
    assert frames[0].image.size == (LCD_WIDTH, LCD_HEIGHT)
    empty = paint_empty()
    assert empty.size == (LCD_WIDTH, LCD_HEIGHT)


def test_render_hash_quantizes() -> None:
    snaps = _snapshots()
    sevs = {s.key: snapshot_severity(s) for s in snaps}
    a = render_hash(snaps, sevs, theme="crew", mode="smart")
    snaps[0].windows[0].remaining_percent -= 2
    b = render_hash(snaps, sevs, theme="crew", mode="smart")
    assert a == b
