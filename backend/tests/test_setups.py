"""Setup dedektörü birim testleri — sentetik OHLCV (ağ/DB gerektirmez).

Her dedektör için: TETİKLEMESİ GEREKEN çerçeve + tetiklememesi gereken yakın-ıska (near-miss).
Invariant'lar: stop < entry < target, strength ∈ [0,100].
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.indicators import compute_indicators
from app.engine.setups import (
    MarketContext,
    detect_gap_hold_continuation,
    detect_htf_squeeze_breakout,
    detect_pead_drift,
    detect_quiet_accumulation,
    detect_rs_shield,
    detect_snapback,
    detect_squeeze_breakout,
    detect_trend_pullback,
)


def _frame(closes, highs=None, lows=None, opens=None, vols=None, start="2023-01-01"):
    """close listesinden OHLCV çerçeve (adj_close=close)."""
    n = len(closes)
    close = np.asarray(closes, dtype=float)
    high = np.asarray(highs, dtype=float) if highs is not None else close * 1.01
    low = np.asarray(lows, dtype=float) if lows is not None else close * 0.99
    open_ = np.asarray(opens, dtype=float) if opens is not None else close
    vol = np.asarray(vols, dtype=float) if vols is not None else np.full(n, 2_000_000.0)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "adj_close": close, "volume": vol},
        index=idx,
    )


def _high_liq_vol(n, price=100.0):
    """avg_tl_vol_20 >= 50M olacak hacim (vol_tl = close*volume)."""
    return np.full(n, 1_000_000.0)  # 100 * 1e6 = 1e8 > 5e7


def _invariants(res):
    assert res is not None
    assert res["stop"] < res["entry_ref"] < res["target"]
    assert 0.0 <= res["strength"] <= 100.0
    assert res["time_exit_days"] > 0 and res["valid_days"] > 0


# --- 1) snapback --------------------------------------------------------

def _snapback_frame():
    # GÜÇLÜ yükseliş trendi (EMA50 fiyatın altında kalır) + son barlarda keskin AŞIRI-SATILMIŞ
    # pullback (rsi<35, EMA50'nin biraz altı AMA %8'den derin değil) + YEŞİL dönüş mumu.
    # Yeni guard `ema50_floor_pct=-0.08` sığ oversold'a izin verir; eski sıkı `close>ema50`
    # guard'ı rsi<35 ile çelişip setup'ı öldürüyordu — bu frame yeni (doğru) davranışı doğrular.
    base = np.linspace(60.0, 118.0, 232)                    # istikrarlı güçlü yükseliş
    dip = [116.0, 113.0, 110.0, 106.0, 102.0, 104.5]        # keskin düşüş + son bar bounce
    closes = np.concatenate([base, dip]).astype(float)
    opens = closes.copy()
    opens[-1] = 102.5                                        # son bar YEŞİL (104.5 > 102.5) — dönüş mumu
    highs = np.maximum(closes, opens) * 1.004
    lows = np.minimum(closes, opens) * 0.99
    vols = _high_liq_vol(len(closes))
    return _frame(closes, highs, lows, opens, vols)


def test_snapback_triggers():
    ind = compute_indicators(_snapback_frame())
    i = len(ind) - 1
    # roc3 = 84/95-1 ~ -0.116 → alt desilde; evren eşiğini -0.02, min -0.15 verelim
    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5,
                        cross={"roc3_p10": -0.02, "roc3_min": -0.15})
    res = detect_snapback(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "snapback"


def test_snapback_near_miss_market_crashing():
    # piyasa çöküyorsa (mkt_ret_5d <= -0.05) tetiklenmemeli
    ind = compute_indicators(_snapback_frame())
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=-0.08, mkt_above_ema50=False, breadth=0.3,
                        cross={"roc3_p10": -0.02, "roc3_min": -0.15})
    assert detect_snapback(ind, i, ctx) is None


def test_snapback_near_miss_not_oversold():
    # roc3 alt-desilde değilse (eşik çok negatif) tetiklenmemeli
    ind = compute_indicators(_snapback_frame())
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5,
                        cross={"roc3_p10": -0.20, "roc3_min": -0.30})
    assert detect_snapback(ind, i, ctx) is None


# --- 2) squeeze_breakout ------------------------------------------------

def _squeeze_frame(with_volume=True):
    # oynaklık DARALMASI: ilk 211 bar geniş, son 49 bar çok dar → dünkü bb_width alt
    # persentilde (<=20). Sonra prior-20 max'ı net aşan hacimli kırılım barı.
    rng = np.random.default_rng(3)
    n = 260
    noise = np.concatenate([rng.normal(0, 1.5, 211), rng.normal(0, 0.05, 49)])
    closes = 100 + noise
    closes[-1] = closes[-2] + 4.0  # kırılım
    highs = closes * 1.002
    lows = closes * 0.998
    highs[-1] = closes[-1] + 0.3
    lows[-1] = closes[-1] - 3.5     # (close-low)/(high-low) ~ 0.92 >= 0.7
    opens = closes.copy()
    opens[-1] = closes[-1] - 3.0
    vols = np.full(n, 1_000_000.0)
    if with_volume:
        vols[-1] = 5_000_000.0  # 5x hacim patlaması
    return _frame(closes, highs, lows, opens, vols)


def test_squeeze_breakout_triggers():
    ind = compute_indicators(_squeeze_frame(with_volume=True))
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5, cross={})
    res = detect_squeeze_breakout(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "squeeze_breakout"


def test_squeeze_breakout_near_miss_no_volume():
    # sıkışma + kırılım var ama HACİM yok → tetiklenmemeli
    ind = compute_indicators(_squeeze_frame(with_volume=False))
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5, cross={})
    assert detect_squeeze_breakout(ind, i, ctx) is None


# --- 3) trend_pullback --------------------------------------------------

def _trend_pullback_frame():
    # güçlü yükseliş trendi (close>ema50>ema200, roc20~13%), sonra EMA20'ye geri çekilme.
    # Düzeltme uzun/derin → rsi ~55 altına soğur; son bar yeşil (bullish) ve EMA20'ye yakın.
    base = list(np.linspace(55, 100, 200))
    rise = list(np.linspace(101, 130, 10))          # dik 10-bar yükseliş 100→130
    dip = [127, 123, 119, 116, 114, 113, 112.5, 113, 113.3, 113.6, 114]  # EMA20'ye çekilme
    closes = np.asarray(base + rise + dip, dtype=float)
    highs = closes * 1.008
    lows = closes * 0.992
    opens = closes.copy()
    opens[-1] = closes[-1] - 0.3  # close>open → bullish
    vols = _high_liq_vol(len(closes), price=114.0)
    return _frame(closes, highs, lows, opens, vols)


def test_trend_pullback_triggers():
    ind = compute_indicators(_trend_pullback_frame())
    i = len(ind) - 1
    # ema20 yakınlığını doğrula (band <= 0.02)
    c = ind["close"].iat[i]
    ema20 = ind["ema20"].iat[i]
    assert abs(c / ema20 - 1.0) <= 0.02, f"kurgu hatası: dist_ema20={c/ema20-1:.4f}"
    ctx = MarketContext(mkt_ret_5d=0.01, mkt_above_ema50=True, breadth=0.6, cross={})
    res = detect_trend_pullback(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "trend_pullback"


def test_trend_pullback_near_miss_weak_regime():
    # rejim zayıf (mkt_above_ema50=False AND breadth<0.45) → tetiklenmemeli
    ind = compute_indicators(_trend_pullback_frame())
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=-0.01, mkt_above_ema50=False, breadth=0.30, cross={})
    assert detect_trend_pullback(ind, i, ctx) is None


# --- 4) pead_drift (canlı-only; sue+kap enjekte) ------------------------

def _pead_frame():
    closes = np.full(220, 100.0)
    closes[-1] = 101.0  # close >= open
    opens = closes.copy()
    opens[-1] = 100.5
    highs = closes * 1.01
    lows = closes * 0.99
    vols = _high_liq_vol(len(closes))
    return _frame(closes, highs, lows, opens, vols)


def test_pead_triggers_with_sue_and_kap():
    ind = compute_indicators(_pead_frame())
    i = len(ind) - 1
    ctx = MarketContext(cross={})
    res = detect_pead_drift(ind, i, ctx, sue=1.5, kap_recent=True)
    _invariants(res)
    assert res["setup"] == "pead_drift"
    assert res["context"]["status"].startswith("deneysel")


def test_pead_near_miss_no_kap():
    # SUE yüksek ama son 3 günde KAP finansal_tablo yok → tetiklenmemeli
    ind = compute_indicators(_pead_frame())
    i = len(ind) - 1
    ctx = MarketContext(cross={})
    assert detect_pead_drift(ind, i, ctx, sue=1.5, kap_recent=False) is None


def test_pead_near_miss_low_sue():
    ind = compute_indicators(_pead_frame())
    i = len(ind) - 1
    ctx = MarketContext(cross={})
    assert detect_pead_drift(ind, i, ctx, sue=0.2, kap_recent=True) is None


# --- 5) quiet_accumulation ----------------------------------------------

def _quiet_frame_and_market():
    n = 220
    closes = np.full(n, 100.0, dtype=float)
    # son 10 barda hisse dirençli (yatay/hafif yukarı) — market düşerken
    closes[-10:] = [100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.3, 100.5, 100.4, 100.6]
    opens = closes.copy()
    highs = closes * 1.005
    lows = closes * 0.997
    vols = np.full(n, 1_000_000.0)
    vols[-10:] = 2_000_000.0  # hacim >= 1.3x ortalama
    frame = _frame(closes, highs, lows, opens, vols)
    # piyasa günlük getiri serisi: son 10 günde piyasa düşüyor (< -0.005)
    mkt_ret = pd.Series(0.0, index=frame.index)
    mkt_ret.iloc[-10:] = -0.01  # her gün -%1 → mkt_down_thr(-0.005) altında
    return frame, mkt_ret


def test_quiet_accumulation_triggers():
    frame, mkt_ret = _quiet_frame_and_market()
    ind = compute_indicators(frame)
    # mkt_ret'i indikatör index'ine hizala
    mkt_ret = mkt_ret.reindex(ind.index)
    i = len(ind) - 1
    ctx = MarketContext(cross={"mkt_ret_series": mkt_ret})
    res = detect_quiet_accumulation(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "quiet_accumulation"
    assert res["context"]["divergence_days"] >= 3


def test_quiet_accumulation_near_miss_market_up():
    # piyasa DÜŞMÜYORSA divergence sayılmaz → tetiklenmemeli
    frame, mkt_ret = _quiet_frame_and_market()
    ind = compute_indicators(frame)
    mkt_ret_up = pd.Series(0.01, index=ind.index)  # piyasa yükseliyor
    i = len(ind) - 1
    ctx = MarketContext(cross={"mkt_ret_series": mkt_ret_up})
    assert detect_quiet_accumulation(ind, i, ctx) is None


# --- ortak invariant: stop hesabı long-güvenli --------------------------

def test_all_results_stop_below_entry():
    """Tetiklenen her setup için stop < entry (long risk pozitif)."""
    ind = compute_indicators(_snapback_frame())
    i = len(ind) - 1
    ctx = MarketContext(mkt_ret_5d=0.0, mkt_above_ema50=True, breadth=0.5,
                        cross={"roc3_p10": -0.02, "roc3_min": -0.15})
    res = detect_snapback(ind, i, ctx)
    assert res is not None
    risk = res["entry_ref"] - res["stop"]
    assert risk > 0
    # target = entry + 2R
    assert abs((res["target"] - res["entry_ref"]) - 2 * risk) < 1e-6


# --- 6) htf_squeeze_breakout (v2) --------------------------------------

def _htf_squeeze_frame():
    """280 bar: uzun DAR aralık (sıkışma), sonra son barda hacimli 60g-zirve kırılımı."""
    rng = np.random.default_rng(7)
    # 279 bar 99-101 arası dar bant (60g aralık dar) + son bar sert kırılım
    body = 100.0 + rng.uniform(-1.0, 1.0, 279)
    closes = np.append(body, 104.0)  # son bar 60g max (~101) üstünde net kırılım
    highs = closes + 0.3
    lows = closes - 0.3
    highs[-1], lows[-1] = 104.3, 101.2   # close_pos yüksek, kırılım barı
    opens = closes.copy(); opens[-1] = 101.5
    vols = _high_liq_vol(len(closes))
    vols[-1] = 3_000_000.0               # vol_tl = 104*3e6 patlama (>2x avg)
    return _frame(closes, highs, lows, opens, vols)


def test_htf_squeeze_triggers():
    ind = compute_indicators(_htf_squeeze_frame())
    i = len(ind) - 1
    ctx = MarketContext()
    res = detect_htf_squeeze_breakout(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "htf_squeeze_breakout"
    assert res["time_exit_days"] == 15


def test_htf_squeeze_near_miss_no_volume():
    # aynı kırılım ama hacim patlaması yoksa tetiklenmemeli
    f = _htf_squeeze_frame()
    f.iloc[-1, f.columns.get_loc("volume")] = 500_000.0  # düşük hacim
    ind = compute_indicators(f)
    assert detect_htf_squeeze_breakout(ind, len(ind) - 1, MarketContext()) is None


def test_htf_squeeze_near_miss_ceiling_day():
    # kırılım günü +%8 üstü (tavan bölgesi) → limit-devam dışlaması devrede
    f = _htf_squeeze_frame()
    # son bar prev_close'un %10 üstünde kapansın (adj_close de güncellenmeli — indikatörler
    # adjusted seride çalışır; yalnız close değişirse ölçekleme etkiyi siler)
    prev = f.iloc[-2, f.columns.get_loc("close")]
    for col in ("close", "adj_close"):
        f.iloc[-1, f.columns.get_loc(col)] = prev * 1.10
    f.iloc[-1, f.columns.get_loc("high")] = prev * 1.11
    ind = compute_indicators(f)
    assert detect_htf_squeeze_breakout(ind, len(ind) - 1, MarketContext()) is None


# --- 7) gap_hold_continuation (v2) -------------------------------------

def _gap_hold_frame():
    """Yukarı trend (close>ema50), son bar %5 hacimli gap ve gün içinde hiç dolmuyor."""
    base = np.linspace(80.0, 100.0, 60)   # yükselen trend → ema50 altında kalır close üstünde
    closes = base.copy()
    prev_close = closes[-1]               # ~100
    gap_open = prev_close * 1.05          # %5 gap
    gap_close = gap_open * 1.02           # close>open, gün içi güçlü
    closes = np.append(closes, gap_close)
    highs = closes * 1.005
    lows = closes * 0.997
    opens = closes.copy()
    # son bar: open=gap_open, low >= prev_close (gap dolmadı), close_pos yüksek
    opens[-1] = gap_open
    lows[-1] = prev_close * 1.001
    highs[-1] = gap_close * 1.001
    vols = _high_liq_vol(len(closes))
    vols[-1] = 4_000_000.0                # >2.5x avg
    return _frame(closes, highs, lows, opens, vols)


def test_gap_hold_triggers():
    ind = compute_indicators(_gap_hold_frame())
    i = len(ind) - 1
    res = detect_gap_hold_continuation(ind, i, MarketContext())
    _invariants(res)
    assert res["setup"] == "gap_hold_continuation"
    assert res["context"]["gap_pct"] >= 3.0


def test_gap_hold_near_miss_gap_filled():
    # gap gün içinde dolduysa (low < prev_close) tetiklenmemeli
    f = _gap_hold_frame()
    prev_close = f.iloc[-2, f.columns.get_loc("close")]
    f.iloc[-1, f.columns.get_loc("low")] = prev_close * 0.98  # gap doldu
    ind = compute_indicators(f)
    assert detect_gap_hold_continuation(ind, len(ind) - 1, MarketContext()) is None


def test_gap_hold_near_miss_too_big_gap():
    # %7 üstü gap (tek-gün patlama bölgesi) → bant dışı, tetiklememeli
    f = _gap_hold_frame()
    prev_close = f.iloc[-2, f.columns.get_loc("close")]
    big = prev_close * 1.12
    f.iloc[-1, f.columns.get_loc("open")] = big
    f.iloc[-1, f.columns.get_loc("low")] = prev_close * 1.001
    f.iloc[-1, f.columns.get_loc("close")] = big * 1.005
    f.iloc[-1, f.columns.get_loc("high")] = big * 1.01
    ind = compute_indicators(f)
    assert detect_gap_hold_continuation(ind, len(ind) - 1, MarketContext()) is None


# --- 8) rs_shield (v2) --------------------------------------------------

def _rs_shield_frame():
    """Piyasa düşerken yatay/pozitif kalan, ema50 üstü isim; son bar yeşil."""
    closes = np.append(np.full(60, 100.0), [100.5])  # 15g yatay → ret15 ~ 0, close>ema50~100
    highs = closes * 1.005
    lows = closes * 0.997
    opens = closes.copy(); opens[-1] = 100.1
    vols = _high_liq_vol(len(closes))
    return _frame(closes, highs, lows, opens, vols)


def test_rs_shield_triggers():
    ind = compute_indicators(_rs_shield_frame())
    i = len(ind) - 1
    # piyasa 15g -%8 (düşüş), bugün +%1.5 (stabilizasyon); rs eşiği düşük → geçsin
    ctx = MarketContext(mkt_ret_15d=-0.08, mkt_day_ret=0.015, cross={"rs15_p90": 0.0})
    res = detect_rs_shield(ind, i, ctx)
    _invariants(res)
    assert res["setup"] == "rs_shield"


def test_rs_shield_near_miss_market_not_down():
    ind = compute_indicators(_rs_shield_frame())
    i = len(ind) - 1
    # piyasa 15g yalnız -%2 (koşul -%5) → tetiklememeli
    ctx = MarketContext(mkt_ret_15d=-0.02, mkt_day_ret=0.015, cross={"rs15_p90": 0.0})
    assert detect_rs_shield(ind, i, ctx) is None


def test_rs_shield_near_miss_no_stabilization():
    ind = compute_indicators(_rs_shield_frame())
    i = len(ind) - 1
    # piyasa bugün yatay (+%0.2 < +%1 tetik) → stabilizasyon günü değil
    ctx = MarketContext(mkt_ret_15d=-0.08, mkt_day_ret=0.002, cross={"rs15_p90": 0.0})
    assert detect_rs_shield(ind, i, ctx) is None


# --- regresyon: NaN pencere persentil sapması (critic Bug 1) -----------

def test_squeeze_percentile_ignores_nan_in_window():
    """Birleşik-takvim reindex'i pencereye NaN sokarsa persentil YALNIZ geçerli değerlerden
    hesaplanmalı — NaN'lar paydaya sayılıp persentili aşağı sapıtmamalı (yanlış sıkışma).

    Aynı gerçek fiyat serisi: (a) temiz, (b) araya NaN barlar serpiştirilmiş. İki durumda da
    dedektör AYNI kararı vermeli (NaN'lar tetiği uydurmamalı)."""
    from app.engine.setups import detect_squeeze_breakout

    # geniş bir bant (sıkışma YOK) → tetiklememeli; NaN sızıntısı bunu tetiğe çevirmemeli
    rng = np.random.default_rng(11)
    closes = 100.0 + np.cumsum(rng.normal(0, 1.2, 300))  # geniş yürüyüş, dar değil
    f_clean = _frame(closes, vols=_high_liq_vol(300))
    ind_clean = compute_indicators(f_clean)
    res_clean = detect_squeeze_breakout(ind_clean, len(ind_clean) - 1, MarketContext())

    # aynı seri ama son 252g penceresine NaN barlar (reindex boşluğu taklidi)
    f_gappy = f_clean.copy()
    gap_rows = np.arange(40, 260, 3)  # düzenli NaN'lar (penceremin ~1/3'ü)
    for col in ("open", "high", "low", "close", "adj_close", "volume"):
        f_gappy.iloc[gap_rows, f_gappy.columns.get_loc(col)] = np.nan
    ind_gappy = compute_indicators(f_gappy)
    res_gappy = detect_squeeze_breakout(ind_gappy, len(ind_gappy) - 1, MarketContext())

    # geniş bantta ikisi de None (NaN'lar yanlış sıkışma üretmemeli)
    assert res_clean is None
    assert res_gappy is None
