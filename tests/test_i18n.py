from __future__ import annotations

from quotadeck.app.i18n import STRINGS, tr
from quotadeck.core.flashbudget import ACTIVE_HOURS_PER_DAY


def test_krw_gui_copy_explains_ten_thousand_won_kmb_scale() -> None:
    assert tr("ko", "currency_krw") == "KRW (만원)"
    assert tr("ko", "krw_unit_hint") == "1K = 천만원 · 1M = 백억 · 1B = 10조"
    assert tr("en", "currency_krw") == "KRW (10,000 won)"
    assert tr("en", "krw_unit_hint") == (
        "1K = 10 million won · 1M = 10 billion won · 1B = 10 trillion won"
    )


def test_flash_and_fx_copy_distinguishes_active_hours_and_treasury_rate() -> None:
    for lang in ("ko", "en"):
        flash = tr(lang, "flash_tip", hours=ACTIVE_HOURS_PER_DAY, daily=96, limit=100, years=2.9)
        assert str(ACTIVE_HOURS_PER_DAY) in flash
        assert "96" in flash
        assert "138" in flash
        hold = tr(lang, "hold_cycle_tip", hold=5, count=3, cycle=15)
        assert "15" in hold
        fx = tr(lang, "fx_auto_tip")
        assert "Treasury" in fx or "재무부" in fx
        assert "realtime" in fx.lower() or "현물" in fx
        smart = tr(lang, "mode_smart_tip")
        fixed = tr(lang, "mode_fixed_tip")
        assert smart
        assert fixed
        assert smart != fixed


def test_korean_and_english_tables_share_the_same_keys() -> None:
    assert set(STRINGS["ko"]) == set(STRINGS["en"])


def test_avg_missing_copy_states_history_thresholds() -> None:
    assert tr("ko", "avg_missing_daily", have=6, need=7) == "일간 완료 이력 6/7"
    assert tr("en", "avg_missing_daily", have=6, need=7) == (
        "Daily completed history 6/7"
    )
    assert "0/1" in tr("ko", "avg_missing_monthly", have=0, need=1)
    assert "0/1" in tr("en", "avg_missing_monthly", have=0, need=1)
    assert tr("ko", "avg_missing_current_incomplete")
    assert tr("en", "avg_missing_current_incomplete")
    assert tr("ko", "avg_missing_estimated_month", estimated=1) == (
        "추정 부분 월 1개는 완료 과거 월이 아닙니다"
    )
    assert "completed past months" in tr("en", "avg_missing_estimated_month", estimated=1)
    assert "141" in tr("ko", "playlist_budget_impossible", count=8, hold=20, required=320, limit=141)
    assert "141" in tr("en", "playlist_budget_impossible", count=8, hold=20, required=320, limit=141)
