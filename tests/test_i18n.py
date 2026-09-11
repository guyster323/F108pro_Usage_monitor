from __future__ import annotations

from quotadeck.app.i18n import tr


def test_krw_gui_copy_explains_ten_thousand_won_kmb_scale() -> None:
    assert tr("ko", "currency_krw") == "KRW (만원)"
    assert tr("ko", "krw_unit_hint") == "1K = 천만원 · 1M = 백억 · 1B = 10조"
    assert tr("en", "currency_krw") == "KRW (10,000 won)"
    assert tr("en", "krw_unit_hint") == (
        "1K = 10 million won · 1M = 10 billion won · 1B = 10 trillion won"
    )
