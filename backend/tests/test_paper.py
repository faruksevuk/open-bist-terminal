"""Kâğıt-portföy saf mantığı: çıkış kuralları + sinyal-stop sizing (DB'siz)."""

from app.engine.paper_trader import _exit_check
from app.risk.sizing import position_size

RISK = {"base_r": 0.01, "k_atr": 2.0, "max_name_pct": 0.30, "max_heat_pct": 0.06}


def test_exit_stop_first_when_both_hit():
    # aynı barda low<=stop VE high>=target → STOP sayılır (muhafazakâr, event-study kuralı)
    res = _exit_check(entry=100, stop=95, target=110, bars_held=1, time_exit_days=5,
                      o=100, h=111, low=94, c=105)
    assert res == ("stop", 95)


def test_exit_gap_through_open_fill():
    # açılış stopun altında → open'dan fill (stop fiyatından değil)
    res = _exit_check(100, 95, 110, 1, 5, o=92, h=93, low=90, c=91)
    assert res == ("stop", 92)


def test_exit_target():
    res = _exit_check(100, 95, 110, 2, 5, o=105, h=112, low=104, c=111)
    assert res == ("target", 110)


def test_exit_time_at_close():
    res = _exit_check(100, 95, 110, 5, 5, o=101, h=103, low=100, c=102)
    assert res == ("time_exit", 102)


def test_no_exit():
    assert _exit_check(100, 95, 110, 2, 5, o=101, h=103, low=99, c=102) is None


def test_sizing_with_plan_stop_targets_base_r():
    # sinyal stopu 96 → hisse başı risk 4; %1 base_r × 100k = 1000 → 250 lot
    sz = position_size(100_000, 100.0, 4.0, RISK, plan_stop=96.0)
    assert sz["valid"] and sz["qty"] == 250
    assert abs(sz["risk_amount"] - 1000) < 1e-6
    assert sz["stop"] == 96.0


def test_sizing_plan_stop_above_entry_invalid():
    sz = position_size(100_000, 100.0, 4.0, RISK, plan_stop=101.0)
    assert not sz["valid"]


def test_sizing_plan_stop_respects_heat():
    # max_name serbest bırakılır (1.0) ki HEAT bağını izole test edelim:
    # base_r %1 → 1000/2 = 500 lot; kalan heat %1 → 1000/2 = 500 → tam sınırda, aşmaz.
    sz = position_size(100_000, 100.0, 0.0, {**RISK, "max_heat_pct": 0.02, "max_name_pct": 1.0},
                       open_heat_pct=0.01, plan_stop=98.0)
    assert sz["qty"] == 500
    assert sz["heat_after"] <= 0.02 + 1e-9

    # kalan heat %0.5'e düşerse heat KIRPAR: 500/2=250 lot, capped_by=heat
    sz2 = position_size(100_000, 100.0, 0.0, {**RISK, "max_heat_pct": 0.02, "max_name_pct": 1.0},
                        open_heat_pct=0.015, plan_stop=98.0)
    assert sz2["qty"] == 250 and sz2["capped_by"] == "heat"
