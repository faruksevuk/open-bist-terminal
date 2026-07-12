"""Olay-çalışması (event study) birim testleri — sentetik (ağ/DB gerektirmez).

Test edilen dürüstlük garantileri:
- point-in-time olay toplama (yalnız t'ye kadarki veri; gelecek veriyle tetiklenmez),
- örtüşme baskılama (aynı setup, horizon içinde yeni olay yok),
- günlük kümeleme (aynı gün olayları önce ortalanır),
- verdict eşik mantığı,
- stop-önce işlem simülasyonu (muhafazakâr).
Hızlı (<10s).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.event_study import (
    _aggregate,
    _simulate_trade,
    _universe_fwd_medians,
    _verdict,
    simulate_trade_detail,
)
from app.engine.indicators import compute_indicators
from app.engine.setups import MarketContext, detect_snapback


def _frame(closes, start="2023-01-01"):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99,
         "close": closes, "adj_close": closes, "volume": np.full(len(closes), 1_000_000.0)},
        index=idx,
    )


# --- verdict eşik mantığı -----------------------------------------------

def test_verdict_thresholds():
    # kanıtlı: n>=30, mean>0, t>=1.5
    assert _verdict(40, 0.01, 2.0) == "kanıtlı"
    # zayıf: n>=30, mean>0, t<1.5
    assert _verdict(40, 0.01, 1.0) == "zayıf"
    # deneysel: n<30
    assert _verdict(10, 0.02, 3.0) == "deneysel"
    # devre dışı: mean<=0 (negatif edge)
    assert _verdict(50, -0.005, 1.0) == "devre dışı"
    assert _verdict(50, 0.0, 5.0) == "devre dışı"
    # sınır: tam 30, t tam 1.5 → kanıtlı
    assert _verdict(30, 0.001, 1.5) == "kanıtlı"


# --- stop-önce işlem simülasyonu ----------------------------------------

def test_simulate_trade_stop_first_when_both_hit():
    # giriş t+1 open. Bir barda hem stop hem target vurulursa ÖNCE STOP sayılır (muhafazakâr).
    # entry=100, stop=95, target=110. Bir bar low=94 (stop) VE high=111 (target) → R=-1.
    closes = [100.0] * 10
    common = list(pd.date_range("2023-01-01", periods=10, freq="B"))
    panel = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [101.0, 111.0] + [101.0] * 8,   # entry barı(i+1=1) hem stop hem target
        "low": [100.0, 94.0] + [100.0] * 8,
        "close": closes,
    }, index=common)
    res = {"stop": 95.0, "target": 110.0, "time_exit_days": 5}
    r, r_net = _simulate_trade(panel, common, i=0, res=res)  # giriş j=1 (maliyet 0 → net==gross)
    assert r is not None and abs(r - (-1.0)) < 1e-9  # stop-önce → -1R
    assert r_net is not None and abs(r_net - (-1.0)) < 1e-9  # cost_pct=0 → net==gross


def test_simulate_trade_target_hit():
    common = list(pd.date_range("2023-01-01", periods=10, freq="B"))
    panel = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [101.0, 101.0, 111.0] + [101.0] * 7,  # 3. barda target
        "low": [100.0] * 10,
        "close": [100.0] * 10,
    }, index=common)
    res = {"stop": 95.0, "target": 110.0, "time_exit_days": 5}
    r, _r_net = _simulate_trade(panel, common, i=0, res=res)
    assert r is not None and abs(r - 2.0) < 1e-9  # +2R (target)


def test_simulate_trade_time_exit():
    # ne stop ne target → zaman-çıkışında close ile R
    common = list(pd.date_range("2023-01-01", periods=10, freq="B"))
    panel = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [102.0] * 10, "low": [98.0] * 10,
        "close": [100, 100, 101, 102, 103, 104, 105, 100, 100, 100],
    }, index=common)
    # entry=open[1]=100, stop=95 (risk=5), time_exit=3 → çıkış barı = 1+3=4, close=103 → R=(103-100)/5=0.6
    res = {"stop": 95.0, "target": 200.0, "time_exit_days": 3}
    r, _r_net = _simulate_trade(panel, common, i=0, res=res)
    assert r is not None and abs(r - 0.6) < 1e-9


# --- işlem maliyeti (net-of-cost) ---------------------------------------

def test_cost_zero_default_net_equals_gross():
    """round_trip_cost_pct=0 (varsayılan) → net == gross; eski davranış değişmez."""
    o = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    h = pd.Series([101.0, 101.0, 111.0, 101.0, 101.0, 101.0])  # 3. barda target
    low = pd.Series([100.0] * 6)
    c = pd.Series([100.0] * 6)
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0, time_exit_days=5)
    assert res.status == "target"
    assert abs(res.r_multiple - 2.0) < 1e-9        # gross +2R
    assert abs(res.r_multiple_net - 2.0) < 1e-9    # net == gross (maliyet 0)
    assert abs(res.pct - 0.10) < 1e-9
    assert abs(res.pct_net - 0.10) < 1e-9


def test_cost_known_gross_expected_net():
    """Bilinen gross → beklenen net R/pct verilen maliyette.

    entry=100, stop=95 (risk=5), target=110 → gross R=+2.0, gross pct=(110-100)/100=0.10.
    round_trip=0.0028: cost_R = 0.0028 × entry/(entry-stop) = 0.0028 × 100/5 = 0.056.
    net_R = 2.0 − 0.056 = 1.944; net_pct = 0.10 − 0.0028 = 0.0972.
    """
    o = pd.Series([100.0, 100.0, 100.0, 100.0])
    h = pd.Series([101.0, 111.0, 101.0, 101.0])   # 1. işlem barında (j=1) target
    low = pd.Series([100.0] * 4)
    c = pd.Series([100.0] * 4)
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=5, round_trip_cost_pct=0.0028)
    assert res.status == "target"
    assert abs(res.r_multiple - 2.0) < 1e-9          # gross korunur
    assert abs(res.r_multiple_net - 1.944) < 1e-9    # net R
    assert abs(res.pct - 0.10) < 1e-9
    assert abs(res.pct_net - 0.0972) < 1e-9          # net pct


def test_cost_stop_net_worse_than_minus_one():
    """Stop'ta gross=-1R; maliyet net'i -1'in ALTINA (daha kötü) iter."""
    o = pd.Series([100.0, 100.0, 100.0])
    h = pd.Series([101.0, 101.0, 101.0])
    low = pd.Series([100.0, 94.0, 100.0])   # j=1 barında stop (94 <= 95)
    c = pd.Series([100.0, 96.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=5, round_trip_cost_pct=0.0028)
    assert res.status == "stop"
    assert abs(res.r_multiple - (-1.0)) < 1e-9       # gross -1R
    # cost_R = 0.0028 × 100/5 = 0.056 → net = -1.056
    assert abs(res.r_multiple_net - (-1.056)) < 1e-9
    assert res.r_multiple_net < -1.0                  # maliyet net'i kötüleştirir


def test_gap_through_stop_fills_at_open_worse():
    """Bar stop'un ALTINDA açılırsa (gap-down/limit) fill = açılış, stop DEĞİL — kayıp
    −1R'den daha kötü (taban kilidi realizmi; stop koruması iş görmez)."""
    o = pd.Series([100.0, 100.0, 90.0, 100.0])   # j=1 giriş; k=2 barı 90'da açılıyor (gap-down)
    h = pd.Series([101.0, 101.0, 92.0, 101.0])
    low = pd.Series([100.0, 100.0, 88.0, 100.0])  # k=2 low 88 <= stop 95
    c = pd.Series([100.0, 100.0, 89.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0, time_exit_days=5)
    assert res.status == "stop"
    # fill = 90 (açılış, stop 95 değil) → R = (90-100)/(100-95) = -2.0 (stop'tan kötü)
    assert abs(res.r_multiple - (-2.0)) < 1e-9
    assert res.r_multiple < -1.0


def test_intraday_stop_touch_fills_at_stop():
    """Bar stop ÜSTÜNDE açılıp gün içi stop'a dokunursa fill = stop (gap yok, klasik −1R)."""
    o = pd.Series([100.0, 100.0, 100.0, 100.0])   # k=2 barı 100'de açık (stop üstü)
    h = pd.Series([101.0, 101.0, 101.0, 101.0])
    low = pd.Series([100.0, 100.0, 94.0, 100.0])  # gün içi 94'e indi (stop 95)
    c = pd.Series([100.0, 100.0, 96.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0, time_exit_days=5)
    assert res.status == "stop"
    assert abs(res.r_multiple - (-1.0)) < 1e-9   # fill stop'ta → tam −1R


def test_cost_infeasible_tiny_risk_net_none():
    """Stop entry'ye maliyetten daha yakınsa (risk_frac <= cost) → net_R anlamsız (None).

    entry=100, stop=99.9 → risk_frac=0.001 < round-trip 0.0028 → maliyet 1R'yi aşar → girilemez.
    GROSS ve pct_net dokunulmaz; yalnız r_multiple_net None (net-R toplamasından düşer).
    """
    o = pd.Series([100.0, 100.0, 100.0])
    h = pd.Series([101.0, 101.0, 101.0])
    low = pd.Series([100.0, 99.85, 100.0])   # j=1 barında stop (99.85 <= 99.9)
    c = pd.Series([100.0, 99.9, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=99.9, target=110.0,
                                time_exit_days=5, round_trip_cost_pct=0.0028)
    assert res.status == "stop"
    assert abs(res.r_multiple - (-1.0)) < 1e-9        # gross -1R (bozulmadı)
    assert res.r_multiple_net is None                  # maliyet-fizibilite: girilemez
    assert res.pct_net is not None                     # pct-uzay maliyet yine yansır


# --- point-in-time: gelecek veriyle tetiklenmemeli ----------------------

def test_pit_no_lookahead():
    """Dedektör i barında YALNIZ i'ye kadarki veriyle çalışmalı: aynı i'de, i sonrası
    barları değiştirmek sonucu ETKİLEMEMELİ (look-ahead yok)."""
    # snapback tetikleyen taban: güçlü yükseliş + oversold pullback + YEŞİL dönüş mumu
    # (yeni ema50_floor_pct guard'ıyla uyumlu; open=close düz frame tetiklemez).
    base = np.linspace(60.0, 118.0, 232)
    dip = [116.0, 113.0, 110.0, 106.0, 102.0, 104.5]
    closes = np.concatenate([base, dip]).astype(float)
    opens = closes.copy()
    opens[-1] = 102.5                    # tetik barı YEŞİL
    i = len(closes) - 1                  # tetik barı (gelecekten önce)

    def _panel(future_close: float):
        # i sonrası 20 bar ekle (farklı gelecek); tetik barı (i) ve öncesi DEĞİŞMEZ
        c = np.concatenate([closes, np.full(20, future_close)])
        o = np.concatenate([opens, np.full(20, future_close)])
        hi = np.maximum(c, o) * 1.01
        lo = np.minimum(c, o) * 0.99
        idx = pd.date_range("2023-01-01", periods=len(c), freq="B")
        return compute_indicators(pd.DataFrame(
            {"open": o, "high": hi, "low": lo, "close": c, "adj_close": c,
             "volume": np.full(len(c), 1_000_000.0)}, index=idx))

    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5,
                        cross={"roc3_p10": -0.02, "roc3_min": -0.15})
    r_up = detect_snapback(_panel(200.0), i, ctx)   # yukarı-fırlayan gelecek
    r_dn = detect_snapback(_panel(10.0), i, ctx)    # çöken gelecek
    assert r_up is not None and r_dn is not None
    # aynı i, aynı geçmiş → giriş/stop/hedef gelecek barlardan BAĞIMSIZ olmalı
    assert abs(r_up["entry_ref"] - r_dn["entry_ref"]) < 1e-9
    assert abs(r_up["stop"] - r_dn["stop"]) < 1e-9
    assert abs(r_up["target"] - r_dn["target"]) < 1e-9


# --- universe forward medyanları ----------------------------------------

def test_universe_fwd_medians_shape():
    panels = {
        "A": _frame(np.linspace(100, 120, 60)),
        "B": _frame(np.linspace(100, 90, 60)),
        "C": _frame(np.full(60, 100.0)),
    }
    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    med = _universe_fwd_medians(panels, common)
    # h=5 için bir orta indekste medyan mevcut olmalı
    assert 30 in med and 5 in med[30]
    assert isinstance(med[30][5], float)


# --- günlük kümeleme + aggregate + overlap ------------------------------

def _make_events(dates, excesses5, R=0.5):
    """Basit olay listesi kurucusu (aggregate testleri için)."""
    evs = []
    for d, e in zip(dates, excesses5):
        evs.append({
            "ticker": "X", "date": d, "strength": 50.0,
            "stock_fwd_1": e, "stock_fwd_3": e, "stock_fwd_5": e, "stock_fwd_10": e,
            "R": R,
        })
    return evs


def test_aggregate_daily_clustering_and_counts():
    # 3 olay AYNI günde, 1 olay farklı günde → n_events=4, n_days=2
    day1 = pd.Timestamp("2023-06-01")
    day2 = pd.Timestamp("2023-06-05")
    dates = [day1, day1, day1, day2]
    common = list(pd.date_range("2023-05-01", "2023-07-01", freq="B"))
    # univ_fwd: her i için tüm horizonlarda medyan=0 → excess = stock_fwd
    n = len(common)
    univ = {i: {h: 0.0 for h in [1, 3, 5, 10]} for i in range(n)}
    evs = _make_events(dates, [0.02, 0.04, 0.06, 0.03])  # pozitif excess
    agg = _aggregate("snapback", evs, univ, common, {"snapback": {}})
    assert agg["n_events"] == 4
    assert agg["n_days"] == 2  # günlük kümeleme: 2 ayrı gün
    assert agg["mean_excess_5d"] is not None and agg["mean_excess_5d"] > 0
    assert agg["excess"]["5"]["n"] == 4


def test_aggregate_negative_edge_disabled():
    day = pd.Timestamp("2023-06-01")
    dates = [day + pd.Timedelta(days=k) for k in range(35)]
    dates = [d for d in dates]
    common = list(pd.date_range("2023-05-01", "2023-08-01", freq="B"))
    n = len(common)
    univ = {i: {h: 0.10 for h in [1, 3, 5, 10]} for i in range(n)}  # evren medyanı yüksek
    # stock_fwd düşük (0.01) → excess = 0.01 - 0.10 = -0.09 (negatif edge)
    evs = _make_events(dates, [0.01] * 35)
    agg = _aggregate("snapback", evs, univ, common, {"snapback": {}})
    assert agg["mean_excess_5d"] < 0
    assert agg["verdict"] == "devre dışı"  # negatif edge gizlenir


def test_aggregate_empty():
    agg = _aggregate("snapback", [], {}, [], {})
    assert agg["n_events"] == 0
    assert agg["verdict"] == "deneysel"


# --- ön-kayıtlı rejim dilimi (by_regime) ----------------------------------

def test_aggregate_regime_split_reported_but_not_in_verdict():
    """regime_up etiketli olaylar up/down dilimlerine ayrılır; verdict AYNI kalır
    (koşullu kural değiştirme = snooping — dilim yalnız raporlama)."""
    day = pd.Timestamp("2023-06-01")
    dates = [day + pd.Timedelta(days=k) for k in range(40)]
    common = list(pd.date_range("2023-05-01", "2023-09-01", freq="B"))
    univ = {i: {h: 0.0 for h in [1, 3, 5, 10]} for i in range(len(common))}
    evs = []
    for k, d in enumerate(dates):
        evs.append({
            "ticker": "X", "date": d, "strength": 50.0,
            "stock_fwd_1": 0.02, "stock_fwd_3": 0.02, "stock_fwd_5": 0.02, "stock_fwd_10": 0.02,
            "R": 0.5, "R_net": 0.45,
            "regime_up": k % 4 != 0,   # 30 up / 10 down
        })
    agg = _aggregate("squeeze_breakout", evs, univ, common, {"squeeze_breakout": {}})
    br = agg["by_regime"]
    assert br["up"]["n"] == 30 and br["down"]["n"] == 10
    assert br["up"]["mean_R_net"] is not None
    assert br["up"]["mean_excess_5d"] is not None
    # verdict rejim diliminden BAĞIMSIZ (yalnız toplam excess'e dayanır)
    assert agg["verdict"] in {"kanıtlı", "zayıf"}  # pozitif excess, n>=30


def test_aggregate_no_regime_column_backward_compatible():
    """regime_up alanı olmayan (eski format) olay listesi kırılmamalı — by_regime boş döner."""
    day = pd.Timestamp("2023-06-01")
    common = list(pd.date_range("2023-05-01", "2023-07-01", freq="B"))
    univ = {i: {h: 0.0 for h in [1, 3, 5, 10]} for i in range(len(common))}
    evs = _make_events([day], [0.02])
    agg = _aggregate("snapback", evs, univ, common, {"snapback": {}})
    assert agg["by_regime"] == {}


# --- çıkış politikaları (trail + partial_be) — bilinen senaryolar --------

def test_trail_captures_more_than_fixed():
    """Trail: büyük kazanan koşar, HWM'den 2R altı trail'e değince çıkar → fixed 2R'den fazla.

    entry=100 stop=95 (risk 5), trail_mult=2 (10 altı). Fiyat 130'a (HWM→trail 120), sonra
    118'e düşer → trail_stop 120 → R=(120-100)/5=+4.0 (fixed olsa 2R hedefte çıkardı)."""
    o = pd.Series([100.0, 100.0, 101.0, 122.0, 100.0, 100.0])
    h = pd.Series([100.0, 100.0, 130.0, 125.0, 100.0, 100.0])
    low = pd.Series([100.0, 100.0, 101.0, 118.0, 100.0, 100.0])
    c = pd.Series([100.0, 100.0, 128.0, 120.0, 100.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=4, exit_policy="trail", exit_cfg={"trail_mult": 2.0})
    assert res.status == "trail_stop"
    assert abs(res.r_multiple - 4.0) < 1e-9      # trail 120 → +4R
    assert res.exit_price is not None            # trail tek fiyatlı


def test_trail_ignores_fixed_target():
    """Trail'de sabit hedef YOK: fixed target'ı (110) geçen bar ORADA çıkmaz — trail stop
    yönetir. Invariant: status ASLA 'target' olmaz (fixed olsa 112>110 → +2R'de çıkardı)."""
    o = pd.Series([100.0, 100.0, 108.0, 100.0])
    h = pd.Series([100.0, 100.0, 112.0, 100.0])   # 112 > fixed target 110
    low = pd.Series([100.0, 100.0, 106.0, 100.0])
    c = pd.Series([100.0, 100.0, 111.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=2, exit_policy="trail", exit_cfg={"trail_mult": 2.0})
    assert res.status != "target"                       # trail sabit hedefte ASLA çıkmaz
    assert res.status in ("trail_stop", "time_exit")    # yalnız trail-stop ya da zaman-çıkışı


def test_partial_scale_then_target():
    """partial_be: 1R'de yarı sat (+1R kilit) + başabaş; kalan hedefe (+2R) → blended +1.5R."""
    o = pd.Series([100.0, 100.0, 101.0, 104.0, 100.0])
    h = pd.Series([100.0, 100.0, 106.0, 111.0, 100.0])   # k2: 1R(105), k3: target(110)
    low = pd.Series([100.0, 100.0, 101.0, 104.0, 100.0])
    c = pd.Series([100.0, 100.0, 105.0, 110.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=4, exit_policy="partial_be")
    assert res.status == "partial_target"
    assert abs(res.r_multiple - 1.5) < 1e-9      # 0.5×1 + 0.5×2


def test_partial_scale_then_breakeven():
    """1R'de yarı sat (+1R) sonra kalan başabaşa (0R) → blended +0.5R (fixed olsa -1R'ye giderdi)."""
    o = pd.Series([100.0, 100.0, 101.0, 101.0, 100.0])
    h = pd.Series([100.0, 100.0, 106.0, 102.0, 100.0])   # k2: 1R scale
    low = pd.Series([100.0, 100.0, 101.0, 99.0, 100.0])  # k3: başabaş(100) altına düştü
    c = pd.Series([100.0, 100.0, 105.0, 100.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=4, exit_policy="partial_be")
    assert res.status == "partial_stop"
    assert abs(res.r_multiple - 0.5) < 1e-9      # 0.5×1 + 0.5×0


def test_partial_full_stop_before_scale():
    """1R'ye ulaşmadan tam stop → -1R (ölçekleme yok, tek satış maliyeti)."""
    o = pd.Series([100.0, 100.0, 100.0, 100.0])
    h = pd.Series([100.0, 100.0, 101.0, 100.0])
    low = pd.Series([100.0, 100.0, 94.0, 100.0])         # k2: stop(95) altı
    c = pd.Series([100.0, 100.0, 96.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=4, exit_policy="partial_be")
    assert res.status == "stop"
    assert abs(res.r_multiple - (-1.0)) < 1e-9


def test_partial_cost_1_5x_when_scaled():
    """Ölçeklenince 2 satış → net R, 1.5× round-trip maliyet düşer (dürüst friction)."""
    o = pd.Series([100.0, 100.0, 101.0, 104.0, 100.0])
    h = pd.Series([100.0, 100.0, 106.0, 111.0, 100.0])
    low = pd.Series([100.0, 100.0, 101.0, 104.0, 100.0])
    c = pd.Series([100.0, 100.0, 105.0, 110.0, 100.0])
    res = simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                                time_exit_days=4, exit_policy="partial_be", round_trip_cost_pct=0.0028)
    # cost_r = 0.0028 × entry/(entry-stop) = 0.0028 × 100/5 = 0.056; scaled → ×1.5 = 0.084
    assert res.status == "partial_target"
    assert abs(res.r_multiple - 1.5) < 1e-9
    assert abs(res.r_multiple_net - (1.5 - 0.084)) < 1e-6


def test_unknown_exit_policy_raises():
    import pytest
    o = pd.Series([100.0, 100.0, 100.0]); h = pd.Series([100.0, 101.0, 101.0])
    low = pd.Series([100.0, 100.0, 100.0]); c = pd.Series([100.0, 100.0, 100.0])
    with pytest.raises(ValueError):
        simulate_trade_detail(o, h, low, c, entry_j=1, stop=95.0, target=110.0,
                              time_exit_days=2, exit_policy="saçma")
