from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from quotadeck.core.models import DisplayMode, Severity, UsageSnapshot, UsageWindow
from quotadeck.core.scheduler import render_hash
from quotadeck.core.severity import snapshot_severity
from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.devices.aula_f108.payload import Frame, PayloadError, delay_byte
from quotadeck.gifio import load_gif
from quotadeck.renderer.budget import SceneBudgetError, allocate
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
    assert sum(frame.delay_ms for frame in load_gif(out)) == sum(
        frame.delay_ms for frame in frames
    )


def test_gif_writer_rejects_off_tick_timing(tmp_path: Path) -> None:
    import pytest
    from PIL import Image

    invalid = Frame(Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT)), delay_ms=30)
    with pytest.raises(PayloadError, match="exact 20 ms tick"):
        write_gif([invalid], tmp_path / "invalid.gif")

def test_budget_is_equal_for_many_accounts() -> None:
    many = allocate(8, 32)
    assert many.frames_per_account == 4
    assert many.total_frames == 32
    assert sum(many.frame_delays_ms) == 5000


def test_static_character_states_use_a_brief_expression_pose() -> None:
    from quotadeck.renderer.scenes import _sprite_index

    assert [_sprite_index(i, 2, "idle") for i in range(8)] == [0, 0, 0, 1, 0, 0, 0, 0]
    assert [_sprite_index(i, 2, "busy") for i in range(4)] == [0, 1, 0, 0]
    assert [_sprite_index(i, 2, "idle", 2) for i in range(2)] == [0, 1]
    assert [_sprite_index(i, 2, "idle", 3) for i in range(3)] == [0, 1, 0]


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


def test_full_bar_hud_keeps_character_left_and_quota_right() -> None:
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


def test_header_status_uses_distinct_shape_cues() -> None:
    from PIL import Image

    from quotadeck.renderer.canvas import severity_color
    from quotadeck.renderer.layout import paint_account

    snap = _snapshots()[0]
    blank = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    patterns = []
    for severity in (
        Severity.HEALTHY,
        Severity.BUSY,
        Severity.CAUTION,
        Severity.CRITICAL,
        Severity.EXHAUSTED,
        Severity.ERROR,
        Severity.OFFLINE,
        Severity.STALE,
        Severity.RESET,
    ):
        image = paint_account(snap, severity, blank, "#19D79C")
        color = severity_color(severity.value)
        patterns.append(
            frozenset(
                (x, y)
                for y in range(4, 16)
                for x in range(226, 237)
                if image.getpixel((x, y)) == color
            )
        )
    assert len(set(patterns)) == len(patterns)


def test_header_alias_does_not_overlap_double_digit_index() -> None:
    from PIL import Image

    from quotadeck.renderer.canvas import MUTED, pixel_text_width
    from quotadeck.renderer.layout import paint_account

    snap = _snapshots()[0]
    snap.display_name = "A_VERY+LONG@ACCOUNT"
    image = paint_account(
        snap,
        Severity.HEALTHY,
        Image.new("RGBA", (88, 108), (0, 0, 0, 0)),
        "#19D79C",
        position=(10, 10),
    )
    index_x = 220 - pixel_text_width("10/10")
    assert not any(
        image.getpixel((x, y)) == MUTED
        for y in range(4, 18)
        for x in range(index_x - 5, 221)
    )


def test_middle_ellipsis_preserves_unique_account_suffix() -> None:
    from PIL import Image

    from quotadeck.renderer.layout import paint_account

    snap_a = _snapshots()[0]
    snap_b = _snapshots()[0]
    snap_a.display_name = "ACCOUNT_01"
    snap_b.display_name = "ACCOUNT_02"
    blank = Image.new("RGBA", (88, 108), (0, 0, 0, 0))
    image_a = paint_account(snap_a, Severity.HEALTHY, blank, "#19D79C", position=(10, 10))
    image_b = paint_account(snap_b, Severity.HEALTHY, blank, "#19D79C", position=(10, 10))
    assert image_a.crop((0, 0, 240, 20)).tobytes() != image_b.crop((0, 0, 240, 20)).tobytes()


def test_supported_alias_punctuation_has_distinct_glyphs() -> None:
    from PIL import Image

    from quotadeck.renderer.canvas import draw_pixel_text

    rendered = []
    for alias in ("JOHN+WORK", "JOHN_WORK", "JOHN@WORK"):
        image = Image.new("L", (120, 16), 0)
        draw_pixel_text(image, (0, 0), alias, fill=255, scale=2)
        rendered.append(image.tobytes())
    assert len(set(rendered)) == len(rendered)


def test_unsupported_aliases_get_stable_distinct_identifiers() -> None:
    from quotadeck.renderer.canvas import pixel_identifier

    aliases = ("A#B", "A!B", "개인", "업무", "A'B", "A:B")
    identifiers = [pixel_identifier(alias) for alias in aliases]
    assert len(set(identifiers)) == len(identifiers)
    assert all(len(identifier) <= 8 for identifier in identifiers)
    assert pixel_identifier("개인") == pixel_identifier("개인")

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


def test_third_window_with_lowest_quota_is_never_hidden() -> None:
    from quotadeck.core.models import display_windows

    snap = _snapshots()[0]
    snap.windows = [
        UsageWindow("primary", "5H", 10, 90),
        UsageWindow("weekly", "WEEK", 10, 90),
        UsageWindow("opus", "OPUS", 100, 0),
        UsageWindow("sonnet", "SONNET", 40, 60),
    ]
    visible = display_windows(snap)
    assert [window.id for window in visible] == ["primary", "opus"]
    assert snapshot_severity(snap) == Severity.EXHAUSTED


