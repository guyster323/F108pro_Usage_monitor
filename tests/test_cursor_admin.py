from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from quotadeck.secrets.store import MemorySecretStore, store_cursor_admin_key
from quotadeck.usage.collectors import CollectorName, CollectorSettings, CollectorStatus
from quotadeck.usage.cursor_admin import (
    ADMIN_CACHE_SAVE_FAILURE_REASON,
    ADMIN_EMPTY_REASON,
    ADMIN_TRUNCATED_REASON,
    FILTERED_USAGE_EVENTS_URL,
    AdminGateLockError,
    CursorAdminAuthError,
    CursorAdminIncompleteError,
    admin_cache_path,
    admin_connection_is_fresh,
    admin_gate_entry,
    admin_sync_is_allowed,
    collect_cursor_admin,
    event_identity,
    event_matches_current_user,
    fetch_filtered_usage_events,
    load_admin_cache,
    lock_admin_account,
    normalize_admin_events,
    persist_admin_key_after_live_validation,
    reserve_admin_sync,
)


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _event(
    *,
    email: str = "dev@company.com",
    timestamp: str = "1750979225854",
    conversation: str = "conv-1",
    model: str = "claude-4.5-sonnet",
    input_tokens: int = 10,
    output_tokens: int = 4,
) -> dict:
    return {
        "timestamp": timestamp,
        "userEmail": email,
        "conversationId": conversation,
        "model": model,
        "isTokenBasedCall": True,
        "tokenUsage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheWriteTokens": 2,
            "cacheReadTokens": 8,
        },
    }


def test_normalize_filters_current_user_and_dedupes() -> None:
    events = [
        _event(),
        _event(),
        _event(email="admin@company.com", timestamp="1750979225999"),
        {
            "timestamp": "1750978339901",
            "userEmail": "dev@company.com",
            "model": "claude-4-sonnet-thinking",
            "isTokenBasedCall": False,
        },
    ]
    rows = normalize_admin_events(
        events,
        account="work",
        email="dev@company.com",
        user_id="work",
        max_records=20,
    )
    assert len(rows) == 1
    assert rows[0].tokens.input_tokens == 10
    assert rows[0].tokens.output_tokens == 4
    assert rows[0].tokens.cached_input_tokens == 8
    assert rows[0].tokens.cache_write_tokens == 2
    assert rows[0].tokens.reasoning_output_tokens == 0
    assert event_matches_current_user(events[0], email="dev@company.com", user_id=None)
    assert not event_matches_current_user(
        events[2], email="dev@company.com", user_id=None
    )
    assert event_identity(events[0]) == event_identity(events[1])


def test_normalize_marks_only_an_additional_valid_event_after_the_limit() -> None:
    exact_limit = normalize_admin_events(
        [
            _event(),
            _event(),
            _event(email="admin@company.com", timestamp="1750979225999"),
            {
                "timestamp": "1750978339901",
                "userEmail": "dev@company.com",
                "model": "claude-4-sonnet-thinking",
                "isTokenBasedCall": False,
            },
        ],
        account="work",
        email="dev@company.com",
        user_id="work",
        max_records=1,
    )
    assert len(exact_limit) == 1
    assert not exact_limit.truncated

    exceeded = normalize_admin_events(
        [
            _event(),
            _event(),
            _event(email="admin@company.com", timestamp="1750979225999"),
            {
                "timestamp": "1750978339901",
                "userEmail": "dev@company.com",
                "model": "claude-4-sonnet-thinking",
                "isTokenBasedCall": False,
            },
            _event(timestamp="1750979226000", conversation="conv-2"),
        ],
        account="work",
        email="dev@company.com",
        user_id="work",
        max_records=1,
    )
    assert len(exceeded) == 1
    assert exceeded.truncated


