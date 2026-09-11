from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Literal


class Severity(str, Enum):
    HEALTHY = "healthy"
    BUSY = "busy"
    CAUTION = "caution"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"
    OFFLINE = "offline"
    STALE = "stale"
    RESET = "reset"
    ERROR = "error"

class CharacterState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    CAUTION = "caution"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"
    RESET = "reset"
    OFFLINE = "offline"
    STALE = "stale"


class DisplayMode(str, Enum):
    FIXED = "fixed"
    SMART = "smart"


class MetricMode(str, Enum):
    """The information shown on the LCD, independent of account ordering."""

    QUOTA = "quota"
    CUMULATIVE = "cumulative"


SnapshotStatus = Literal["ok", "stale", "offline", "error", "rate_limited"]

@dataclass(slots=True)
class UsageWindow:
    id: str
    label: str
    used_percent: float
    remaining_percent: float
    resets_at: datetime | None = None


@dataclass(slots=True)
class AccountRef:
    provider: str
    account_id: str
    display_name: str
    source_path: str
    plan: str | None = None
    email_local: str | None = None
    source_kind: str = "cli"
    source_label: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    @property
    def key(self) -> str:
        return f"{self.provider}:{self.account_id}"

    @property
    def source_badge(self) -> str:
        if self.source_kind == "app":
            return "APP"
        return "CLI"


@dataclass(slots=True)
class UsageSnapshot:
    provider: str
    account_id: str
    display_name: str
    plan: str | None
    windows: list[UsageWindow]
    status: SnapshotStatus
    fetched_at: datetime
    error: str | None = None
    source_path: str = ""
    @property
    def key(self) -> str:
        return f"{self.provider}:{self.account_id}"

    @property
    def critical_remaining(self) -> float | None:
        values = [
            value
            for window in self.windows
            if (value := normalize_remaining(window.remaining_percent)) is not None
        ]
        return min(values) if values else None


def normalize_remaining(remaining: object) -> float | None:
    """Return a finite 0..100 percentage, or None for untrusted input."""
    if remaining is None or isinstance(remaining, bool):
        return None
    try:
        value = float(remaining)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(value):
        return None
    return max(0.0, min(100.0, value))


def display_windows(snapshot: UsageSnapshot) -> list[UsageWindow]:
    """Keep the primary window and always expose the most constrained one."""
    windows = list(snapshot.windows)
    if len(windows) <= 2:
        return windows

    def quota_key(window: UsageWindow) -> float:
        remaining = normalize_remaining(window.remaining_percent)
        return 101.0 if remaining is None else remaining

    return [windows[0], min(windows[1:], key=quota_key)]
