from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

from quotadeck.core.models import CharacterState, Severity, UsageSnapshot

_BANDS: list[tuple[float, Severity]] = [
    (50.0, Severity.HEALTHY),
    (20.0, Severity.BUSY),
    (10.0, Severity.CAUTION),
    (1.0, Severity.CRITICAL),
    (0.0, Severity.EXHAUSTED),
]

_HYSTERESIS = 3.0
RESET_HOLD = timedelta(minutes=30)

def band_for_remaining(remaining: float | None) -> Severity:
    if remaining is None or isinstance(remaining, bool):
        return Severity.STALE
    try:
        value = float(remaining)
    except (TypeError, ValueError, OverflowError):
        return Severity.STALE
    if not isfinite(value):
        return Severity.STALE
    if value <= 0:
        return Severity.EXHAUSTED
    for floor, band in _BANDS:
        if value >= floor:
            return band
    return Severity.EXHAUSTED

def apply_hysteresis(previous: Severity | None, remaining: float | None) -> Severity:
    current = band_for_remaining(remaining)
    if previous is None or remaining is None:
        return current
    if previous in {Severity.OFFLINE, Severity.STALE, Severity.ERROR, Severity.RESET}:
        return current
    if current == previous:
        return current
    # Crossing a boundary requires remaining to be 3% past the new band.
    floors = {Severity.HEALTHY: 50.0, Severity.BUSY: 20.0, Severity.CAUTION: 10.0, Severity.CRITICAL: 1.0}
    if _rank(current) > _rank(previous):
        floor = floors.get(current)
        if floor is not None and remaining < floor + _HYSTERESIS:
            return previous
    if _rank(current) < _rank(previous):
        floor = floors.get(previous)
        if floor is not None and remaining > floor - _HYSTERESIS:
            return previous
    return current

def _rank(severity: Severity) -> int:
    order = [
        Severity.EXHAUSTED,
        Severity.CRITICAL,
        Severity.CAUTION,
        Severity.BUSY,
        Severity.HEALTHY,
    ]
    try:
        return order.index(severity)
    except ValueError:
        return 3

def detect_reset(previous: UsageSnapshot | None, current: UsageSnapshot) -> bool:
    if previous is None or current.status != "ok" or not previous.windows or not current.windows:
        return False
    prev_min = previous.critical_remaining
    curr_min = current.critical_remaining
    if prev_min is None or curr_min is None:
        return False
    jumped = curr_min - prev_min >= 25.0
    reset_passed = False
    now = current.fetched_at
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    for old, new in zip(previous.windows, current.windows, strict=False):
        if old.resets_at and new.resets_at and new.resets_at > old.resets_at:
            if old.resets_at <= now:
                reset_passed = True
    return jumped and (reset_passed or prev_min < 30.0)

def snapshot_severity(
    snapshot: UsageSnapshot,
    previous_severity: Severity | None = None,
    reset_until: datetime | None = None,
) -> Severity:
    now = snapshot.fetched_at
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if reset_until and now < reset_until:
        return Severity.RESET
    if snapshot.status == "offline":
        return Severity.OFFLINE
    if snapshot.status == "stale":
        return Severity.STALE
    if snapshot.status in {"error", "rate_limited"}:
        return Severity.ERROR
    return apply_hysteresis(previous_severity, snapshot.critical_remaining)

def character_state(severity: Severity) -> CharacterState:
    mapping = {
        Severity.HEALTHY: CharacterState.IDLE,
        Severity.BUSY: CharacterState.BUSY,
        Severity.CAUTION: CharacterState.CAUTION,
        Severity.CRITICAL: CharacterState.CRITICAL,
        Severity.EXHAUSTED: CharacterState.EXHAUSTED,
        Severity.RESET: CharacterState.RESET,
        Severity.OFFLINE: CharacterState.OFFLINE,
        Severity.STALE: CharacterState.STALE,
        Severity.ERROR: CharacterState.STALE,
    }
    return mapping[severity]
