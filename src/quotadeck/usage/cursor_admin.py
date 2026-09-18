"""Official Cursor Team Admin API collector (filtered-usage-events).

The API key never enters config or logs. Events are attributed to the current
signed-in Cursor user, paginated, deduplicated, and cached as normalized
observations. Automatic sync is independently gated to at most once per hour.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time as _wall_clock
from typing import Any, Mapping

from quotadeck.config import app_dir
from quotadeck.secrets.store import (
    SecretStore,
    load_cursor_admin_key,
    store_cursor_admin_key,
)
from quotadeck.usage.collectors import (
    CollectorAttempt,
    CollectorName,
    CollectorSettings,
    CollectorStatus,
    coverage_for,
    observation_from_row,
)
from quotadeck.usage.models import (
    UsageCoverage,
    UsageDataset,
    UsageObservation,
    UsageSourceKind,
)

log = logging.getLogger("quotadeck.usage.cursor_admin")

FILTERED_USAGE_EVENTS_URL = "https://api.cursor.com/teams/filtered-usage-events"
ADMIN_SYNC_INTERVAL_SECONDS = 3600.0
ADMIN_GATE_LOCK_TIMEOUT_SECONDS = 5.0
ADMIN_CACHE_SCHEMA = "quotadeck.cursor_admin.v2"
_ADMIN_CACHE_LEGACY_SCHEMA = "quotadeck.cursor_admin.v1"
ADMIN_EMPTY_REASON = (
    "Cursor Admin API connected; no current-user token events yet."
)
ADMIN_TRUNCATED_REASON = (
    "Cursor Admin API reached the configured record limit; the displayed token subtotal is partial."
)
ADMIN_CACHE_SAVE_FAILURE_REASON = (
    "Cursor Admin API returned data, but the refreshed cache could not be saved."
)
ADMIN_SYNC_STATE_FAILURE_REASON = (
    "Cursor Admin API data was saved, but refresh state could not be recorded."
)
ADMIN_LEGACY_CACHE_REASON = (
    "This Cursor Admin cache predates completeness metadata; refresh is required before treating it as complete."
)
_MAX_PAGES = 50
_DEFAULT_PAGE_SIZE = 1000
_LOOKBACK_DAYS = 365
_ADMIN_LIMITATIONS = (
    "Cursor Admin API events are filtered to the current signed-in user.",
    "Request-based events without tokenUsage are skipped; reasoning is unknown.",
    "state.vscdb is never treated as a token ledger.",
    "An empty current-user result is unknown usage, not a measured zero.",
)

_Clock = Callable[[], float]
AdminPoster = Callable[..., Any]


class CursorAdminAuthError(Exception):
    """401/403 from the official Admin API. The message must not include the key."""


class CursorAdminRequestError(Exception):
    """Transport or protocol failure without response bodies."""


class CursorAdminIncompleteError(CursorAdminRequestError):
    """Pagination cap reached while the API still reports more pages."""


class AdminGateLockError(RuntimeError):
    """The per-account Admin gate lock could not be acquired in time."""


class AdminEventNormalization(tuple[UsageObservation, ...]):
    """Tuple-compatible normalized events plus bounded-collection metadata.

    Existing callers can continue to iterate, index, and take ``len()`` of the
    result.  ``truncated`` is true only when another distinct, attributable,
    token-bearing event was found after the configured observation limit.
    """

    def __new__(
        cls,
        observations: tuple[UsageObservation, ...],
        *,
        truncated: bool = False,
    ) -> AdminEventNormalization:
        value = super().__new__(cls, observations)
        value.truncated = bool(truncated)
        return value

    @property
    def observations(self) -> tuple[UsageObservation, ...]:
        return tuple(self)


def default_cursor_admin_dir(*, root: Path | None = None) -> Path:
    path = (root if root is not None else app_dir()) / "cursor-admin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def admin_cache_path(account_id: str, *, root: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in account_id.strip())[:80]
    return default_cursor_admin_dir(root=root) / f"{safe or 'cursor'}.json"


def admin_gate_path(*, root: Path | None = None) -> Path:
    return default_cursor_admin_dir(root=root) / "sync-gate.json"


def admin_account_lock_path(account: str, *, root: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in account.strip())[:80]
    return default_cursor_admin_dir(root=root) / f"{safe or 'cursor'}.gate.lock"


def admin_gate_file_lock_path(*, root: Path | None = None) -> Path:
    path = admin_gate_path(root=root)
    return path.with_name(f"{path.name}.lock")


@contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float = ADMIN_GATE_LOCK_TIMEOUT_SECONDS,
    busy_message: str = "another QuotaDeck instance holds this Admin sync lock",
) -> Iterator[None]:
    """Windows-safe exclusive lock modeled on flash-state locking."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise AdminGateLockError("Admin sync lock is unavailable") from exc

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
                        raise AdminGateLockError(busy_message) from exc
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
                        raise AdminGateLockError(busy_message) from exc
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


