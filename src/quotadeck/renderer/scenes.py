from __future__ import annotations

from pathlib import Path

from quotadeck.core.models import DisplayMode, Severity, UsageSnapshot
from quotadeck.core.severity import character_state
from quotadeck.devices.aula_f108.payload import Frame
from quotadeck.renderer.budget import SceneBudget, allocate
from quotadeck.renderer.layout import paint_account, paint_empty, paint_overview, paint_transition
from quotadeck.renderer.sprites import Theme, load_theme


def render_playlist(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    theme: Theme | Path,
    *,
    mode: DisplayMode = DisplayMode.SMART,
    frame_budget: int = 32,
) -> list[Frame]:
    theme_obj = theme if isinstance(theme, Theme) else load_theme(Path(theme))
    if not snapshots:
        return [Frame(image=paint_empty(), delay_ms=2000)]
    ordered = _order(snapshots, severities, mode)
    budget = allocate(len(snapshots), frame_budget)
    if budget.group_by_provider:
        return _render_grouped(snapshots, severities, theme_obj, budget, frame_budget)
    frames: list[Frame] = []
    previous = None
    for snapshot in ordered:
        severity = severities.get(snapshot.key, Severity.STALE)
        state = character_state(severity).value
        sprites = theme_obj.state_images(snapshot.provider, state)
        accent = theme_obj.accent(snapshot.provider)
        hero = paint_account(snapshot, severity, sprites[0], accent)
        if previous is not None and budget.include_transition:
            frames.append(Frame(image=paint_transition(previous, hero), delay_ms=budget.transition_ms))
        frames.append(Frame(image=hero, delay_ms=budget.hero_hold_ms))
        for i in range(budget.anim_frames):
            sprite = sprites[(i + 1) % len(sprites)]
            frames.append(
                Frame(
                    image=paint_account(snapshot, severity, sprite, accent),
                    delay_ms=budget.anim_delay_ms,
                )
            )
        previous = hero
    if budget.include_overview and ordered:
        rows = [(item, severities.get(item.key, Severity.STALE)) for item in ordered]
        frames.append(Frame(image=paint_overview(rows, theme_obj.accent("codex")), delay_ms=budget.overview_hold_ms))
    return frames[:frame_budget]


def _render_grouped(snapshots, severities, theme_obj, budget, frame_budget) -> list[Frame]:
    from collections import defaultdict

    from quotadeck.renderer.layout import paint_overview

    groups: dict[str, list] = defaultdict(list)
    for snap in snapshots:
        groups[snap.provider].append(snap)
    frames: list[Frame] = []
    for provider, members in groups.items():
        rows = [(item, severities.get(item.key, Severity.STALE)) for item in members]
        hero = members[0]
        state = character_state(severities.get(hero.key, Severity.STALE)).value
        sprite = theme_obj.state_images(provider, state)[0]
        accent = theme_obj.accent(provider)
        frames.append(Frame(image=paint_account(hero, severities.get(hero.key, Severity.STALE), sprite, accent), delay_ms=budget.hero_hold_ms))
        frames.append(Frame(image=paint_overview(rows, accent), delay_ms=budget.overview_hold_ms))
    return frames[:frame_budget]


def _order(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    mode: DisplayMode,
) -> list[UsageSnapshot]:
    if mode == DisplayMode.FIXED:
        return list(snapshots)
    hot = {Severity.CRITICAL, Severity.CAUTION, Severity.EXHAUSTED}
    result: list[UsageSnapshot] = []
    for item in snapshots:
        result.append(item)
        if severities.get(item.key) in hot:
            result.append(item)
    # de-dupe adjacent extras but keep double slot for hot accounts
    return result
