from __future__ import annotations

from pathlib import Path

from quotadeck.core.models import DisplayMode, Severity, UsageSnapshot
from quotadeck.core.severity import character_state
from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET
from quotadeck.devices.aula_f108.payload import Frame
from quotadeck.renderer.budget import allocate
from quotadeck.renderer.layout import paint_account, paint_empty
from quotadeck.renderer.sprites import Theme, load_theme


def _sprite_index(
    frame_index: int,
    sprite_count: int,
    state: str = "idle",
    frame_count: int | None = None,
) -> int:
    if sprite_count <= 1:
        return 0
    if sprite_count == 2:
        # Image-generated pose pairs can differ more than a one-pixel breath.
        # Show the expression pose briefly instead of morphing twice per second.
        slot_frames = 8 if frame_count is None else frame_count
        if slot_frames <= 1:
            return 0
        expression_frame = max(1, (slot_frames - 1) // 2)
        return 1 if frame_index == expression_frame else 0
    # Ping-pong prevents a visible jump from the last pose to the first.
    cycle = list(range(sprite_count)) + list(range(sprite_count - 2, 0, -1))
    return cycle[frame_index % len(cycle)]


def render_playlist(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    theme: Theme | Path,
    *,
    mode: DisplayMode = DisplayMode.SMART,
    frame_budget: int = LCD_DEFAULT_BUDGET,
    hold_ms: int | None = None,
) -> list[Frame]:
    """Render one equal-duration full-screen slot for every selected account."""
    # A caller can construct Theme directly, so reload through the strict
    # runtime validator instead of trusting an unchecked dataclass instance.
    theme_obj = load_theme(theme.root) if isinstance(theme, Theme) else load_theme(Path(theme))
    if not snapshots:
        return [Frame(image=paint_empty(), delay_ms=500)]

    ordered = _order(snapshots, severities, mode)
    budget = allocate(len(ordered), frame_budget, hold_ms=hold_ms)
    frames: list[Frame] = []
    total = len(ordered)
    for position, snapshot in enumerate(ordered, start=1):
        severity = severities.get(snapshot.key, Severity.STALE)
        state = character_state(severity).value
        sprites = theme_obj.state_images(snapshot.provider, state)
        accent = theme_obj.accent(snapshot.provider)
        for frame_index, delay_ms in enumerate(budget.frame_delays_ms):
            sprite = sprites[
                _sprite_index(
                    frame_index,
                    len(sprites),
                    state,
                    budget.frames_per_account,
                )
            ]
            frames.append(
                Frame(
                    image=paint_account(
                        snapshot,
                        severity,
                        sprite,
                        accent,
                        position=(position, total),
                    ),
                    delay_ms=delay_ms,
                )
            )
    if len(frames) != budget.total_frames:
        raise RuntimeError(
            f"renderer produced {len(frames)} frames; budget expected {budget.total_frames}"
        )
    return frames


def _order(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    mode: DisplayMode,
) -> list[UsageSnapshot]:
    if mode == DisplayMode.FIXED:
        return list(snapshots)
    priority = {
        Severity.EXHAUSTED: 0,
        Severity.CRITICAL: 1,
        Severity.ERROR: 2,
        Severity.CAUTION: 3,
        Severity.OFFLINE: 4,
        Severity.STALE: 5,
        Severity.RESET: 6,
        Severity.BUSY: 7,
        Severity.HEALTHY: 8,
    }
    # Python's sort is stable: equal-severity accounts preserve the user's order.
    return sorted(
        snapshots,
        key=lambda item: priority.get(severities.get(item.key, Severity.STALE), 5),
    )
