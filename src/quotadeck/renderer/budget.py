from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from quotadeck.devices.aula_f108.constants import (
    LCD_DEFAULT_BUDGET,
    LCD_MAX_DELAY_MS,
    LCD_MAX_FRAMES,
    LCD_SOFT_CAP,
)

# Logical/GIF quantization only. Hardware encode uses the observed 2 ms
# playback unit; see quotadeck.devices.aula_f108.constants.
TICK_MS = 20
DEFAULT_ACCOUNT_HOLD_MS = 5000
DESIRED_FRAMES_PER_ACCOUNT = 8
IDLE_PLACEHOLDER_MS = 2000


class SceneBudgetError(ValueError):
    """Raised when every selected account cannot be represented safely."""

    def __init__(
        self,
        message: str,
        *,
        account_count: int = 0,
        hold_ms: int = 0,
        required: int = 0,
        limit: int = LCD_MAX_FRAMES,
    ) -> None:
        super().__init__(message)
        self.account_count = int(account_count)
        self.hold_ms = int(hold_ms)
        self.required = int(required)
        self.limit = int(limit)

    @property
    def hold_seconds(self) -> int:
        return max(0, int(round(self.hold_ms / 1000))) if self.hold_ms else 0


def playlist_budget_kwargs(error: SceneBudgetError) -> dict[str, object]:
    """Stable i18n kwargs for a rejected account×hold combination."""

    return {
        "count": error.account_count,
        "hold": error.hold_seconds,
        "required": error.required,
        "limit": error.limit,
    }


@dataclass(frozen=True, slots=True)
class SceneBudget:
    account_hold_ms: int
    frames_per_account: int
    frame_delays_ms: tuple[int, ...]
    total_frames: int


def _quantized_hold_ms(hold_ms: int) -> int:
    total_ticks = max(1, int(round(int(hold_ms) / TICK_MS)))
    return total_ticks * TICK_MS


def frames_per_account_for_hold(hold_ms: int) -> int:
    """Minimum frames that keep every delay on the 500 ms grid-safe max."""

    account_hold_ms = _quantized_hold_ms(hold_ms)
    return max(1, ceil(account_hold_ms / LCD_MAX_DELAY_MS))


def required_playlist_frames(
    account_count: int,
    hold_ms: int | None = None,
) -> int:
    """Frames needed so every enabled account keeps its configured hold."""

    if account_count <= 0:
        return 0
    requested = DEFAULT_ACCOUNT_HOLD_MS if hold_ms is None else int(hold_ms)
    return frames_per_account_for_hold(requested) * int(account_count)


def enabled_account_count(accounts: Any) -> int:
    """Count enabled accounts, including unconnected Cursor cards."""

    return sum(1 for item in accounts or () if getattr(item, "enabled", True))


def apply_feasible_frame_budget(config: Any) -> int:
    """Raise the hidden budget to a feasible 81–141 requirement.

    Combinations that already fit the stored budget are left unchanged so a
    3-account × 5 s default of 80 stays 80. Impossible combinations
    (required > 141) are not mutated; callers must reject them before write.
    """

    needed = required_playlist_frames(
        enabled_account_count(getattr(config, "accounts", ())),
        int(getattr(config, "scene_hold_seconds", 5)) * 1000,
    )
    stored = max(1, int(getattr(config, "frame_budget", LCD_DEFAULT_BUDGET)))
    if 0 < needed <= LCD_MAX_FRAMES and stored < needed:
        config.frame_budget = needed
    return needed


def validate_playlist_budget(config: Any) -> int:
    """Reject account×hold combinations that exceed the 141-frame hard limit."""

    count = enabled_account_count(getattr(config, "accounts", ()))
    hold_s = int(getattr(config, "scene_hold_seconds", 5))
    needed = required_playlist_frames(count, hold_s * 1000)
    if needed > LCD_MAX_FRAMES:
        raise SceneBudgetError(
            f"{count} accounts × {hold_s}s need {needed} frames; "
            f"hard limit is {LCD_MAX_FRAMES}",
            account_count=count,
            hold_ms=hold_s * 1000,
            required=needed,
            limit=LCD_MAX_FRAMES,
        )
    return needed


