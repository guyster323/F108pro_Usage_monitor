from __future__ import annotations

from dataclasses import dataclass

from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET, LCD_SOFT_CAP


@dataclass
class SceneBudget:
    hero_hold_ms: int = 2500
    anim_delay_ms: int = 400
    overview_hold_ms: int = 2500
    transition_ms: int = 200
    anim_frames: int = 3
    include_overview: bool = True
    include_transition: bool = False
    group_by_provider: bool = False


def allocate(
    account_count: int,
    frame_budget: int = LCD_DEFAULT_BUDGET,
    *,
    hold_ms: int | None = None,
) -> SceneBudget:
    budget = min(frame_budget, LCD_SOFT_CAP)
    include_overview = account_count >= 3
    group = account_count >= 5
    scenes = account_count if not group else min(account_count, 4)
    leftover = budget - (1 if include_overview else 0)
    anim = 1 if account_count >= 2 else 3
    if scenes * (1 + anim) > leftover:
        anim = 1
    if scenes * (1 + anim) > leftover:
        anim = 0
    dwell = 3200 if hold_ms is None else max(800, int(hold_ms))
    return SceneBudget(
        hero_hold_ms=dwell,
        anim_delay_ms=min(500, max(200, dwell // 8)),
        overview_hold_ms=dwell,
        anim_frames=anim,
        include_overview=include_overview,
        include_transition=False,
        group_by_provider=group,
    )