@contextmanager
def lock_admin_account(
    account: str,
    *,
    root: Path | None = None,
    timeout_seconds: float = ADMIN_GATE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    with exclusive_file_lock(
        admin_account_lock_path(account, root=root),
        timeout_seconds=timeout_seconds,
        busy_message="another QuotaDeck instance is reserving this Cursor Admin sync",
    ):
        yield


@contextmanager
def lock_admin_gate_file(
    *,
    root: Path | None = None,
    timeout_seconds: float = ADMIN_GATE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    with exclusive_file_lock(
        admin_gate_file_lock_path(root=root),
        timeout_seconds=timeout_seconds,
        busy_message="another QuotaDeck instance is updating the Admin sync gate",
    ):
        yield


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _gate_accounts(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (payload or {}).get("accounts")
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def _gate_account_key(accounts: Mapping[str, Any], account: str) -> str | None:
    wanted = account.strip().casefold()
    if not wanted:
        return None
    for key in accounts:
        if str(key).casefold() == wanted:
            return str(key)
    return None


def admin_gate_entry(
    account: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    accounts = _gate_accounts(_read_json(admin_gate_path(root=root)))
    key = _gate_account_key(accounts, account)
    if key is None:
        return {}
    entry = accounts.get(key)
    return dict(entry) if isinstance(entry, dict) else {}


def admin_sync_is_allowed(
    *,
    account: str,
    root: Path | None = None,
    now: float | None = None,
    interval_seconds: float = ADMIN_SYNC_INTERVAL_SECONDS,
) -> bool:
    if interval_seconds <= 0:
        return True
    try:
        last = float(admin_gate_entry(account, root=root).get("last_attempt") or 0)
    except (TypeError, ValueError):
        last = 0.0
    current = now if now is not None else _wall_clock()
    return (current - last) >= interval_seconds


def _write_admin_sync_attempt(
    *,
    account: str,
    root: Path | None,
    now: float,
    succeeded: bool,
    reason: str | None = None,
) -> None:
    path = admin_gate_path(root=root)
    previous = _read_json(path) or {}
    accounts = _gate_accounts(previous)
    key = _gate_account_key(accounts, account) or account.strip() or "cursor"
    existing = accounts.get(key) if isinstance(accounts.get(key), dict) else {}
    last_success = existing.get("last_success")
    previous_reason = existing.get("last_reason")
    last_reason = (
        None
        if succeeded
        else str(reason).strip()
        if reason is not None and str(reason).strip()
        else previous_reason
        if isinstance(previous_reason, str) and previous_reason.strip()
        else None
    )
    entry = {
        "last_attempt": now,
        "last_success": now if succeeded else last_success,
    }
    if last_reason:
        entry["last_reason"] = last_reason
    accounts[key] = entry
    _write_json(path, {"accounts": accounts})


def mark_admin_sync_attempt(
    *,
    account: str,
    root: Path | None = None,
    now: float | None = None,
    succeeded: bool = False,
    reason: str | None = None,
) -> None:
    current = now if now is not None else _wall_clock()
    with lock_admin_gate_file(root=root):
        _write_admin_sync_attempt(
            account=account,
            root=root,
            now=current,
            succeeded=succeeded,
            reason=reason,
        )


def _record_admin_sync_failure(
    *,
    account: str,
    root: Path | None,
    now: float,
    reason: str,
) -> None:
    try:
        mark_admin_sync_attempt(
            account=account,
            root=root,
            now=now,
            succeeded=False,
            reason=reason,
        )
    except (AdminGateLockError, OSError):
        log.warning("event=cursor_admin_sync_failure_state_save_failed", exc_info=True)


def reserve_admin_sync(
    *,
    account: str,
    root: Path | None = None,
    now: float | None = None,
    force: bool = False,
    ready: bool = True,
    interval_seconds: float = ADMIN_SYNC_INTERVAL_SECONDS,
    timeout_seconds: float = ADMIN_GATE_LOCK_TIMEOUT_SECONDS,
) -> bool:
    """Atomically decide and reserve one account's hourly Admin API slot.

    Different accounts use different lock files so they do not wait on each
    other. The shared gate file is locked only for the short read/modify/write.
    """

    current = now if now is not None else _wall_clock()
    with lock_admin_account(account, root=root, timeout_seconds=timeout_seconds):
        with lock_admin_gate_file(root=root, timeout_seconds=timeout_seconds):
            if not force and not admin_sync_is_allowed(
                account=account,
                root=root,
                now=current,
                interval_seconds=interval_seconds,
            ):
                return False
            if not ready:
                return False
            _write_admin_sync_attempt(
                account=account,
                root=root,
                now=current,
                succeeded=False,
            )
            return True


def clear_admin_account_state(account: str, *, root: Path | None = None) -> None:
    """Remove one account's last-known-good cache and per-account gate entry."""

    cache = admin_cache_path(account, root=root)
    try:
        cache.unlink(missing_ok=True)
    except OSError:
        pass
    path = admin_gate_path(root=root)
    with lock_admin_account(account, root=root):
        with lock_admin_gate_file(root=root):
            previous = _read_json(path) or {}
            accounts = _gate_accounts(previous)
            key = _gate_account_key(accounts, account)
            if key is None:
                return
            accounts.pop(key, None)
            _write_json(path, {"accounts": accounts})


def event_identity(event: Mapping[str, Any]) -> str:
    usage = event.get("tokenUsage") if isinstance(event.get("tokenUsage"), Mapping) else {}
    return "|".join(
        (
            str(event.get("timestamp") or ""),
            str(event.get("conversationId") or ""),
            str(event.get("model") or ""),
            str((usage or {}).get("inputTokens") or 0),
            str((usage or {}).get("outputTokens") or 0),
            str((usage or {}).get("cacheWriteTokens") or 0),
            str((usage or {}).get("cacheReadTokens") or 0),
        )
    )


def event_matches_current_user(
    event: Mapping[str, Any],
    *,
    email: str | None,
    user_id: str | None,
) -> bool:
    wanted_email = (email or "").strip().casefold()
    wanted_user = str(user_id or "").strip()
    event_email = str(event.get("userEmail") or "").strip().casefold()
    event_user = str(event.get("userId") or event.get("user_id") or "").strip()
    if wanted_email and event_email:
        return event_email == wanted_email
    if wanted_user and event_user:
        return event_user == wanted_user
    return False


def _token_row(event: Mapping[str, Any]) -> dict[str, Any] | None:
    usage = event.get("tokenUsage")
    if not isinstance(usage, Mapping):
        return None
    if event.get("isTokenBasedCall") is False:
        return None
    model = str(event.get("model") or "").strip()
    if not model:
        return None
    timestamp = event.get("timestamp")
    row = {
        "model": model,
        "timestamp": timestamp,
        "session_id": str(event.get("conversationId") or "cursor-admin"),
        "event_id": event_identity(event),
        "inputTokens": usage.get("inputTokens"),
        "outputTokens": usage.get("outputTokens"),
        "cacheReadTokens": usage.get("cacheReadTokens"),
        "cacheWriteTokens": usage.get("cacheWriteTokens"),
    }
    return row


def normalize_admin_events(
    events: list[Mapping[str, Any]],
    *,
    account: str,
    email: str | None,
    user_id: str | None,
    max_records: int,
) -> AdminEventNormalization:
    limit = max(0, int(max_records))
    seen: set[str] = set()
    rows: list[UsageObservation] = []
    truncated = False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if not event_matches_current_user(event, email=email, user_id=user_id):
            continue
        mapped = _token_row(event)
        if mapped is None:
            continue
        identity = event_identity(event)
        if identity in seen:
            continue
        item = observation_from_row(
            mapped,
            provider="cursor",
            source_kind=UsageSourceKind.CURSOR_ADMIN,
            default_model="cursor",
            account=account,
            index=len(rows),
        )
        if item is None:
            continue
        seen.add(identity)
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(item)
    return AdminEventNormalization(tuple(rows), truncated=truncated)


def save_admin_cache(
    path: Path,
    *,
    account: str,
    email: str | None,
    observations: tuple[UsageObservation, ...],
    coverage: UsageCoverage | None = None,
) -> None:
    stored_coverage = coverage or coverage_for(
        provider="cursor",
        source_kind=UsageSourceKind.CURSOR_ADMIN,
        source_label="CURSOR ADMIN",
        location_hint="cursor-admin-cache",
        observations=observations,
        limitations=_ADMIN_LIMITATIONS,
    )
    payload = {
        "schema": ADMIN_CACHE_SCHEMA,
        "account": account,
        "email": email or "",
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage": {
            "scan_truncated": bool(stored_coverage.scan_truncated),
            "limitations": list(stored_coverage.limitations),
        },
        "observations": [
            {
                "model": item.model,
                "observed_at": item.observed_at.isoformat(),
                "event_id": item.event_id,
                "session_id": item.session_id,
                "input_tokens": item.tokens.input_tokens,
                "output_tokens": item.tokens.output_tokens,
                "cached_input_tokens": item.tokens.cached_input_tokens,
                "cache_write_tokens": item.tokens.cache_write_tokens,
            }
            for item in observations
        ],
    }
    _write_json(path, payload)


def load_admin_cache(path: Path, *, account: str) -> UsageDataset | None:
    payload = _read_json(path)
    schema = str((payload or {}).get("schema") or "")
    if payload is None or schema not in {ADMIN_CACHE_SCHEMA, _ADMIN_CACHE_LEGACY_SCHEMA}:
        return None
    cached_account = str(payload.get("account") or "").strip()
    if cached_account and cached_account.casefold() != account.casefold():
        return None
    observations: list[UsageObservation] = []
    raw_rows = payload.get("observations")
    if not isinstance(raw_rows, list):
        return None
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            continue
        item = observation_from_row(
            raw,
            provider="cursor",
            source_kind=UsageSourceKind.CURSOR_ADMIN,
            default_model="cursor",
            account=account,
            index=index,
        )
        if item is not None:
            observations.append(item)
    scan_truncated = False
    limitations = _ADMIN_LIMITATIONS
    if schema == _ADMIN_CACHE_LEGACY_SCHEMA:
        # v1 did not record whether the bounded normalizer reached its limit.
        # Keep the data usable, but never claim it was complete.
        scan_truncated = True
        limitations = _ADMIN_LIMITATIONS + (ADMIN_LEGACY_CACHE_REASON,)
    else:
        metadata = payload.get("coverage")
        if not isinstance(metadata, Mapping) or "scan_truncated" not in metadata:
            scan_truncated = True
            limitations = _ADMIN_LIMITATIONS + (ADMIN_LEGACY_CACHE_REASON,)
        else:
            scan_truncated = bool(metadata.get("scan_truncated"))
            raw_limitations = metadata.get("limitations")
            if isinstance(raw_limitations, list):
                parsed_limitations = tuple(
                    str(item).strip() for item in raw_limitations if str(item).strip()
                )
                if parsed_limitations:
                    limitations = parsed_limitations
    coverage = coverage_for(
        provider="cursor",
        source_kind=UsageSourceKind.CURSOR_ADMIN,
        source_label="CURSOR ADMIN",
        location_hint="cursor-admin-cache",
        observations=tuple(observations),
        truncated=scan_truncated,
        limitations=limitations,
    )
    return UsageDataset(tuple(observations), (coverage,))


def _window_ms(*, now: datetime | None = None) -> tuple[int, int]:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _default_poster(url: str, **kwargs: Any) -> Any:
    from quotadeck.http import post

    return post(url, **kwargs)


def fetch_filtered_usage_events(
    *,
    api_key: str,
    email: str | None,
    user_id: str | None,
    poster: AdminPoster | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_pages: int = _MAX_PAGES,
) -> list[dict[str, Any]]:
    if not email and not user_id:
        raise CursorAdminRequestError(
            "Current Cursor user email or user id is required for Admin API attribution."
        )
    start, end = _window_ms() if start_ms is None or end_ms is None else (start_ms, end_ms)
    send = poster or _default_poster
    events: list[dict[str, Any]] = []
    page = 1
    has_next = False
    while page <= max_pages:
        body: dict[str, Any] = {
            "startDate": start,
            "endDate": end,
            "page": page,
            "pageSize": min(1000, max(1, page_size)),
        }
        if email:
            body["email"] = email
        elif user_id:
            try:
                body["userId"] = int(user_id)
            except (TypeError, ValueError):
                body["userId"] = user_id
        try:
            response = send(
                FILTERED_USAGE_EVENTS_URL,
                json=body,
                auth=(api_key, ""),
                headers={"Content-Type": "application/json"},
            )
        except CursorAdminAuthError:
            raise
        except Exception as exc:
            raise CursorAdminRequestError("Cursor Admin API request failed.") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status in {401, 403}:
            raise CursorAdminAuthError("Cursor Admin API authorization failed.")
        if status >= 400:
            raise CursorAdminRequestError(f"Cursor Admin API returned HTTP {status}.")
        try:
            payload = response.json()
        except Exception as exc:
            raise CursorAdminRequestError("Cursor Admin API returned a non-JSON body.") from exc
        if not isinstance(payload, Mapping):
            raise CursorAdminRequestError("Cursor Admin API returned an unexpected payload.")
        page_events = payload.get("usageEvents")
        if isinstance(page_events, list):
            events.extend(item for item in page_events if isinstance(item, Mapping))
        pagination = payload.get("pagination")
        has_next = isinstance(pagination, Mapping) and bool(pagination.get("hasNextPage"))
        log.info("event=cursor_admin_page page=%s events=%s next=%s", page, len(events), has_next)
        if not has_next:
            break
        page += 1
    if has_next:
        raise CursorAdminIncompleteError(
            "Cursor Admin API pagination exceeded the page cap; last-known-good cache is kept."
        )
    return events


def _attempt_from_dataset(
    dataset: UsageDataset,
    *,
    stale: bool = False,
    reason: str | None = None,
    live_validated: bool = False,
    truncated: bool | None = None,
) -> CollectorAttempt:
    bounded = (
        any(item.scan_truncated for item in dataset.coverages)
        if truncated is None
        else bool(truncated)
    )
    return CollectorAttempt(
        CollectorName.CURSOR_ADMIN,
        CollectorStatus.OK,
        dataset=dataset,
        reason=reason,
        truncated=bounded,
        stale=stale,
        live_validated=live_validated,
    )


def _cached_attempt_from_gate(
    dataset: UsageDataset,
    *,
    account: str,
    root: Path | None,
    reason: str | None = None,
) -> CollectorAttempt:
    entry = admin_gate_entry(account, root=root)
    try:
        last_attempt = float(entry.get("last_attempt") or 0)
        last_success = float(entry.get("last_success") or 0)
    except (TypeError, ValueError):
        last_attempt = 0.0
        last_success = 0.0
    stale = last_success <= 0 or last_attempt > last_success + 1e-6
    stored_reason = entry.get("last_reason")
    cached_reason = reason or (
        str(stored_reason).strip()
        if isinstance(stored_reason, str) and stored_reason.strip()
        else None
    )
    if cached_reason is None and stale:
        cached_reason = (
            ADMIN_EMPTY_REASON
            if not dataset.observations
            else "Serving last-known-good Cursor Admin cache."
        )
    return _attempt_from_dataset(
        dataset,
        stale=stale,
        reason=cached_reason,
    )


def collect_cursor_admin(
    *,
    account: str,
    email: str | None,
    user_id: str | None = None,
    settings: CollectorSettings | None = None,
    store: SecretStore | None = None,
    root: Path | None = None,
    poster: AdminPoster | None = None,
    clock: _Clock | None = None,
    force: bool = False,
    api_key: str | None = None,
) -> CollectorAttempt:
    cfg = settings or CollectorSettings.from_env()
    cache_file = admin_cache_path(account, root=root)
    cached = load_admin_cache(cache_file, account=account)
    now = (clock or _wall_clock)()
    key = (api_key or "").strip() or (load_cursor_admin_key(store=store) or "")
    attributed = bool((email or "").strip() or (user_id or "").strip())
    try:
        reserved = reserve_admin_sync(
            account=account,
            root=root,
            now=now,
            force=force,
            ready=bool(key) and attributed,
        )
    except AdminGateLockError as exc:
        if cached is not None:
            return _cached_attempt_from_gate(
                cached,
                account=account,
                root=root,
                reason=str(exc),
            )
        return CollectorAttempt(
            CollectorName.CURSOR_ADMIN,
            CollectorStatus.MISSING,
            reason=str(exc),
        )
    if not reserved:
        if not force and not admin_sync_is_allowed(account=account, root=root, now=now):
            if cached is not None:
                log.info("event=cursor_admin_gate skipped=hourly account_bound=1")
                return _cached_attempt_from_gate(cached, account=account, root=root)
            return CollectorAttempt(
                CollectorName.CURSOR_ADMIN,
                CollectorStatus.MISSING,
                reason="Cursor Admin API hourly gate is closed and no local cache exists.",
            )
        if not key:
            if cached is not None:
                return _attempt_from_dataset(
                    cached,
                    stale=True,
                    reason="Cursor Admin API key is missing; last-known-good cache is shown.",
                )
            return CollectorAttempt(
                CollectorName.CURSOR_ADMIN,
                CollectorStatus.UNUSABLE,
                reason="Cursor Admin API key is not stored. Reconnect Enterprise API.",
            )
        if not attributed:
            if cached is not None:
                return _attempt_from_dataset(
                    cached,
                    stale=True,
                    reason="Current Cursor user could not be attributed.",
                )
            return CollectorAttempt(
                CollectorName.CURSOR_ADMIN,
                CollectorStatus.UNUSABLE,
                reason="Current Cursor user email is required for Admin API attribution.",
            )
        if cached is not None:
            return _cached_attempt_from_gate(cached, account=account, root=root)
        return CollectorAttempt(
            CollectorName.CURSOR_ADMIN,
            CollectorStatus.MISSING,
            reason="Cursor Admin API hourly gate is closed and no local cache exists.",
        )
    try:
        events = fetch_filtered_usage_events(
            api_key=key,
            email=email,
            user_id=user_id,
            poster=poster,
        )
    except CursorAdminAuthError as exc:
        log.info("event=cursor_admin_auth_failed")
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=str(exc),
        )
        if cached is not None:
            return _attempt_from_dataset(cached, stale=True, reason=str(exc))
        return CollectorAttempt(
            CollectorName.CURSOR_ADMIN,
            CollectorStatus.UNUSABLE,
            reason=str(exc),
        )
    except CursorAdminIncompleteError as exc:
        log.info("event=cursor_admin_pagination_incomplete")
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=str(exc),
        )
        if cached is not None:
            return _attempt_from_dataset(cached, stale=True, reason=str(exc))
        return CollectorAttempt(
            CollectorName.CURSOR_ADMIN,
            CollectorStatus.UNUSABLE,
            reason=str(exc),
        )
    except CursorAdminRequestError as exc:
        log.info("event=cursor_admin_request_failed")
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=str(exc),
        )
        if cached is not None:
            return _attempt_from_dataset(cached, stale=True, reason=str(exc))
        return CollectorAttempt(
            CollectorName.CURSOR_ADMIN,
            CollectorStatus.UNUSABLE,
            reason=str(exc),
        )
    finally:
        key = ""

    normalization = normalize_admin_events(
        events,
        account=account,
        email=email,
        user_id=user_id,
        max_records=cfg.max_records,
    )
    observations = normalization.observations
    coverage = coverage_for(
        provider="cursor",
        source_kind=UsageSourceKind.CURSOR_ADMIN,
        source_label="CURSOR ADMIN",
        location_hint="cursor-admin-api",
        observations=observations,
        truncated=normalization.truncated,
        limitations=(
            _ADMIN_LIMITATIONS
            + ((ADMIN_TRUNCATED_REASON,) if normalization.truncated else ())
        ),
    )
    dataset = UsageDataset(observations, (coverage,))
    if not observations and cached is not None and cached.observations:
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=ADMIN_EMPTY_REASON,
        )
        log.info(
            "event=cursor_admin_sync status=ok observations=0 preserved_cache=%s",
            len(cached.observations),
        )
        return _attempt_from_dataset(
            cached,
            stale=True,
            reason=ADMIN_EMPTY_REASON,
            live_validated=True,
        )
    try:
        save_admin_cache(
            cache_file,
            account=account,
            email=email,
            observations=observations,
            coverage=coverage,
        )
    except (OSError, TypeError, ValueError):
        log.warning("event=cursor_admin_cache_save_failed", exc_info=True)
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=(
                ADMIN_CACHE_SAVE_FAILURE_REASON
                if observations
                else ADMIN_TRUNCATED_REASON
                if normalization.truncated
                else ADMIN_EMPTY_REASON
            ),
        )
        return _attempt_from_dataset(
            dataset,
            reason=(
                ADMIN_CACHE_SAVE_FAILURE_REASON
                if observations
                else ADMIN_TRUNCATED_REASON
                if normalization.truncated
                else ADMIN_EMPTY_REASON
            ),
            live_validated=True,
            stale=True,
            truncated=normalization.truncated,
        )
    if observations:
        try:
            mark_admin_sync_attempt(account=account, root=root, now=now, succeeded=True)
        except (AdminGateLockError, OSError):
            log.warning("event=cursor_admin_sync_state_save_failed", exc_info=True)
            return _attempt_from_dataset(
                dataset,
                reason=ADMIN_SYNC_STATE_FAILURE_REASON,
                live_validated=True,
                stale=True,
                truncated=normalization.truncated,
            )
    else:
        _record_admin_sync_failure(
            account=account,
            root=root,
            now=now,
            reason=(
                ADMIN_TRUNCATED_REASON
                if normalization.truncated
                else ADMIN_EMPTY_REASON
            ),
        )
    log.info("event=cursor_admin_sync status=ok observations=%s", len(observations))
    return _attempt_from_dataset(
        dataset,
        reason=(
            ADMIN_TRUNCATED_REASON
            if normalization.truncated
            else None if observations else ADMIN_EMPTY_REASON
        ),
        live_validated=True,
        truncated=normalization.truncated,
    )


