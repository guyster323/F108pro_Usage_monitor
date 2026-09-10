from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from quotadeck.devices.aula_f108.constants import (
    LCD_DEFAULT_BUDGET,
    LCD_DELAY_TICK_MS,
    LCD_MAX_DELAY_MS,
    LCD_MAX_FRAMES,
    LCD_SOFT_CAP,
)

TICK_MS = LCD_DELAY_TICK_MS
DEFAULT_ACCOUNT_HOLD_MS = 5000
DESIRED_FRAMES_PER_ACCOUNT = 8


class SceneBudgetError(ValueError):
    """Raised when every selected account cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class SceneBudget:
    account_hold_ms: int
    frames_per_account: int
    frame_delays_ms: tuple[int, ...]
    total_frames: int


def _distribute_ticks(total_ticks: int, frame_count: int) -> tuple[int, ...]:
    """Partition firmware ticks exactly; never introduce rounding drift."""
    base, extra = divmod(total_ticks, frame_count)
    ticks = [base] * frame_count
    # Put the longer frames at the end so the first visual response is prompt.
    for index in range(frame_count - extra, frame_count):
        if extra:
            ticks[index] += 1
    return tuple(value * TICK_MS for value in ticks)


def allocate(
    account_count: int,
    frame_budget: int = LCD_DEFAULT_BUDGET,
    *,
    hold_ms: int | None = None,
) -> SceneBudget:
    """Allocate equal, exact-duration full-screen slots to every account."""
    requested_hold = DEFAULT_ACCOUNT_HOLD_MS if hold_ms is None else int(hold_ms)
    total_ticks = max(1, int(round(requested_hold / TICK_MS)))
    account_hold_ms = total_ticks * TICK_MS
    if account_count <= 0:
        return SceneBudget(account_hold_ms, 0, (), 0)

    budget = min(max(1, int(frame_budget)), LCD_SOFT_CAP, LCD_MAX_FRAMES)
    per_account_capacity = budget // account_count
    minimum_frames = ceil(account_hold_ms / LCD_MAX_DELAY_MS)
    if per_account_capacity < minimum_frames:
        required = minimum_frames * account_count
        raise SceneBudgetError(
            f"frame budget {budget} cannot show {account_count} accounts for "
            f"{account_hold_ms} ms each; at least {required} frames are required"
        )

    frames_per_account = min(
        DESIRED_FRAMES_PER_ACCOUNT,
        per_account_capacity,
        total_ticks,
    )
    frames_per_account = max(minimum_frames, frames_per_account)
    delays = _distribute_ticks(total_ticks, frames_per_account)
    if any(delay > LCD_MAX_DELAY_MS for delay in delays):
        raise SceneBudgetError("a generated frame delay exceeds the 510 ms firmware limit")
    return SceneBudget(
        account_hold_ms=account_hold_ms,
        frames_per_account=frames_per_account,
        frame_delays_ms=delays,
        total_frames=frames_per_account * account_count,
    )
