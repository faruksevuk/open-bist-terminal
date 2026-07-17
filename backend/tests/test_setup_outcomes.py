"""Canlı sinyal sonuç-takibi (OOS) birim testleri — sentetik (ağ/DB gerektirmez).

Doğrulanan garantiler:
- giriş = tetik barından SONRAKİ ilk barın OPEN'ı,
- target gün-3'te → 'target', doğru R,
- stop gün-2'de → 'stop', R ≈ -1,
- ne stop ne target → 'time_exit' (çıkış barı close'u),
- yeterli bar yok → 'pending' (kısmi),
- stop & target AYNI barda → stop-önce (muhafazakâr),
- beklenti (expectancy) matematiği bilinen girdilerle.
Hızlı (<5s).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.backtest.event_study import simulate_trade_detail
from app.engine.setup_outcomes import (
    _agg_group,
    _evaluate_one,
    _weeks_span,
    outcome_summary,
)


def _bars(dates, opens, highs, lows, closes):
    """OHLC DataFrame (tarih-indeksli; load_daily çıktısı gibi)."""
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=idx,
    ).astype(float)


def _sig(**kw):
    """Hafif SetupSignal stub (yalnız _evaluate_one'ın okuduğu alanlar)."""
    base = dict(
        id=1, ticker="TEST", setup="snapback",
        triggered_at=date(2023, 1, 2), stop=95.0, target=110.0, time_exit_days=5,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- entry = tetik-sonrası ilk bar OPEN ---------------------------------

def test_entry_is_first_bar_open_after_trigger():
    # tetik 01-02; giriş 01-03 open=100
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
    bars = _bars(dates, [99, 100, 101, 102], [99.5, 101, 102, 103],
                 [98.5, 99, 100, 101], [99, 100.5, 101.5, 102.5])
    row = _evaluate_one(_sig(target=200.0, time_exit_days=2), bars)  # target uzak → time_exit
    assert row["entry_date"] == date(2023, 1, 3)
    assert abs(row["entry_price"] - 100.0) < 1e-9


# --- target gün-3'te → 'target', doğru R --------------------------------

def test_target_hit_day3():
    # giriş 01-03 open=100, stop=95 (risk=5), target=110. 3. işlem barında (01-05) high>=110.
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"]
    opens = [99, 100, 101, 102, 103]
    highs = [99.5, 101, 104, 111, 112]   # 01-05 high=111 >= target
    lows = [98.5, 99, 100, 101, 102]     # stop hiç vurulmadı
    closes = [99, 100.5, 103, 110.5, 111]
    bars = _bars(dates, opens, highs, lows, closes)
    row = _evaluate_one(_sig(stop=95.0, target=110.0, time_exit_days=5), bars)
    assert row["status"] == "target"
    assert abs(row["realized_r"] - 2.0) < 1e-9  # (110-100)/5 = +2R
    assert row["exit_date"] == date(2023, 1, 5)
    assert row["days_held"] == 2  # entry_pos=1 (01-03) → exit_pos=3 (01-05): 2 bar


# --- stop gün-2'de → 'stop', R ≈ -1 -------------------------------------

def test_stop_hit_day2():
    # giriş 01-03 open=100, stop=95. 2. işlem barında (01-04) low<=95.
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
    opens = [99, 100, 99, 98]
    highs = [99.5, 101, 100, 99]
    lows = [98.5, 99, 94, 90]     # 01-04 low=94 <= stop 95
    closes = [99, 100, 96, 92]
    bars = _bars(dates, opens, highs, lows, closes)
    row = _evaluate_one(_sig(stop=95.0, target=110.0, time_exit_days=5), bars)
    assert row["status"] == "stop"
    assert abs(row["realized_r"] - (-1.0)) < 1e-9  # (95-100)/5 = -1R
    assert row["exit_date"] == date(2023, 1, 4)
    assert row["days_held"] == 1


# --- ne stop ne target → 'time_exit' ------------------------------------

def test_time_exit_close():
    # giriş 01-03 open=100, stop=95, target=200 (ulaşılmaz), time_exit=3.
    # çıkış barı = entry_pos(1)+3 = pos 4 → close orada.
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"]
    opens = [99, 100, 101, 102, 103]
    highs = [99.5, 102, 103, 104, 105]
    lows = [98.5, 98, 99, 100, 101]      # stop 95 hiç vurulmadı
    closes = [99, 100, 101, 102, 103]    # pos4 close=103
    bars = _bars(dates, opens, highs, lows, closes)
    row = _evaluate_one(_sig(stop=95.0, target=200.0, time_exit_days=3), bars)
    assert row["status"] == "time_exit"
    # R = (103 - 100) / 5 = 0.6
    assert abs(row["realized_r"] - 0.6) < 1e-9
    assert row["exit_date"] == date(2023, 1, 6)
    assert row["days_held"] == 3


# --- yeterli bar yok → 'pending' ----------------------------------------

def test_pending_not_enough_bars():
    # giriş oldu (01-03) ama time_exit=5 → çıkış barı henüz yok; stop/target de vurulmadı.
    dates = ["2023-01-02", "2023-01-03", "2023-01-04"]
    opens = [99, 100, 101]
    highs = [99.5, 101, 102]
    lows = [98.5, 99, 100]
    closes = [99, 100, 101]
    bars = _bars(dates, opens, highs, lows, closes)
    row = _evaluate_one(_sig(stop=95.0, target=200.0, time_exit_days=5), bars)
    assert row["status"] == "pending"
    assert row["entry_date"] == date(2023, 1, 3)  # giriş oldu
    assert row["realized_r"] is None
    assert row["exit_date"] is None


def test_pending_no_bar_after_trigger():
    # tetik-sonrası HİÇ bar yok → giriş beklemede (BIST bugün kapanmamış senaryosu).
    dates = ["2023-01-01", "2023-01-02"]
    bars = _bars(dates, [99, 100], [100, 101], [98, 99], [99.5, 100])
    row = _evaluate_one(_sig(triggered_at=date(2023, 1, 2)), bars)  # tetik son bar
    assert row["status"] == "pending"
    assert row["entry_date"] is None


# --- stop & target AYNI barda → stop-önce -------------------------------

def test_stop_and_target_same_bar_stop_first():
    # giriş 01-03 open=100, stop=95, target=110. 01-04 barında low=94 (stop) VE high=111 (target).
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
    opens = [99, 100, 100, 100]
    highs = [99.5, 101, 111, 101]    # 01-04 high=111 (target)
    lows = [98.5, 99, 94, 99]        # 01-04 low=94 (stop) → aynı bar
    closes = [99, 100, 100, 100]
    bars = _bars(dates, opens, highs, lows, closes)
    row = _evaluate_one(_sig(stop=95.0, target=110.0, time_exit_days=5), bars)
    assert row["status"] == "stop"  # muhafazakâr: stop-önce
    assert abs(row["realized_r"] - (-1.0)) < 1e-9


# --- realized_pct doğru -------------------------------------------------

def test_realized_pct():
    dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
    bars = _bars(dates, [99, 100, 101, 102], [99.5, 101, 111, 103],
                 [98.5, 99, 100, 101], [99, 100, 110, 102])
    row = _evaluate_one(_sig(stop=95.0, target=110.0, time_exit_days=5), bars)
    assert row["status"] == "target"
    # exit_price = target = 110; entry = 100 → pct = 0.10
    assert abs(row["realized_pct"] - 0.10) < 1e-9


# --- simulate_trade_detail no_entry (bozuk stop) ------------------------

def test_no_entry_when_entry_at_or_below_stop():
    # giriş open <= stop → geçersiz (risk<=0) → no_entry
    o = pd.Series([100.0, 90.0, 90.0])
    h = pd.Series([101.0, 91.0, 91.0])
    low = pd.Series([99.0, 89.0, 89.0])
    c = pd.Series([100.0, 90.0, 90.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0, time_exit_days=3)
    assert res.status == "no_entry"


# --- toplulaştırma (aggregate) ------------------------------------------

def _oc(status, r=None, pct=None, held=None, setup="snapback", trig=date(2023, 1, 2)):
    return SimpleNamespace(status=status, realized_r=r, realized_pct=pct, days_held=held,
                           setup=setup, triggered_at=trig)


def test_agg_group_counts_and_means():
    rows = [
        _oc("target", r=2.0, pct=0.10, held=3),
        _oc("stop", r=-1.0, pct=-0.05, held=1),
        _oc("time_exit", r=0.5, pct=0.02, held=5),
        _oc("pending"),
        _oc("no_entry"),
    ]
    d = _agg_group(rows)
    assert d["n_closed"] == 3
    assert d["n_pending"] == 1
    assert d["n_no_entry"] == 1
    assert d["n_target"] == 1 and d["n_stop"] == 1 and d["n_time_exit"] == 1
    # isabet = R>0 oranı → 2/3
    assert abs(d["isabet"] - round(2 / 3, 3)) < 1e-9
    # ort R = (2 - 1 + 0.5)/3 = 0.5
    assert abs(d["ort_r"] - 0.5) < 1e-9
    assert abs(d["toplam_r"] - 1.5) < 1e-9
    assert abs(d["medyan_r"] - 0.5) < 1e-9
    assert abs(d["ort_gun"] - 3.0) < 1e-9


def test_agg_group_net_zero_cost_equals_gross():
    """cost_pct=0 (varsayılan) → net alanları gross ile birebir."""
    rows = [
        _oc("target", r=2.0, pct=0.10, held=3),
        _oc("stop", r=-1.0, pct=-0.05, held=1),
    ]
    d = _agg_group(rows)  # cost_pct varsayılan 0
    assert abs(d["ort_r"] - d["ort_r_net"]) < 1e-9
    assert abs(d["toplam_r"] - d["toplam_r_net"]) < 1e-9
    assert abs(d["ort_pct"] - d["ort_pct_net"]) < 1e-9


def test_agg_group_net_with_cost():
    """Bilinen gross → beklenen net (round-trip 0.0028).

    target: r=2.0, pct=0.10 → cost_R = 0.0028×2/0.10 = 0.056 → net_r=1.944; net_pct=0.0972.
    stop:   r=-1.0, pct=-0.05 → cost_R = 0.0028×(-1)/(-0.05) = 0.056 → net_r=-1.056; net_pct=-0.0528.
    toplam_r_net = 1.944 + (-1.056) = 0.888.
    """
    rows = [
        _oc("target", r=2.0, pct=0.10, held=3),
        _oc("stop", r=-1.0, pct=-0.05, held=1),
    ]
    d = _agg_group(rows, cost_pct=0.0028)
    assert abs(d["toplam_r"] - 1.0) < 1e-9            # gross 2 - 1
    assert abs(d["toplam_r_net"] - 0.888) < 1e-9      # net türetilmiş
    assert abs(d["ort_r_net"] - 0.444) < 1e-9
    assert d["ort_r_net"] < d["ort_r"]                # maliyet net'i düşürür


def test_agg_group_net_infeasible_excluded():
    """Maliyet-fizibilite: risk_frac<=cost olan işlem net-R toplamasından düşer (net_r=None).

    trade A: r=2.0, pct=0.10 → risk_frac=0.05 > 0.0028 → feasible (net_r=1.944).
    trade B: r=-1.0, pct=-0.001 → risk_frac=0.001 < 0.0028 → INFEASIBLE (net_r=None).
    → net-R toplaması yalnız A'yı içerir; gross ikisini de içerir.
    """
    rows = [
        _oc("target", r=2.0, pct=0.10, held=3),
        _oc("stop", r=-1.0, pct=-0.001, held=1),   # stop entry'ye maliyetten yakın
    ]
    d = _agg_group(rows, cost_pct=0.0028)
    assert d["n_closed"] == 2                          # gross her iki işlemi sayar
    assert abs(d["toplam_r"] - 1.0) < 1e-9            # gross 2 - 1
    assert abs(d["toplam_r_net"] - 1.944) < 1e-9      # net yalnız feasible A
    assert abs(d["ort_r_net"] - 1.944) < 1e-9         # net ort = tek feasible işlem


def test_agg_group_empty():
    d = _agg_group([])
    assert d["n_closed"] == 0
    assert d["isabet"] is None
    assert d["ort_r"] is None
    assert d["toplam_r"] is None
    assert d["ort_r_net"] is None
    assert d["toplam_r_net"] is None


def test_weeks_span():
    rows = [
        _oc("target", r=1.0, trig=date(2023, 1, 2)),
        _oc("stop", r=-1.0, trig=date(2023, 1, 16)),  # 14 gün sonra → 2 hafta
    ]
    assert abs(_weeks_span(rows) - 2.0) < 1e-9
    # tek işlem → 1 hafta tabanı (sıfıra bölme yok)
    assert abs(_weeks_span([_oc("target", r=1.0)]) - 1.0) < 1e-9


# --- beklenti (expectancy) matematiği (fake session) --------------------

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """outcome_summary yalnız select(SetupOutcome) çalıştırır + get_config('risk') okur."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_k):
        return _FakeResult(self._rows)

    def get(self, *_a, **_k):
        return None


# key-farkında monkeypatch: base_r için risk config; costs için verilen maliyet dict;
# goals için sabit %10 hedef (test matematiği hedef-bağımsız kalsın — hedef artık config'ten).
def _cfg_stub(cost_dict):
    def _get(_session, key):
        if key == "costs":
            return cost_dict
        if key == "goals":
            return {"target_weekly_pct": 10.0}
        return {"base_r": 0.01}
    return _get


def test_expectancy_math(monkeypatch):
    import app.engine.setup_outcomes as mod

    # SIFIR maliyet → net == gross (eski davranış korunur; deterministik).
    monkeypatch.setattr(mod, "get_config", _cfg_stub(
        {"commission_pct_per_side": 0.0, "spread_slippage_pct_per_side": 0.0}))

    # 4 kapalı işlem, sum_r = 2 -1 +0.5 +0.5 = 2.0; triggered span 14 gün → 2 hafta.
    rows = [
        _oc("target", r=2.0, pct=0.10, held=3, trig=date(2023, 1, 2)),
        _oc("stop", r=-1.0, pct=-0.05, held=1, trig=date(2023, 1, 4)),
        _oc("time_exit", r=0.5, pct=0.02, held=5, trig=date(2023, 1, 10)),
        _oc("target", r=0.5, pct=0.02, held=2, trig=date(2023, 1, 16)),
    ]
    summ = outcome_summary(_FakeSession(rows))
    e = summ["expectancy"]
    assert e["risk_per_trade"] == 0.01
    assert e["n_closed"] == 4
    # weeks = 14/7 = 2.0; measured_r_per_week = 2.0/2 = 1.0
    assert abs(e["weeks_span"] - 2.0) < 1e-9
    assert abs(e["measured_r_per_week"] - 1.0) < 1e-9
    # SIFIR maliyet → net == gross
    assert abs(e["measured_r_per_week_net"] - 1.0) < 1e-9
    # expected_weekly_pct = 1.0 * 0.01 * 100 = 1.0
    assert abs(e["expected_weekly_pct"] - 1.0) < 1e-9
    assert abs(e["expected_weekly_pct_net"] - 1.0) < 1e-9
    # needed_r_per_week = 0.10 / 0.01 = 10 (maliyeti YOK SAYAR)
    assert abs(e["needed_r_per_week"] - 10.0) < 1e-9
    assert e["target_weekly_pct"] == 10.0
    assert e["needed_ignores_cost"] is True
    # gap = 10 - 1(net) = 9
    assert abs(e["gap"] - 9.0) < 1e-9
    assert "hafta" in e["gap_note"]


def test_expectancy_net_below_gross(monkeypatch):
    """POZİTİF maliyet → net R/pct brütün ALTINDA; gap büyür; verdict-gerekliliği maliyeti yok sayar."""
    import app.engine.setup_outcomes as mod

    # round-trip = 2×(0.0004+0.0010) = 0.0028 → net_pct = gross_pct − 0.0028.
    monkeypatch.setattr(mod, "get_config", _cfg_stub(
        {"commission_pct_per_side": 0.0004, "spread_slippage_pct_per_side": 0.0010}))

    # Tek kapalı işlem, bilinen gross: r=2.0, pct=0.10, tek gün → 1 hafta tabanı.
    #   risk_fraksiyonu = pct/r = 0.10/2.0 = 0.05; cost_R = cost_pct × r/pct = 0.0028×2/0.10 = 0.056
    #   net_r = 2.0 − 0.056 = 1.944; net_pct = 0.10 − 0.0028 = 0.0972.
    rows = [_oc("target", r=2.0, pct=0.10, held=3, trig=date(2023, 1, 2))]
    summ = outcome_summary(_FakeSession(rows))
    ov, e = summ["overall"], summ["expectancy"]
    assert abs(ov["ort_r"] - 2.0) < 1e-9         # gross korunur
    assert abs(ov["ort_r_net"] - 1.944) < 1e-6   # net < gross
    assert abs(ov["ort_pct_net"] - 0.0972) < 1e-6
    # net measured < gross measured; gap NET'e dayanır → daha büyük
    assert e["measured_r_per_week_net"] < e["measured_r_per_week"]
    assert abs(summ["round_trip_cost_pct"] - 0.0028) < 1e-9
    assert "NET" in e["gap_note"]


def test_expectancy_empty(monkeypatch):
    import app.engine.setup_outcomes as mod
    monkeypatch.setattr(mod, "get_config", _cfg_stub(
        {"commission_pct_per_side": 0.0004, "spread_slippage_pct_per_side": 0.0010}))
    summ = outcome_summary(_FakeSession([]))
    e = summ["expectancy"]
    assert e["n_closed"] == 0
    assert e["measured_r_per_week"] == 0.0
    assert e["measured_r_per_week_net"] == 0.0
    assert e["expected_weekly_pct"] == 0.0
    assert e["expected_weekly_pct_net"] == 0.0
    assert "Henüz kapanan sinyal yok" in e["gap_note"]
    assert np.isfinite(e["needed_r_per_week"])