def _distribute_ticks(total_ticks: int, frame_count: int) -> tuple[int, ...]:
    """Partition firmware ticks exactly; never introduce rounding drift."""
    base, extra = divmod(total_ticks, frame_count)
    ticks = [base] * frame_count
    # Put the longer frames at the end so the first visual response is prompt.
    for index in range(frame_count - extra, frame_count):
        if extra:
            ticks[index] += 1
    return tuple(value * TICK_MS for value in ticks)


def idle_placeholder_delays(total_ms: int = IDLE_PLACEHOLDER_MS) -> tuple[int, ...]:
    """Split a Preview idle hold into exact, firmware-encodable frame delays."""
    total_ticks = max(1, int(round(int(total_ms) / TICK_MS)))
    hold_ms = total_ticks * TICK_MS
    frame_count = max(1, ceil(hold_ms / LCD_MAX_DELAY_MS))
    delays = _distribute_ticks(total_ticks, frame_count)
    if any(delay > LCD_MAX_DELAY_MS for delay in delays):
        raise SceneBudgetError(
            f"idle placeholder delay exceeds the {LCD_MAX_DELAY_MS} ms firmware limit",
            required=frame_count,
            hold_ms=hold_ms,
            limit=LCD_MAX_FRAMES,
        )
    return delays


def allocate(
    account_count: int,
    frame_budget: int = LCD_DEFAULT_BUDGET,
    *,
    hold_ms: int | None = None,
) -> SceneBudget:
    """Allocate equal, exact-duration full-screen slots to every account.

    A stored budget below the feasible requirement is raised up to the 141
    hard limit (8×6 s → 96, 9×5 s → 90, 3×20 s → 120). Only combinations
    that need more than 141 frames fail closed.
    """
    requested_hold = DEFAULT_ACCOUNT_HOLD_MS if hold_ms is None else int(hold_ms)
    account_hold_ms = _quantized_hold_ms(requested_hold)
    if account_count <= 0:
        return SceneBudget(account_hold_ms, 0, (), 0)

    needed = required_playlist_frames(account_count, account_hold_ms)
    if needed > LCD_MAX_FRAMES:
        raise SceneBudgetError(
            f"frame budget cannot show {account_count} accounts for "
            f"{account_hold_ms} ms each; at least {needed} frames are required "
            f"(hard limit {LCD_MAX_FRAMES})",
            account_count=account_count,
            hold_ms=account_hold_ms,
            required=needed,
            limit=LCD_MAX_FRAMES,
        )

    budget = min(
        LCD_SOFT_CAP,
        LCD_MAX_FRAMES,
        max(max(1, int(frame_budget)), needed),
    )
    per_account_capacity = budget // account_count
    minimum_frames = frames_per_account_for_hold(account_hold_ms)
    frames_per_account = min(
        DESIRED_FRAMES_PER_ACCOUNT,
        per_account_capacity,
        max(1, account_hold_ms // TICK_MS),
    )
    frames_per_account = max(minimum_frames, frames_per_account)
    delays = _distribute_ticks(account_hold_ms // TICK_MS, frames_per_account)
    if any(delay > LCD_MAX_DELAY_MS for delay in delays):
        raise SceneBudgetError(
            f"a generated frame delay exceeds the {LCD_MAX_DELAY_MS} ms firmware limit",
            account_count=account_count,
            hold_ms=account_hold_ms,
            required=needed,
            limit=LCD_MAX_FRAMES,
        )
    return SceneBudget(
        account_hold_ms=account_hold_ms,
        frames_per_account=frames_per_account,
        frame_delays_ms=delays,
        total_frames=frames_per_account * account_count,
    )
