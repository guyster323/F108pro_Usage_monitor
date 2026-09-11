from datetime import datetime, timedelta, timezone

import pytest

from quotadeck.core.flashbudget import FlashBudget


def test_ten_minute_interval_matches_readme() -> None:
    budget = FlashBudget(min_interval=timedelta(minutes=10))
    assert budget.estimated_daily_writes() == 96
    assert budget.estimated_uncapped_daily_writes() == 96
    assert round(budget.estimated_years(), 1) == 2.9

def test_one_minute_without_cap_is_months_not_years() -> None:
    budget = FlashBudget(min_interval=timedelta(minutes=1))
    assert budget.estimated_uncapped_daily_writes() == 960
    months = budget.estimated_years(cap=False) * 12
    assert 3.0 <= months <= 4.0


@pytest.mark.parametrize(
    ("minutes", "expected_writes"),
    [(1, 960), (7, 138), (40, 24), (90, 11), (120, 8), (961, 1)],
)
def test_daily_estimate_uses_the_whole_active_day(minutes: int, expected_writes: int) -> None:
    budget = FlashBudget(
        min_interval=timedelta(minutes=minutes),
        daily_limit=10_000,
    )
    assert budget.estimated_uncapped_daily_writes() == expected_writes
    assert budget.estimated_daily_writes() == expected_writes


def test_daily_estimate_counts_the_immediate_first_upload() -> None:
    budget = FlashBudget(
        min_interval=timedelta(minutes=961),
        daily_limit=10_000,
    )
    assert budget.estimated_uncapped_daily_writes() == 1


def test_daily_estimate_is_independent_of_polling_interval() -> None:
    budget = FlashBudget(
        min_interval=timedelta(minutes=7),
        daily_limit=10_000,
    )
    assert budget.estimated_daily_writes(poll_seconds=1) == 138
    assert budget.estimated_daily_writes(poll_seconds=600) == 138


def test_safety_cap_is_separate_from_period_upper_bound() -> None:
    budget = FlashBudget(
        min_interval=timedelta(minutes=1),
        daily_limit=100,
    )
    assert budget.estimated_uncapped_daily_writes() == 960
    assert budget.estimated_daily_writes() == 100
    assert budget.estimated_years(cap=False) < budget.estimated_years(cap=True)

def test_faster_interval_shortens_uncapped_life() -> None:
    slow = FlashBudget(min_interval=timedelta(minutes=10)).estimated_years(cap=False)
    fast = FlashBudget(min_interval=timedelta(minutes=1)).estimated_years(cap=False)
    assert fast < slow


def test_flash_state_survives_atomic_save_and_restore(tmp_path) -> None:
    path = tmp_path / "state" / "flash-state.json"
    now = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
    budget = FlashBudget(min_interval=timedelta(minutes=10))
    budget.record(now)
    budget.record(now + timedelta(minutes=10))
    budget.persist(path)

    restored = FlashBudget(min_interval=timedelta(minutes=30))
    assert restored.restore(path, now + timedelta(minutes=11))
    assert restored.day == now.date()
    assert restored.uploads_today == 2
    assert restored.last_upload == now + timedelta(minutes=10)
    assert not list(path.parent.glob("*.tmp"))


def test_restored_daily_cap_cannot_be_bypassed_by_force(tmp_path) -> None:
    path = tmp_path / "flash-state.json"
    now = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
    budget = FlashBudget(daily_limit=1)
    budget.record(now)
    budget.persist(path)

    restored = FlashBudget(daily_limit=1)
    assert restored.restore(path, now + timedelta(hours=1))
    allowed, reason = restored.can_upload(now + timedelta(hours=1), force=True)
    assert not allowed
    assert reason == "daily flash limit reached (1)"


def test_flash_state_rolls_daily_count_after_restart(tmp_path) -> None:
    path = tmp_path / "flash-state.json"
    before_midnight = datetime(2026, 9, 11, 23, 59, tzinfo=timezone.utc)
    budget = FlashBudget()
    budget.record(before_midnight)
    budget.persist(path)

    restored = FlashBudget()
    assert restored.restore(path, before_midnight + timedelta(minutes=2))
    assert restored.uploads_today == 0
    assert restored.day == (before_midnight + timedelta(minutes=2)).date()
    assert restored.last_upload == before_midnight


def test_corrupt_flash_state_is_ignored(tmp_path) -> None:
    path = tmp_path / "flash-state.json"
    path.write_text("{not json", encoding="utf-8")
    budget = FlashBudget()
    assert not budget.restore(path)
    assert budget.last_upload is None
    assert budget.uploads_today == 0