def admin_connection_is_fresh(attempt: CollectorAttempt) -> bool:
    """True only after a live Admin API validation, including a valid-empty page."""

    return bool(attempt.live_validated)


def persist_admin_key_after_live_validation(
    attempt: CollectorAttempt,
    api_key: str,
    *,
    store: SecretStore | None = None,
) -> bool:
    """Store the candidate key only after a live Admin validation."""

    if not admin_connection_is_fresh(attempt):
        return False
    store_cursor_admin_key(api_key, store=store)
    return True


__all__ = [
    "ADMIN_CACHE_SCHEMA",
    "ADMIN_EMPTY_REASON",
    "ADMIN_CACHE_SAVE_FAILURE_REASON",
    "ADMIN_GATE_LOCK_TIMEOUT_SECONDS",
    "ADMIN_LEGACY_CACHE_REASON",
    "ADMIN_SYNC_INTERVAL_SECONDS",
    "ADMIN_SYNC_STATE_FAILURE_REASON",
    "ADMIN_TRUNCATED_REASON",
    "FILTERED_USAGE_EVENTS_URL",
    "AdminEventNormalization",
    "AdminGateLockError",
    "CursorAdminAuthError",
    "CursorAdminIncompleteError",
    "CursorAdminRequestError",
    "admin_cache_path",
    "admin_connection_is_fresh",
    "admin_sync_is_allowed",
    "clear_admin_account_state",
    "collect_cursor_admin",
    "event_identity",
    "event_matches_current_user",
    "fetch_filtered_usage_events",
    "load_admin_cache",
    "lock_admin_account",
    "normalize_admin_events",
    "persist_admin_key_after_live_validation",
    "reserve_admin_sync",
    "save_admin_cache",
]
