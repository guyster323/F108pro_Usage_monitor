from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
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
        if not self.windows:
            return None
        return min(w.remaining_percent for w in self.windows)
