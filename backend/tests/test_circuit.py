"""Devre kesici saf matematiği — evaluate_circuit (DB'siz)."""

from app.risk.circuit import evaluate_circuit


def test_no_trip_flat():
    st = evaluate_circuit(100_000, 100_000, 100_000, 0.03, 0.10)
    assert st["tripped"] == [] and not st["active"]
    assert st["daily_ret"] == 0.0 and st["weekly_dd"] == 0.0


def test_daily_trip():
    # gün-başı 100k → 96.9k = -%3.1 ≤ -%3 → daily atar
    st = evaluate_circuit(96_900, 100_000, 100_000, 0.03, 0.10)
    assert st["tripped"] == ["daily"] and st["active"]


def test_daily_not_tripped_just_above():
    st = evaluate_circuit(97_100, 100_000, 100_000, 0.03, 0.10)
    assert st["tripped"] == []


def test_weekly_trip_from_week_high():
    # hafta tepe 110k → 98k = -%10.9 tepe-düşüş → weekly atar (gün-içi düz olsa bile)
    st = evaluate_circuit(98_000, 98_000, 110_000, 0.03, 0.10)
    assert st["tripped"] == ["weekly"]


def test_both_trip():
    st = evaluate_circuit(89_000, 93_000, 100_000, 0.03, 0.10)
    assert st["tripped"] == ["daily", "weekly"]


def test_zero_thresholds_disable():
    # 0 eşik = kesici kapalı (config'te bilinçli kapatma)
    st = evaluate_circuit(50_000, 100_000, 100_000, 0.0, 0.0)
    assert st["tripped"] == []


def test_missing_bases_safe():
    st = evaluate_circuit(100_000, None, None, 0.03, 0.10)
    assert st["tripped"] == [] and st["daily_ret"] == 0.0