def test_non_finite_window_does_not_crash_renderer() -> None:
    from quotadeck.renderer.layout import paint_account

    snap = _snapshots()[0]
    snap.windows[0].remaining_percent = float("nan")
    snap.windows[1].remaining_percent = float("inf")
    assert snap.critical_remaining is None
    assert snapshot_severity(snap) == Severity.STALE
    image = paint_account(
        snap,
        Severity.STALE,
        load_theme(THEME).state_images("codex", "stale")[0],
        "#19D79C",
    )
    assert image.size == (LCD_WIDTH, LCD_HEIGHT)

def test_empty_playlist_renders_idle_card() -> None:
    from quotadeck.renderer.layout import paint_empty
    from quotadeck.renderer.scenes import render_playlist
    from quotadeck.renderer.sprites import load_theme

    frames = render_playlist([], {}, load_theme(THEME), frame_budget=8)
    assert len(frames) == 1
    assert frames[0].image.size == (LCD_WIDTH, LCD_HEIGHT)
    empty = paint_empty()
    assert empty.size == (LCD_WIDTH, LCD_HEIGHT)


def test_unchecked_theme_object_cannot_bypass_runtime_validation(tmp_path: Path) -> None:
    import pytest

    from quotadeck.renderer.sprites import Theme, ThemeError

    unchecked = Theme("bad", "Bad", tmp_path / "missing", 4, {}, {})
    with pytest.raises(ThemeError):
        render_playlist(_snapshots()[:1], {}, unchecked, frame_budget=8)

def test_scene_hold_splits_long_delay() -> None:
    snaps = _snapshots()[:1]
    sevs = {s.key: snapshot_severity(s) for s in snaps}
    frames = render_playlist(snaps, sevs, load_theme(THEME), frame_budget=16, hold_ms=8000)
    holds = [frame.delay_ms for frame in frames]
    assert sum(delay_byte(value) * 20 for value in holds) == 8000
    assert max(holds) <= 5100


def test_scene_hold_uses_two_frames_at_exact_delay_limit() -> None:
    budget = allocate(1, 2, hold_ms=10200)
    assert budget.frame_delays_ms == (5100, 5100)

def test_allocate_uses_requested_hold() -> None:
    budget = allocate(2, 32, hold_ms=7000)
    assert budget.account_hold_ms == 7000
    assert sum(budget.frame_delays_ms) == 7000


def test_every_account_gets_exactly_five_seconds() -> None:
    snaps = _snapshots()
    sevs = {s.key: snapshot_severity(s) for s in snaps}
    frames = render_playlist(snaps, sevs, load_theme(THEME), frame_budget=32, hold_ms=5000)
    budget = allocate(len(snaps), 32, hold_ms=5000)
    assert len(frames) == len(snaps) * budget.frames_per_account
    for index in range(len(snaps)):
        start = index * budget.frames_per_account
        account_frames = frames[start : start + budget.frames_per_account]
        assert sum(delay_byte(frame.delay_ms) * 20 for frame in account_frames) == 5000


def test_smart_order_has_no_duplicate_accounts() -> None:
    from quotadeck.core.models import DisplayMode, Severity
    from quotadeck.renderer.scenes import _order

    snaps = _snapshots()
    sevs = {snap.key: Severity.HEALTHY for snap in snaps}
    sevs[snaps[-1].key] = Severity.CRITICAL
    ordered = _order(snaps, sevs, DisplayMode.SMART)
    assert ordered[0].key == snaps[-1].key
    assert sorted(item.key for item in ordered) == sorted(item.key for item in snaps)
    assert len({item.key for item in ordered}) == len(snaps)


def test_budget_never_silently_drops_an_account() -> None:
    import pytest

    with pytest.raises(SceneBudgetError, match="at least"):
        allocate(9, 8, hold_ms=5000)


def test_many_same_provider_accounts_get_full_screen_slots(monkeypatch) -> None:
    from dataclasses import replace

    from PIL import Image

    import quotadeck.renderer.scenes as scenes

    template = _snapshots()[0]
    snaps = [
        replace(template, account_id=f"account-{index}", display_name=f"A{index}")
        for index in range(6)
    ]
    markers = {snap.key: (20 + index * 30, 10, 10) for index, snap in enumerate(snaps)}

    def marker_card(snapshot, _severity, _sprite, _accent, *, position=None):
        assert position is not None
        return Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), markers[snapshot.key])

    monkeypatch.setattr(scenes, "paint_account", marker_card)
    severities = {snap.key: Severity.HEALTHY for snap in snaps}
    frames = scenes.render_playlist(
        snaps,
        severities,
        load_theme(THEME),
        mode=DisplayMode.FIXED,
        frame_budget=30,
        hold_ms=5000,
    )
    per_account = allocate(6, 30, hold_ms=5000).frames_per_account
    assert len(frames) == 6 * per_account
    assert [frames[i * per_account].image.getpixel((0, 0)) for i in range(6)] == [
        markers[snap.key] for snap in snaps
    ]


def test_render_hash_quantizes() -> None:
    snaps = _snapshots()
    sevs = {s.key: snapshot_severity(s) for s in snaps}
    a = render_hash(snaps, sevs, theme="crew", mode="smart")
    snaps[0].windows[0].remaining_percent -= 2
    b = render_hash(snaps, sevs, theme="crew", mode="smart")
    assert a == b


def test_render_hash_tracks_each_visible_quota_bar() -> None:
    snaps = _snapshots()
    snap = snaps[0]
    snap.windows[0].remaining_percent = 90
    snap.windows[1].remaining_percent = 50
    sevs = {item.key: snapshot_severity(item) for item in snaps}
    before = render_hash(snaps, sevs, theme="crew", mode="smart")
    snap.windows[0].remaining_percent = 70
    after = render_hash(snaps, sevs, theme="crew", mode="smart")
    assert before != after
