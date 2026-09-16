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