def test_admin_limit_partial_state_survives_cache_and_service_reload(
    tmp_path: Path,
) -> None:
    from quotadeck.usage.service import UsageLoadStatus, UsageService

    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    recent = str(int(time.time() * 1000))
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=1)

    def poster(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [
                    _event(timestamp=recent),
                    _event(timestamp=str(int(recent) + 1), conversation="conv-2"),
                ],
            },
        )

    first = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        force=True,
    )
    assert first.usable
    assert first.truncated
    assert first.reason == ADMIN_TRUNCATED_REASON
    assert first.dataset is not None
    assert first.dataset.coverages[0].scan_truncated

    cached = load_admin_cache(admin_cache_path("work", root=tmp_path), account="work")
    assert cached is not None
    assert cached.coverages[0].scan_truncated
    assert ADMIN_TRUNCATED_REASON in cached.coverages[0].limitations

    def boom(*_args, **_kwargs):
        raise AssertionError("the hourly gate must serve the persisted partial cache")

    reloaded = UsageService(collector=settings).load(
        "cursor",
        tmp_path / "state.vscdb",
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=boom,
    )
    assert reloaded.status is UsageLoadStatus.PARTIAL
    assert reloaded.available
    assert reloaded.reason == ADMIN_TRUNCATED_REASON
    assert reloaded.dataset is not None
    assert reloaded.dataset.coverages[0].scan_truncated


def test_legacy_admin_cache_is_conservatively_partial(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    def poster(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event(timestamp=str(int(time.time() * 1000)))],
            },
        )

    collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        force=True,
    )
    path = admin_cache_path("work", root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = "quotadeck.cursor_admin.v1"
    payload.pop("coverage", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    cached = load_admin_cache(path, account="work")
    assert cached is not None
    assert cached.coverages[0].scan_truncated
    assert any("predates completeness metadata" in item for item in cached.coverages[0].limitations)


def test_pagination_walks_has_next_page() -> None:
    pages = [
        FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": True, "currentPage": 1},
                "usageEvents": [_event(timestamp="1")],
            },
        ),
        FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False, "currentPage": 2},
                "usageEvents": [_event(timestamp="2", conversation="conv-2")],
            },
        ),
    ]
    calls: list[dict] = []

    def poster(url: str, **kwargs):
        assert url == FILTERED_USAGE_EVENTS_URL
        assert kwargs["auth"] == ("test-admin-key", "")
        calls.append(kwargs["json"])
        return pages[len(calls) - 1]

    events = fetch_filtered_usage_events(
        api_key="test-admin-key",
        email="dev@company.com",
        user_id=None,
        poster=poster,
        start_ms=1,
        end_ms=2,
    )
    assert len(events) == 2
    assert [item["page"] for item in calls] == [1, 2]
    assert all(item["pageSize"] == 1000 for item in calls)


def test_auth_failure_does_not_include_key() -> None:
    def poster(url: str, **kwargs):
        return FakeResponse(401, {"error": "nope"})

    try:
        fetch_filtered_usage_events(
            api_key="super-secret-admin-key-VALUE",
            email="dev@company.com",
            user_id=None,
            poster=poster,
            start_ms=1,
            end_ms=2,
        )
    except CursorAdminAuthError as exc:
        assert "super-secret-admin-key-VALUE" not in str(exc)
        assert "authorization" in str(exc).casefold()
    else:
        raise AssertionError("401 must raise CursorAdminAuthError")


def test_hourly_gate_is_independent_and_uses_cache(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)
    calls = {"n": 0}

    def poster(url: str, **kwargs):
        calls["n"] += 1
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event()],
            },
        )

    first = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        clock=lambda: 10_000.0,
        force=True,
    )
    assert first.usable
    assert calls["n"] == 1
    assert not admin_sync_is_allowed(account="work", root=tmp_path, now=10_100.0)
    assert admin_sync_is_allowed(account="home", root=tmp_path, now=10_100.0)

    def boom(*_args, **_kwargs):
        raise AssertionError("hourly gate must skip the network")

    second = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=boom,
        clock=lambda: 10_100.0,
        force=False,
    )
    assert second.usable
    assert not second.stale
    assert not second.live_validated
    assert not admin_connection_is_fresh(second)
    assert second.dataset is not None
    assert second.dataset.observations[0].tokens.input_tokens == 10

    third = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        clock=lambda: 14_000.0,
        force=False,
    )
    assert third.usable
    assert calls["n"] == 2


