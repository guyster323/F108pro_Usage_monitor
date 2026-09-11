from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil
from pathlib import Path
# Consumer SPI NOR is typically rated ~100k program/erase cycles.
# F108 Pro does not publish the LCD flash rating; treat this as an estimate.
TYPICAL_SPI_NOR_CYCLES = 100_000
ACTIVE_HOURS_PER_DAY = 16
FLASH_LOCK_TIMEOUT_SECONDS = 30.0


class FlashStateLockError(RuntimeError):
    """Wear state could not be locked safely, so no flash write may proceed."""


@contextmanager
def lock_flash_state(
    state_path: Path,
    *,
    timeout_seconds: float = FLASH_LOCK_TIMEOUT_SECONDS,
):
    """Serialize the complete flash decision/write transaction across processes."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise FlashStateLockError("flash state lock is unavailable") from exc

    deadline = time.monotonic() + timeout_seconds
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise FlashStateLockError(
                            "another QuotaDeck instance is using flash"
                        ) from exc
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise FlashStateLockError(
                            "another QuotaDeck instance is using flash"
                        ) from exc
                    time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


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

    def restore(self, path: Path, now: datetime | None = None) -> bool:
        """Restore non-secret wear state, ignoring malformed or partial files."""

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                return False
            raw_day = raw.get("day")
            saved_day = date.fromisoformat(raw_day) if isinstance(raw_day, str) else None
            raw_last = raw.get("last_upload")
            last_upload = (
                datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
                if isinstance(raw_last, str) and raw_last
                else None
            )
            if last_upload is not None:
                if last_upload.tzinfo is None:
                    last_upload = last_upload.replace(tzinfo=timezone.utc)
                else:
                    last_upload = last_upload.astimezone(timezone.utc)
                if last_upload > now:
                    last_upload = now
            uploads_today = int(raw.get("uploads_today", 0))
            if uploads_today < 0:
                return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

        self.day = saved_day
        self.uploads_today = uploads_today
        self.last_upload = last_upload
        self._roll(now)
        return True

    def persist(self, path: Path) -> None:
        """Atomically persist the conservative write-ahead wear reservation."""

        path.parent.mkdir(parents=True, exist_ok=True)
        last_upload = self.last_upload
        if last_upload is not None and last_upload.tzinfo is None:
            last_upload = last_upload.replace(tzinfo=timezone.utc)
        payload = {
            "version": 1,
            "day": self.day.isoformat() if self.day is not None else None,
            "last_upload": (
                last_upload.astimezone(timezone.utc).isoformat()
                if last_upload is not None
                else None
            ),
            "uploads_today": self.uploads_today,
        }
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def writes_per_hour(self) -> float:
        return 3600 / max(self.min_interval.total_seconds(), 1)

    def estimated_daily_writes(self, poll_seconds: int = 60) -> int:
        _ = poll_seconds
        return min(self.daily_limit, self.estimated_uncapped_daily_writes())

    def estimated_uncapped_daily_writes(self) -> int:
        active_seconds = ACTIVE_HOURS_PER_DAY * 3600
        interval_seconds = max(self.min_interval.total_seconds(), 1)
        return ceil(active_seconds / interval_seconds)
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
