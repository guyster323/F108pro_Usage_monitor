from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Consumer SPI NOR is typically rated ~100k program/erase cycles.
# F108 Pro does not publish the LCD flash rating; treat this as an estimate.
TYPICAL_SPI_NOR_CYCLES = 100_000
ACTIVE_HOURS_PER_DAY = 16


@dataclass
class FlashBudget:
    min_interval: timedelta = timedelta(minutes=10)
    max_age: timedelta = timedelta(minutes=60)
    daily_limit: int = 100
    last_upload: datetime | None = None
    uploads_today: int = 0
    day: date | None = None

    def _roll(self, now: datetime) -> None:
        today = now.date()
        if self.day != today:
            self.day = today
            self.uploads_today = 0

    def can_upload(self, now: datetime | None = None, *, force: bool = False) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self._roll(now)
        if self.uploads_today >= self.daily_limit:
            return False, f"daily flash limit reached ({self.daily_limit})"
        if self.last_upload is None:
            return True, "first upload"
        elapsed = now - self.last_upload
        if elapsed < self.min_interval and not force:
            remain = self.min_interval - elapsed
            return False, f"cooldown {int(remain.total_seconds())}s"
        if force or elapsed >= self.min_interval:
            return True, "interval ok"
        return False, "blocked"

    def next_allowed(self, now: datetime | None = None) -> datetime | None:
        """Earliest moment a non-forced upload may run, or None if allowed now."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self._roll(now)
        if self.uploads_today >= self.daily_limit:
            midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            return midnight
        if self.last_upload is None:
            return None
        ready = self.last_upload + self.min_interval
        return ready if ready > now else None

    def stale(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if self.last_upload is None:
            return True
        return now - self.last_upload >= self.max_age

    def record(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self._roll(now)
        self.last_upload = now
        self.uploads_today += 1

    def writes_per_hour(self) -> int:
        return max(1, int(3600 / max(self.min_interval.total_seconds(), 1)))

    def estimated_daily_writes(self, poll_seconds: int = 60) -> int:
        _ = poll_seconds
        return min(self.daily_limit, self.writes_per_hour() * ACTIVE_HOURS_PER_DAY)

    def estimated_uncapped_daily_writes(self) -> int:
        return self.writes_per_hour() * ACTIVE_HOURS_PER_DAY

    def estimated_years(
        self,
        poll_seconds: int = 60,
        cycles: int = TYPICAL_SPI_NOR_CYCLES,
        *,
        cap: bool = True,
    ) -> float:
        daily = self.estimated_daily_writes(poll_seconds) if cap else self.estimated_uncapped_daily_writes()
        if daily <= 0:
            return 0.0
        return cycles / daily / 365.0
