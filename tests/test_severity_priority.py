from __future__ import annotations

from quotadeck.core.models import Severity
from quotadeck.core.severity import worst_severity


def test_critical_not_hidden_by_healthy() -> None:
    assert worst_severity([Severity.HEALTHY, Severity.CRITICAL]) == Severity.CRITICAL


def test_exhausted_beats_everything() -> None:
    assert worst_severity(list(Severity)) == Severity.EXHAUSTED


def test_error_visible_over_healthy() -> None:
    assert worst_severity([Severity.HEALTHY, Severity.ERROR]) == Severity.ERROR


def test_stale_visible_over_caution_order_is_explicit() -> None:
    assert worst_severity([Severity.CAUTION, Severity.STALE]) == Severity.STALE


def test_empty_returns_none() -> None:
    assert worst_severity([]) is None
    assert worst_severity([None]) is None