def test_failed_refresh_keeps_last_known_good(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event()],
            },
        )

    collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=ok,
        force=True,
    )

    def fail(url: str, **kwargs):
        return FakeResponse(500, {"error": "down"})

    stale = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=fail,
        force=True,
    )
    assert stale.usable
    assert stale.stale
    assert not stale.live_validated
    assert stale.name is CollectorName.CURSOR_ADMIN
    assert stale.dataset is not None
    assert stale.dataset.observations[0].tokens.input_tokens == 10
    assert not admin_connection_is_fresh(stale)

    gated = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=fail,
        clock=lambda: 10.0,
        force=False,
    )
    assert gated.usable
    assert gated.stale


def test_two_accounts_have_independent_admin_gates(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work", "home"), max_records=20)
    calls: list[str] = []

    def poster(url: str, **kwargs):
        calls.append(str(kwargs["json"].get("email")))
        email = kwargs["json"].get("email")
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event(email=email)],
            },
        )

    work = collect_cursor_admin(
        account="work",
        email="work@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        clock=lambda: 10_000.0,
        force=True,
    )
    home = collect_cursor_admin(
        account="home",
        email="home@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        clock=lambda: 10_100.0,
        force=False,
    )
    assert work.usable and home.usable
    assert calls == ["work@company.com", "home@company.com"]
    assert not admin_sync_is_allowed(account="work", root=tmp_path, now=10_200.0)
    assert not admin_sync_is_allowed(account="home", root=tmp_path, now=10_200.0)

    def boom(*_args, **_kwargs):
        raise AssertionError("each account must stay independently throttled")

    blocked_work = collect_cursor_admin(
        account="work",
        email="work@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=boom,
        clock=lambda: 10_200.0,
        force=False,
    )
    blocked_home = collect_cursor_admin(
        account="home",
        email="home@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=boom,
        clock=lambda: 10_200.0,
        force=False,
    )
    assert blocked_work.usable and not blocked_work.stale
    assert blocked_home.usable and not blocked_home.stale


def test_incomplete_pagination_preserves_last_known_good(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event()],
            },
        )

    first = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=ok,
        force=True,
    )
    assert first.usable and not first.stale

    def endless(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": True},
                "usageEvents": [_event(timestamp="999", input_tokens=99)],
            },
        )

    try:
        fetch_filtered_usage_events(
            api_key="test-admin-key",
            email="dev@company.com",
            user_id=None,
            poster=endless,
            start_ms=1,
            end_ms=2,
            max_pages=2,
        )
    except CursorAdminIncompleteError:
        pass
    else:
        raise AssertionError("incomplete pagination must fail safely")

    stale = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=endless,
        force=True,
    )
    assert stale.usable
    assert stale.stale
    assert stale.dataset is not None
    assert stale.dataset.observations[0].tokens.input_tokens == 10


def test_inline_key_is_not_stored_on_failed_validation(tmp_path: Path) -> None:
    store = MemorySecretStore()

    def unauthorized(url: str, **kwargs):
        return FakeResponse(401, {"error": "nope"})

    attempt = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=CollectorSettings(cursor_admin_accounts=("work",)),
        store=store,
        root=tmp_path,
        poster=unauthorized,
        api_key="inline-secret-key",
        force=True,
    )
    assert not attempt.usable
    assert not admin_connection_is_fresh(attempt)
    from quotadeck.secrets.store import load_cursor_admin_key

    assert load_cursor_admin_key(store=store) is None


def test_usage_service_marks_stale_admin_last_known_good(tmp_path: Path) -> None:
    from time import time

    from quotadeck.usage.service import UsageService

    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)
    service = UsageService(collector=settings)
    recent = str(int(time() * 1000))

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event(timestamp=recent)],
            },
        )

    first = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=ok,
        force=True,
    )
    assert first.available
    assert not first.stale

    def fail(url: str, **kwargs):
        return FakeResponse(500, {"error": "down"})

    stale = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=fail,
        force=True,
    )
    assert stale.available
    assert stale.stale
    assert stale.report is not None


