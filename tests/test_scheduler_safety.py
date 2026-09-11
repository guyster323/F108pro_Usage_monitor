from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quotadeck.config import AppConfig
from quotadeck.core.flashbudget import FlashBudget
from quotadeck.core.models import AccountRef, Severity, UsageSnapshot, UsageWindow
from quotadeck.core.scheduler import QuotaDeckRuntime, render_hash
from quotadeck.core.severity import detect_reset


def _snapshot() -> UsageSnapshot:
    return UsageSnapshot(
        provider="codex",
        account_id="one",
        display_name="ONE",
        plan="plus",
        windows=[UsageWindow("primary", "5H", 20, 80)],
        status="ok",
        fetched_at=datetime.now(timezone.utc),
    )


def test_runtime_restores_flash_state_across_recreation(tmp_path) -> None:
    path = tmp_path / "flash-state.json"
    now = datetime.now(timezone.utc)
    first = QuotaDeckRuntime(AppConfig(), mock=True, flash_state_path=path)
    first.budget.record(now)
    first.budget.persist(path)

    restarted = QuotaDeckRuntime(AppConfig(), mock=True, flash_state_path=path)
    assert restarted.budget.last_upload == now
    assert restarted.budget.uploads_today == 1
    allowed, reason = restarted.budget.can_upload(now + timedelta(minutes=1))
    assert not allowed
    assert reason.startswith("cooldown")


def test_automatic_stale_refresh_does_not_bypass_flash_cooldown() -> None:
    config = AppConfig(min_upload_minutes=10, max_age_minutes=1)
    runtime = QuotaDeckRuntime(config, mock=True)
    now = datetime.now(timezone.utc)
    snapshot = _snapshot()
    runtime.state.severity[snapshot.key] = Severity.HEALTHY
    runtime.state.last_hash = render_hash(
        [snapshot],
        runtime.state.severity,
        theme=config.theme,
        mode=config.display_mode.value,
        hold_seconds=config.scene_hold_seconds,
        frame_budget=config.frame_budget,
    )
    runtime.budget.last_upload = now - timedelta(minutes=2)
    result = runtime.maybe_upload([snapshot])
    assert result.startswith("deferred: cooldown")


def test_runtime_prunes_vanished_account_state() -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True)
    runtime.state.severity = {
        "codex:active": Severity.HEALTHY,
        "codex:gone": Severity.EXHAUSTED,
    }
    runtime.state.previous["codex:gone"] = _snapshot()
    runtime.state.reset_until["codex:gone"] = datetime.now(timezone.utc)
    runtime._retain_active_accounts({"codex:active"})
    assert set(runtime.state.severity) == {"codex:active"}
    assert runtime.state.previous == {}
    assert runtime.state.reset_until == {}


def test_one_broken_provider_discovery_does_not_hide_other_accounts() -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True)

    class BrokenProvider:
        id = "broken"

        def discover(self):
            raise ValueError("damaged auth store")

    class HealthyProvider:
        id = "codex"

        def discover(self):
            return [
                AccountRef(
                    provider="codex",
                    account_id="healthy",
                    display_name="HEALTHY",
                    source_path="",
                )
            ]

    runtime.providers = {
        "broken": BrokenProvider(),
        "codex": HealthyProvider(),
    }

    assert [account.account_id for account in runtime.selected_accounts()] == [
        "healthy"
    ]


def test_one_broken_provider_fetch_does_not_hide_other_snapshots(monkeypatch) -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True)
    broken_account = AccountRef(
        provider="broken",
        account_id="broken-one",
        display_name="BROKEN",
        source_path="/redacted/broken",
        plan="api",
    )
    healthy_account = AccountRef(
        provider="healthy",
        account_id="healthy-one",
        display_name="HEALTHY",
        source_path="/redacted/healthy",
        plan="plus",
    )

    class BrokenProvider:
        id = "broken"

        def fetch(self, _account):
            raise ValueError("damaged provider response")

    class HealthyProvider:
        id = "healthy"

        def fetch(self, _account):
            return UsageSnapshot(
                provider="healthy",
                account_id="healthy-one",
                display_name="HEALTHY",
                plan="plus",
                windows=[UsageWindow("primary", "5H", 20, 80)],
                status="ok",
                fetched_at=datetime.now(timezone.utc),
            )

    runtime.providers = {
        "broken": BrokenProvider(),
        "healthy": HealthyProvider(),
    }
    monkeypatch.setattr(
        runtime,
        "selected_accounts",
        lambda: [broken_account, healthy_account],
    )

    snapshots = runtime.poll()

    assert [snapshot.account_id for snapshot in snapshots] == [
        "broken-one",
        "healthy-one",
    ]
    assert snapshots[0].status == "error"
    assert snapshots[0].windows == []
    assert snapshots[0].error == "Provider usage could not be fetched."
    assert snapshots[1].status == "ok"


def test_quota_hash_includes_frame_budget() -> None:
    snapshot = _snapshot()
    severities = {snapshot.key: Severity.HEALTHY}
    assert render_hash(
        [snapshot], severities, theme="crew", mode="smart", frame_budget=4
    ) != render_hash(
        [snapshot], severities, theme="crew", mode="smart", frame_budget=8
    )


