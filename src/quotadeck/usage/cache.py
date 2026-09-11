"""Small thread-safe cache for local usage scans."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import isfinite
from threading import RLock
from time import monotonic
from typing import Callable, Hashable

from quotadeck.usage.models import UsageDataset


@dataclass(frozen=True, slots=True)
class UsageCacheEntry:
    dataset: UsageDataset
    stored_at: float


class UsageDatasetCache:
    """TTL cache that never serializes transcript-derived metadata to disk."""

    def __init__(
        self,
        ttl_seconds: float = 30.0,
        *,
        clock: Callable[[], float] = monotonic,
        max_entries: int = 64,
    ) -> None:
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("ttl_seconds must be a finite non-negative number") from exc
        if not isfinite(ttl) or ttl < 0:
            raise ValueError("ttl_seconds must be a finite non-negative number")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise ValueError("max_entries must be a positive integer")
        if max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self.ttl_seconds = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, UsageCacheEntry] = OrderedDict()
        self._lock = RLock()

    def _purge_expired(self, now: float) -> None:
        if self.ttl_seconds == 0:
            self._entries.clear()
            return
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.stored_at >= self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def get(self, key: Hashable) -> UsageDataset | None:
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.dataset

    def put(self, key: Hashable, dataset: UsageDataset) -> None:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            if self.ttl_seconds == 0:
                return
            self._entries[key] = UsageCacheEntry(dataset, now)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            self._purge_expired(self._clock())
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(self._clock())
            return len(self._entries)


__all__ = ["UsageCacheEntry", "UsageDatasetCache"]