def test_empty_http_200_validates_without_fabricating_zero(tmp_path: Path) -> None:
    store = MemorySecretStore()
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)
    calls = {"n": 0}

    def empty_ok(url: str, **kwargs):
        calls["n"] += 1
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": []},
        )

    attempt = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=empty_ok,
        api_key="inline-empty-ok-key",
        force=True,
    )
    assert attempt.status is CollectorStatus.OK
    assert attempt.live_validated
    assert admin_connection_is_fresh(attempt)
    assert attempt.dataset is not None
    assert attempt.dataset.observations == ()
    assert attempt.reason == ADMIN_EMPTY_REASON
    assert not attempt.stale
    from quotadeck.secrets.store import load_cursor_admin_key

    assert load_cursor_admin_key(store=store) is None
    entry = admin_gate_entry("work", root=tmp_path)
    assert float(entry.get("last_success") or 0) == 0
    cached = load_admin_cache(admin_cache_path("work", root=tmp_path), account="work")
    assert cached is not None
    assert cached.observations == ()
    assert not admin_sync_is_allowed(account="work", root=tmp_path, now=10.0)

    def boom(*_args, **_kwargs):
        raise AssertionError("empty success must consume the hourly gate")

    gated = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=boom,
        clock=lambda: 10.0,
        force=False,
    )
    assert gated.status is CollectorStatus.OK
    assert gated.dataset is not None
    assert gated.dataset.observations == ()
    assert gated.stale
    assert not gated.live_validated
    assert not admin_connection_is_fresh(gated)
    assert calls["n"] == 1


def test_empty_admin_snapshot_is_no_usage_yet_not_zero(tmp_path: Path) -> None:
    from quotadeck.core.models import AccountRef
    from quotadeck.usage.service import CumulativeUsageService, UsageLoadStatus, UsageService

    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)

    def empty_ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": []},
        )

    service = UsageService(collector=settings)
    loaded = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=empty_ok,
        force=True,
    )
    assert loaded.status is UsageLoadStatus.UNAVAILABLE
    assert loaded.report is None
    assert loaded.dataset is not None
    assert loaded.dataset.observations == ()
    assert loaded.reason == ADMIN_EMPTY_REASON

    from types import SimpleNamespace

    cards = CumulativeUsageService()
    cards.loader = service
    cards._engine_report = lambda _account: SimpleNamespace(  # type: ignore[method-assign]
        load_results=(loaded,),
        grok_scans=(),
    )
    snapshot = cards.snapshot(
        AccountRef(
            provider="cursor",
            account_id="work",
            display_name="WORK",
            source_path=str(tmp_path / "state.vscdb"),
            extra={"email": "dev@company.com"},
        )
    )
    assert not snapshot.available
    assert snapshot.report is None
    assert snapshot.source_label == "CURSOR ADMIN"
    assert snapshot.error == ADMIN_EMPTY_REASON
    assert snapshot.this_cost_usd is None
    assert snapshot.average_cost_usd is None


def test_auth_and_network_failures_still_fail_without_storing_key(tmp_path: Path) -> None:
    store = MemorySecretStore()
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    def unauthorized(url: str, **kwargs):
        return FakeResponse(401, {"error": "nope"})

    auth = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=unauthorized,
        api_key="candidate-admin-key",
        force=True,
    )
    assert not auth.usable
    assert not auth.live_validated
    assert not admin_connection_is_fresh(auth)
    from quotadeck.secrets.store import load_cursor_admin_key

    assert load_cursor_admin_key(store=store) is None
    assert not admin_cache_path("work", root=tmp_path).exists()

    def down(url: str, **kwargs):
        raise ConnectionError("offline")

    network = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=down,
        api_key="candidate-admin-key",
        force=True,
    )
    assert not network.usable
    assert not network.live_validated
    assert not admin_connection_is_fresh(network)
    assert load_cursor_admin_key(store=store) is None
    assert not admin_cache_path("work", root=tmp_path).exists()


