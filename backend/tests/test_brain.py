"""brain._stance_from_signal — sistem sinyali → deterministik duruş (SAF, DB'siz)."""

from app.llm.brain import _stance_from_signal


def test_sell_is_cik():
    assert _stance_from_signal("sell", 10) == "cik"


def test_reduce_is_azalt():
    assert _stance_from_signal("reduce", 40) == "azalt"


def test_buy_is_koru():
    assert _stance_from_signal("buy", 70) == "koru"
    assert _stance_from_signal("strong_buy", 80) == "koru"


def test_hold_splits_on_score():
    assert _stance_from_signal("hold", 60) == "koru"   # güçlü hold → koru
    assert _stance_from_signal("hold", 40) == "izle"   # zayıf hold → izle


def test_none_signal_is_izle():
    assert _stance_from_signal(None, None) == "izle"