def test_render_hash_normalizes_mixed_naive_and_aware_resets() -> None:
    naive = datetime(2026, 9, 11, 12, 30)
    offset = datetime(
        2026,
        9,
        11,
        21,
        30,
        tzinfo=timezone(timedelta(hours=9)),
    )
    mixed = _snapshot()
    mixed.windows = [
        UsageWindow("session", "5H", 20, 80, naive),
        UsageWindow("weekly", "WEEK", 30, 70, offset),
    ]
    normalized = _snapshot()
    normalized.windows = [
        UsageWindow(
            "session",
            "5H",
            20,
            80,
            datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        ),
        UsageWindow(
            "weekly",
            "WEEK",
            30,
            70,
            datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        ),
    ]
    severities = {mixed.key: Severity.HEALTHY}

    assert render_hash(
        [mixed], severities, theme="crew", mode="smart"
    ) == render_hash(
        [normalized], severities, theme="crew", mode="smart"
    )


def test_reset_detection_normalizes_provider_timestamps() -> None:
    previous = _snapshot()
    previous.windows = [
        UsageWindow(
            "session",
            "5H",
            95,
            5,
            datetime(2026, 9, 11, 12, 30),
        )
    ]
    current = _snapshot()
    current.fetched_at = datetime(2026, 9, 11, 13, tzinfo=timezone.utc)
    current.windows = [
        UsageWindow(
            "session",
            "5H",
            20,
            80,
            datetime(
                2026,
                9,
                11,
                22,
                30,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )
    ]

    assert detect_reset(previous, current)


def test_failed_device_write_keeps_conservative_write_ahead_reservation(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "flash-state.json"
    runtime = QuotaDeckRuntime(AppConfig(), flash_state_path=path)
    snapshot = _snapshot()
    runtime.state.severity[snapshot.key] = Severity.HEALTHY
    monkeypatch.setattr(runtime, "build_frames", lambda _snapshots: [object()])

    class FailedDevice:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def upload_frames(self, _frames, *, progress):
            _ = progress
            raise RuntimeError("simulated device failure")

    monkeypatch.setattr("quotadeck.core.scheduler.F108Device", FailedDevice)
    with pytest.raises(RuntimeError, match="simulated device failure"):
        runtime.maybe_upload([snapshot])

    assert runtime.budget.uploads_today == 1
    assert runtime.budget.last_upload is not None
    assert runtime.state.last_hash is None
    restored = FlashBudget()
    assert restored.restore(path)
    assert restored.uploads_today == 1


def test_successful_device_write_is_counted_then_persisted(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "flash-state.json"
    runtime = QuotaDeckRuntime(AppConfig(), flash_state_path=path)
    snapshot = _snapshot()
    runtime.state.severity[snapshot.key] = Severity.HEALTHY
    monkeypatch.setattr(runtime, "build_frames", lambda _snapshots: [object()])

    class SuccessfulDevice:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def upload_frames(self, _frames, *, progress):
            _ = progress

    monkeypatch.setattr("quotadeck.core.scheduler.F108Device", SuccessfulDevice)
    assert runtime.maybe_upload([snapshot]) == "uploaded 1 frames"

    assert runtime.budget.uploads_today == 1
    assert runtime.budget.last_upload is not None
    assert path.is_file()
    restored = FlashBudget()
    assert restored.restore(path)
    assert restored.uploads_today == 1


def test_stale_runtime_instances_cannot_overwrite_the_shared_daily_count(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "flash-state.json"
    seed = FlashBudget(daily_limit=2)
    seed.record(datetime.now(timezone.utc) - timedelta(hours=1))
    seed.persist(path)
    config = AppConfig(daily_flash_limit=2)
    first = QuotaDeckRuntime(config, flash_state_path=path)
    second = QuotaDeckRuntime(config, flash_state_path=path)
    snapshot = _snapshot()
    for runtime in (first, second):
        runtime.state.severity[snapshot.key] = Severity.HEALTHY
        monkeypatch.setattr(runtime, "build_frames", lambda _snapshots: [object()])

    class SuccessfulDevice:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def upload_frames(self, _frames, *, progress):
            _ = progress

    monkeypatch.setattr("quotadeck.core.scheduler.F108Device", SuccessfulDevice)

    assert first.maybe_upload([snapshot], force=True) == "uploaded 1 frames"
    assert second.maybe_upload([snapshot], force=True).startswith(
        "deferred: daily flash limit reached"
    )
    restored = FlashBudget()
    assert restored.restore(path)
    assert restored.uploads_today == 2


def test_persist_failure_blocks_device_before_any_flash_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "flash-state.json"
    runtime = QuotaDeckRuntime(AppConfig(), flash_state_path=path)
    snapshot = _snapshot()
    runtime.state.severity[snapshot.key] = Severity.HEALTHY
    monkeypatch.setattr(runtime, "build_frames", lambda _snapshots: [object()])
    monkeypatch.setattr(
        runtime.budget,
        "persist",
        lambda _path: (_ for _ in ()).throw(OSError("read-only")),
    )

    class UnexpectedDevice:
        def __enter__(self):
            raise AssertionError("device must not open without a reservation")

    monkeypatch.setattr("quotadeck.core.scheduler.F108Device", UnexpectedDevice)

    assert runtime.maybe_upload([snapshot]) == (
        "deferred: flash state could not be reserved"
    )
    assert runtime.budget.uploads_today == 0
    assert runtime.budget.last_upload is None