def test_cache_save_failure_does_not_mark_data_refresh_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemorySecretStore()
    settings = CollectorSettings(cursor_admin_accounts=("work",))

    def poster(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event(timestamp=str(int(time.time() * 1000)))],
            },
        )

    def fail_save(*_args, **_kwargs):
        raise OSError("cache directory unavailable")

    monkeypatch.setattr("quotadeck.usage.cursor_admin.save_admin_cache", fail_save)
    attempt = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=poster,
        api_key="inline-cache-failure-key",
        force=True,
    )
    assert attempt.usable
    assert attempt.live_validated
    assert attempt.reason == ADMIN_CACHE_SAVE_FAILURE_REASON
    entry = admin_gate_entry("work", root=tmp_path)
    assert float(entry.get("last_success") or 0) == 0


def test_valid_empty_reconnect_preserves_nonempty_cache(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": [_event()]},
        )

    first = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=ok,
        force=True,
    )
    assert first.live_validated
    assert first.dataset is not None
    assert first.dataset.observations[0].tokens.input_tokens == 10

    def empty_ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": []},
        )

    reconnect = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=empty_ok,
        api_key="reconnect-empty-key",
        force=True,
    )
    assert reconnect.live_validated
    assert admin_connection_is_fresh(reconnect)
    assert reconnect.stale
    assert reconnect.reason == ADMIN_EMPTY_REASON
    assert reconnect.dataset is not None
    assert reconnect.dataset.observations[0].tokens.input_tokens == 10
    cached = load_admin_cache(admin_cache_path("work", root=tmp_path), account="work")
    assert cached is not None
    assert cached.observations[0].tokens.input_tokens == 10
    from quotadeck.secrets.store import load_cursor_admin_key

    assert load_cursor_admin_key(store=store) == "test-admin-key"


def test_valid_empty_with_prior_cache_propagates_stale(tmp_path: Path) -> None:
    from time import time

    from quotadeck.core.models import AccountRef
    from quotadeck.usage.service import CumulativeUsageService, UsageService

    store = MemorySecretStore()
    store_cursor_admin_key("test-admin-key", store=store)
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)
    recent = str(int(time() * 1000))

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {
                "pagination": {"hasNextPage": False},
                "usageEvents": [_event(timestamp=recent)],
            },
        )

    service = UsageService(collector=settings)
    first = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=ok,
        force=True,
    )
    assert first.available
    assert not first.stale

    def empty_ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": []},
        )

    stale = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=empty_ok,
        force=True,
    )
    assert stale.available
    assert stale.stale
    assert stale.report is not None
    assert stale.reason == ADMIN_EMPTY_REASON
    assert stale.dataset is not None
    assert stale.dataset.observations[0].tokens.input_tokens == 10

    from types import SimpleNamespace

    cards = CumulativeUsageService()
    cards.loader = service
    cards._engine_report = lambda _account: SimpleNamespace(  # type: ignore[method-assign]
        load_results=(stale,),
        grok_scans=(),
    )
    snapshot = cards.snapshot(
        AccountRef(
            provider="cursor",
            account_id="work",
            display_name="WORK",
            source_path=str(tmp_path / "state.vscdb"),
            extra={"email": "dev@company.com"},
        )
    )
    assert snapshot.available
    assert snapshot.status == "stale"
    assert snapshot.source_label == "CURSOR ADMIN STALE"
    assert snapshot.error == ADMIN_EMPTY_REASON

    def boom(*_args, **_kwargs):
        raise AssertionError("the hourly gate should serve the persisted stale cache")

    same_service = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=boom,
        force=False,
    )
    assert same_service.available
    assert same_service.stale
    assert same_service.reason == ADMIN_EMPTY_REASON

    reloaded_service = UsageService(collector=settings)
    reloaded = reloaded_service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=boom,
        force=False,
    )
    assert reloaded.available
    assert reloaded.stale
    assert reloaded.reason == ADMIN_EMPTY_REASON

    fresh = service.load(
        "cursor",
        tmp_path,
        account_id="work",
        account_email="dev@company.com",
        secret_store=store,
        admin_root=tmp_path,
        admin_poster=ok,
        force=True,
    )
    assert fresh.available
    assert not fresh.stale
    assert fresh.reason is None


