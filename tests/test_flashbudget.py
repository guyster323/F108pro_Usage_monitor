from datetime import timedelta

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

def test_faster_interval_shortens_uncapped_life() -> None:
    slow = FlashBudget(min_interval=timedelta(minutes=10)).estimated_years(cap=False)
    fast = FlashBudget(min_interval=timedelta(minutes=1)).estimated_years(cap=False)
    assert fast < slow