def test_admin_key_persists_only_after_live_validation(tmp_path: Path) -> None:
    store = MemorySecretStore()
    settings = CollectorSettings(cursor_admin_accounts=("work",), max_records=20)

    def unauthorized(url: str, **kwargs):
        return FakeResponse(401, {"error": "nope"})

    auth = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=unauthorized,
        api_key="candidate-auth-fail",
        force=True,
    )
    assert not persist_admin_key_after_live_validation(
        auth, "candidate-auth-fail", store=store
    )
    from quotadeck.secrets.store import load_cursor_admin_key

    assert load_cursor_admin_key(store=store) is None

    def ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": [_event()]},
        )

    first = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=ok,
        api_key="live-ok-key",
        force=True,
    )
    assert persist_admin_key_after_live_validation(first, "live-ok-key", store=store)
    assert load_cursor_admin_key(store=store) == "live-ok-key"

    def boom(*_args, **_kwargs):
        raise AssertionError("gated cache must not count as live validation")

    gated = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=boom,
        clock=lambda: 10.0,
        force=False,
    )
    assert not persist_admin_key_after_live_validation(
        gated, "gated-should-not-store", store=store
    )
    assert load_cursor_admin_key(store=store) == "live-ok-key"

    def empty_ok(url: str, **kwargs):
        return FakeResponse(
            200,
            {"pagination": {"hasNextPage": False}, "usageEvents": []},
        )

    empty = collect_cursor_admin(
        account="work",
        email="dev@company.com",
        settings=settings,
        store=store,
        root=tmp_path,
        poster=empty_ok,
        api_key="empty-reconnect-key",
        force=True,
    )
    assert empty.stale
    assert persist_admin_key_after_live_validation(
        empty, "empty-reconnect-key", store=store
    )
    assert load_cursor_admin_key(store=store) == "empty-reconnect-key"


def test_reserve_admin_sync_is_per_account_and_skips_when_not_ready(
    tmp_path: Path,
) -> None:
    assert not reserve_admin_sync(
        account="work", root=tmp_path, now=10_000.0, ready=False
    )
    assert admin_sync_is_allowed(account="work", root=tmp_path, now=10_100.0)
    assert reserve_admin_sync(account="work", root=tmp_path, now=10_200.0, ready=True)
    assert not reserve_admin_sync(account="work", root=tmp_path, now=10_300.0)
    assert reserve_admin_sync(account="home", root=tmp_path, now=10_300.0)
    assert not admin_sync_is_allowed(account="work", root=tmp_path, now=10_400.0)
    assert not admin_sync_is_allowed(account="home", root=tmp_path, now=10_400.0)


def test_overlapping_same_account_reserve_only_one_wins(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        results.append(
            reserve_admin_sync(
                account="work",
                root=tmp_path,
                now=5_000.0,
                timeout_seconds=2.0,
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_admin_account_lock_cleans_up_after_error_and_timeout(tmp_path: Path) -> None:
    try:
        with lock_admin_account("work", root=tmp_path, timeout_seconds=0.5):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with lock_admin_account("work", root=tmp_path, timeout_seconds=0.5):
        pass

    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with lock_admin_account("work", root=tmp_path, timeout_seconds=3.0):
            held.set()
            release.wait(3.0)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(1.0)
    try:
        with lock_admin_account("work", root=tmp_path, timeout_seconds=0.2):
            raise AssertionError("held lock must time out")
    except AdminGateLockError:
        pass
    release.set()
    thread.join(2.0)
    with lock_admin_account("work", root=tmp_path, timeout_seconds=0.5):
        pass


def test_different_accounts_do_not_block_each_other(tmp_path: Path) -> None:
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with lock_admin_account("work", root=tmp_path, timeout_seconds=2.0):
            held.set()
            release.wait(2.0)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(1.0)
    started = time.monotonic()
    assert reserve_admin_sync(
        account="home",
        root=tmp_path,
        now=7_000.0,
        timeout_seconds=0.8,
    )
    assert time.monotonic() - started < 0.6
    release.set()
    thread.join(2.0)
