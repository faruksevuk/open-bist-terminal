"""Olay-tetikli SETUP dedektörleri — SETUPS v0.1 (kısa-vade 2-10 gün işlem katmanı).

NEDEN (docstring'de kalsın): kalibre edilmiş kesitsel faktör skoru DÜRÜST ama düşük-vol
isimleri seçiyor — kullanıcının asıl hedefine (2-10 günlük giriş/stop/hedefli kısa-vade
işlem yakalamak) yaramıyor. Bu VERİ üzerinde yapılan faktör teşhisi roc5 IC=-0.019
(NW t=-2.76) gösterdi: 5-günün kaybedenleri geri sıçrıyor — sistemin hiç kullanmadığı
gerçek bir kısa-vade dönüş edge'i. Bu katman her SETUP'ı KENDİ verimizde bir olay-çalışması
(event study) ile bağımsız doğrular (app/backtest/event_study.py).

Tasarım: saf fonksiyonlar. Her dedektör bir per-ticker indikatör DataFrame'i
(compute_indicators çıktısı) + kesitsel bağlam (`MarketContext`) alır ve BELİRLİ bir
bar indeksinde (`i`) çalışır. Canlı tarama son barı (`i=-1`) kullanır; olay-çalışması
geçmişteki HER barı point-in-time (yalnız t'ye kadarki veriyle) değerlendirir.

Dönüş: tetiklenmezse None; tetiklenirse dict:
    {setup, strength(0-100), entry_ref(son close), stop, target, time_exit_days,
     valid_days, context(tetikleyen indikatör değerleri)}

TÜM parametreler literatür-temelli PRIOR varsayılanlar — optimizasyon YOK (§9.5 deneme
disiplini). Config 'setups' anahtarından okunur, yoksa _DEF_SETUPS.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# --- setup görünen adları (Türkçe; anahtarlar İngilizce) ----------------
SETUP_LABELS: dict[str, str] = {
    "snapback": "Panik Dönüşü",
    "squeeze_breakout": "Sıkışma Kırılımı",
    "trend_pullback": "Trend İçi Düzeltme",
    "pead_drift": "Bilanço Sürprizi (PEAD)",
    "quiet_accumulation": "Sessiz Toplama",
    # v2 (2026-07-07 araştırma turu; literatür-refütasyonu sonrası mekanizma-temelli)
    "htf_squeeze_breakout": "Uzun Sıkışma Kırılımı",
    "gap_hold_continuation": "Boşluk Tutunması",
    "rs_shield": "Düşüşte Dirençli Lider",
}

# --- varsayılan config (PRIOR) ------------------------------------------
# Hiçbiri optimize EDİLMEDİ; tümü literatür/araştırma-temelli varsayılan (§9.5).
_DEF_SETUPS: dict = {
    "common": {
        "min_liq_tl": 50_000_000,   # likidite tabanı ( scoring ile aynı)
        "min_bars": 200,            # yeterli geçmiş
        "block_news_neg": -5,       # taze negatif haber → setup bloke
    },
    "snapback": {
        "roc3_decile": 0.10,        # roc3 evren alt-desili
        "rsi_max": 35,
        "ema200_floor_mult": 0.90,  # close > 0.90*ema200 (ölüm sarmalı değil)
        "ema50_floor_pct": -0.08,   # close, EMA50'nin %8'inden fazla altında değil (derin kırılım guard'ı)
        "idio_dd": -0.04,           # hisse 5g - piyasa 5g <= -0.04 (idiyosenkratik)
        "mkt_floor_5d": -0.05,      # piyasa çökmüyor
        "atr_stop_mult": 0.5,
        "rr": 2.0,
        "time_exit_days": 5,
        "valid_days": 2,
    },
    "squeeze_breakout": {
        "bbwidth_pctile_max": 20,   # 252g kendi tarihinde bb_width persentili <= 20 (dün)
        "vol_mult": 1.5,            # vol_tl >= 1.5 * avg_tl_vol_20
        "close_range_min": 0.70,    # (close-low)/(high-low) >= 0.7
        "atr_stop_mult": 0.5,
        "rr": 2.0,
        "time_exit_days": 10,
        "valid_days": 2,
    },
    "trend_pullback": {
        "roc20_min": 10,
        "ema20_band": 0.02,         # |close/ema20 - 1| <= 0.02
        "rsi_low": 35,
        "rsi_high": 55,
        "breadth_floor": 0.45,      # mkt_above_ema50 OR breadth > 0.45
        "atr_stop_mult": 0.5,
        "rr": 2.0,
        "time_exit_days": 10,
        "valid_days": 2,
    },
    "pead_drift": {
        "sue_min": 0.5,
        "kap_window_days": 3,       # son 3 işlem günü içinde finansal_tablo KAP olayı
        "atr_stop_mult": 0.5,
        "rr": 2.0,
        "time_exit_days": 15,
        "valid_days": 3,
    },
    "quiet_accumulation": {
        "lookback": 10,
        "mkt_down_thr": -0.005,     # piyasa eş-ağırlık ret < -0.005
        "vol_mult": 1.3,            # vol_tl >= 1.3 * avg_tl_vol_20
        "min_count": 3,             # >= 3 gün divergence
        "last_down_thr": -0.01,     # son bar > -%1 (kesin düşüş değil)
        "atr_stop_mult": 0.5,
        "rr": 2.0,
        "time_exit_days": 10,
        "valid_days": 3,
    },
    # --- v2 dedektörler (araştırma turu 2026-07-07) ------------------------
    # Literatür bulguları (limit-devam/52h/hacim-primi/haftalık-reversal/BIST-contrarian)
    # adversarial incelemede HEPSİ refüte edildi (0/21). Bu üç dedektör mekanizma-temelli:
    # htf = sistem-içi kanıtlı squeeze edge'inin uzun-ufuk analojisi ('medium'); diğerleri
    # prior-only. Tümü refüte aileleri (tavan kovalama, tek-gün patlaması) BİLİNÇLİ dışlar
    # (day_gain_max, max_entry_gap kuralları). §9.5: parametre araması YOK, tek event-study.
    "htf_squeeze_breakout": {
        "range_lookback_days": 60, "width_pctile_max": 20, "pctile_window_days": 252,
        "breakout_lookback_days": 60, "vol_mult": 2.0, "close_range_min": 0.70,
        "day_gain_max_pct": 8, "max_entry_gap_pct": 4, "stop_lookback_low_days": 10,
        "atr_stop_mult": 0.5, "rr": 2.0, "time_exit_days": 15, "valid_days": 2, "min_bars": 260,
    },
    "gap_hold_continuation": {
        "gap_min_pct": 3, "gap_max_pct": 7, "close_range_min": 0.75, "vol_mult": 2.5,
        "day_gain_max_pct": 9, "ema_filter_len": 50, "max_entry_gap_pct": 4,
        "atr_stop_mult": 0.5, "rr": 2.0, "time_exit_days": 10, "valid_days": 1,
    },
    "rs_shield": {
        "mkt_ret_lookback_days": 15, "mkt_ret_max": -0.05, "stock_ret_min": 0.0,
        "rs_pctile_min": 90, "trigger_mkt_day_gain_min": 0.01, "max_entry_gap_pct": 4,
        "stop_lookback_low_days": 10, "atr_stop_mult": 0.5, "rr": 2.0,
        "time_exit_days": 12, "valid_days": 2,
    },
}


# --- kesitsel piyasa bağlamı --------------------------------------------

@dataclass
class MarketContext:
    """Tarama başına BİR kez hesaplanır (features.py'deki eş-ağırlık evren mantığıyla).

    Olay-çalışmasında ise HER bar için (point-in-time) yeniden üretilir: o tarihe kadarki
    eş-ağırlık piyasa serisinden türetilir. Kesitsel eşikler (ör. roc3 desili) `cross`
    üzerinden geçirilir çünkü o gün tüm evreni gerektirir.
    """

    mkt_ret_5d: float = 0.0
    mkt_above_ema50: bool = False
    breadth: float = 0.5
    # v2: rs_shield için piyasa 15g getirisi + bugünkü piyasa getirisi (stabilizasyon tetiği)
    mkt_ret_15d: float = 0.0
    mkt_day_ret: float = 0.0
    # kesitsel yardımcılar (o barda tüm evrenin değeri): dedektör içinde eşik hesaplamak için
    cross: dict = field(default_factory=dict)


# --- ortak yardımcılar --------------------------------------------------

def _f(v) -> float | None:
    if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct_change(s: pd.Series, i: int, n: int) -> float | None:
    """i barında n-günlük yüzde değişim (point-in-time; i>=n gerekir)."""
    if i - n < 0:
        return None
    a, b = s.iat[i - n], s.iat[i]
    if pd.isna(a) or pd.isna(b) or a == 0:
        return None
    return float(b / a - 1.0)


def _liquid(ind: pd.DataFrame, i: int, min_liq: float) -> bool:
    v = ind["avg_tl_vol_20"].iat[i] if "avg_tl_vol_20" in ind else None
    return bool(v is not None and not pd.isna(v) and v >= min_liq)


def _mk_result(setup: str, entry_ref: float, stop: float, rr: float,
               time_exit_days: int, valid_days: int, strength: float,
               context: dict, target: float | None = None) -> dict | None:
    """Ortak sonuç kurucu + invariant koruması (stop < entry < target).

    Stop giriş fiyatının üstündeyse (geçersiz risk) → setup düşürülür (None).
    target verilmemişse 2R (veya rr) ile üretilir.
    """
    entry_ref = float(entry_ref)
    stop = float(stop)
    if not np.isfinite(entry_ref) or not np.isfinite(stop):
        return None
    risk = entry_ref - stop
    if risk <= 0:  # stop girişin altında değil → geçersiz (long setup)
        return None
    if target is None:
        target = entry_ref + rr * risk
    target = float(target)
    if not (stop < entry_ref < target):
        return None
    return {
        "setup": setup,
        "strength": float(np.clip(strength, 0.0, 100.0)),
        "entry_ref": round(entry_ref, 6),
        "stop": round(stop, 6),
        "target": round(target, 6),
        "time_exit_days": int(time_exit_days),
        "valid_days": int(valid_days),
        "context": context,
    }


# --- 1) snapback ("Panik Dönüşü") --------------------------------------

def detect_snapback(ind: pd.DataFrame, i: int, ctx: MarketContext,
                    cfg: dict | None = None) -> dict | None:
    """roc5 IC=-0.019 (NW t=-2.76) ölçülü edge'iyle hizalı: aşırı-satılmış idiyosenkratik
    düşüşün geri sıçraması. Ölüm sarmalı ve piyasa çöküşü hariç tutulur.

    Tetik (i barında): roc3 evren alt-desilinde AND rsi14<35 AND close>0.90*ema200 AND
    close EMA50'nin ema50_floor_pct'inden fazla altında DEĞİL (derin kırılım/düşen-bıçak
    guard'ı — sığ oversold pullback'e izin verir) AND dönüş mumu (close>open) AND
    (hisse 5g ret - piyasa 5g ret <= -0.04) AND piyasa 5g ret > -0.05.
    stop = son 3 barın min low'u - 0.5*atr14; target = 2R; time_exit=5; valid=2.
    strength = aşırı-satılmışlık derinliği (roc3 persentili) ve rsi derinliğinin harmanı.

    NOT: eski `close > ema50` guard'ı rsi<35 ile çelişip setup'ı fiilen öldürüyordu (gerçek
    aşırı-satılmış isim zaten EMA50'nin biraz altındadır). ema50_floor_pct (%-8) yalnız DERİN
    kırılımı eler; düşen-bıçak koruması korunur, snapback yine tetiklenebilir.
    """
    p = (cfg or _DEF_SETUPS)["snapback"]
    if i < 3:
        return None
    c = ind["close"].iat[i]
    ema200 = ind["ema200"].iat[i]
    rsi = ind["rsi14"].iat[i]
    atr = ind["atr14"].iat[i]
    if any(pd.isna(x) for x in (c, ema200, rsi, atr)) or atr <= 0:
        return None

    roc3 = _pct_change(ind["close"], i, 3)
    ret5 = _pct_change(ind["close"], i, 5)
    if roc3 is None or ret5 is None:
        return None

    # kesitsel: roc3 evren alt-desil eşiği (o gün tüm evrenden geçirilir)
    roc3_cut = ctx.cross.get("roc3_p10")
    if roc3_cut is None:
        return None

    if not (roc3 <= roc3_cut):
        return None
    if not (rsi < p["rsi_max"]):
        return None
    if not (c > p["ema200_floor_mult"] * ema200):  # ölüm sarmalı değil
        return None
    ema50 = ind["ema50"].iat[i]
    # düşen bıçak koruması: yalnız DERİN kırılımı (EMA50'nin ema50_floor_pct altı) blokla;
    # sığ aşırı-satılmış pullback'e izin ver (sıkı `c>ema50` rsi<35 ile çelişip setup'ı öldürüyordu).
    floor_pct = p.get("ema50_floor_pct", -0.08)
    if pd.isna(ema50) or ema50 <= 0 or (c - ema50) / ema50 < floor_pct:
        return None
    if c <= ind["open"].iat[i]:  # dönüş mumu değilse alma
        return None
    if not ((ret5 - ctx.mkt_ret_5d) <= p["idio_dd"]):  # idiyosenkratik düşüş
        return None
    if not (ctx.mkt_ret_5d > p["mkt_floor_5d"]):  # piyasa çökmüyor
        return None

    low3 = float(ind["low"].iloc[max(0, i - 2): i + 1].min())
    stop = low3 - p["atr_stop_mult"] * atr

    # strength: roc3 ne kadar dipteyse (alt-desil içinde) ve rsi ne kadar düşükse o kadar güçlü.
    # roc3 derinliği: 0 (tam eşikte) .. 1 (evren en dibi). rsi derinliği: rsi=35→0, rsi=10→1.
    roc3_min = ctx.cross.get("roc3_min")
    if roc3_min is None:
        roc3_min = roc3
    denom = (roc3_cut - roc3_min) if (roc3_cut - roc3_min) > 1e-9 else 1e-9
    depth = float(np.clip((roc3_cut - roc3) / denom, 0.0, 1.0))
    rsi_depth = float(np.clip((p["rsi_max"] - rsi) / 25.0, 0.0, 1.0))  # 35→10 aralığı
    strength = 100.0 * (0.6 * depth + 0.4 * rsi_depth)

    ctx_out = {
        "roc3": round(roc3, 4), "roc3_cut": round(roc3_cut, 4), "rsi14": round(float(rsi), 1),
        "ret5_excess": round(ret5 - ctx.mkt_ret_5d, 4), "mkt_ret_5d": round(ctx.mkt_ret_5d, 4),
    }
    return _mk_result("snapback", c, stop, p["rr"], p["time_exit_days"], p["valid_days"],
                      strength, ctx_out)


# --- 2) squeeze_breakout ("Sıkışma Kırılımı") --------------------------

def detect_squeeze_breakout(ind: pd.DataFrame, i: int, ctx: MarketContext,
                            cfg: dict | None = None) -> dict | None:
    """Bollinger sıkışması (kendi 252g tarihinin alt %20 genişliği) ardından hacimli kırılım.

    Tetik: bb_width = (bb_upper-bb_lower)/bb_mid; DÜNKÜ (i-1) bb_width'in kendi 252g
    persentili <= 20 AND close > prior 20 barın max close'u AND vol_tl >= 1.5*avg_tl_vol_20
    AND (close-low)/(high-low) >= 0.70.
    stop = kırılım barının low'u - 0.5*atr14; target = 2R; time_exit=10; valid=2.
    """
    p = (cfg or _DEF_SETUPS)["squeeze_breakout"]
    if i < 21:
        return None
    c = ind["close"].iat[i]
    hi = ind["high"].iat[i]
    lo = ind["low"].iat[i]
    atr = ind["atr14"].iat[i]
    vol = ind["vol_tl"].iat[i] if "vol_tl" in ind else None
    avgvol = ind["avg_tl_vol_20"].iat[i]
    if any(pd.isna(x) for x in (c, hi, lo, atr)) or atr <= 0 or hi <= lo:
        return None
    if vol is None or pd.isna(vol) or pd.isna(avgvol) or avgvol <= 0:
        return None

    bb_width = ((ind["bb_upper"] - ind["bb_lower"]) / ind["bb_mid"]).replace(
        [np.inf, -np.inf], np.nan)
    # DÜNKÜ sıkışma: i-1 barındaki genişlik, kendi son 252g penceresinde persentil
    win = bb_width.iloc[max(0, i - 252): i]  # i hariç (i-1'e kadar) → look-ahead yok
    if len(win) < 60 or pd.isna(win.iloc[-1]):
        return None
    yday_width = win.iloc[-1]
    # persentil YALNIZ geçerli değerler üzerinden: pencere birleşik-takvime reindex'li
    # olduğundan işlem görmeyen günler NaN; NaN'lar paydaya sayılırsa persentil aşağı
    # sapar → yanlış "sıkışma" tetiği (gappy/yeni isimlerde). dropna ile düzeltilir.
    w = win.dropna()
    if len(w) < 60:
        return None
    pctile = float((w <= yday_width).mean() * 100.0)
    if not (pctile <= p["bbwidth_pctile_max"]):
        return None

    # kırılım: close prior 20 barın (i hariç) max close'unu aşmalı
    prior_max = float(ind["close"].iloc[max(0, i - 20): i].max())
    if not (c > prior_max):
        return None
    if not (vol >= p["vol_mult"] * avgvol):
        return None
    close_pos = (c - lo) / (hi - lo)
    if not (close_pos >= p["close_range_min"]):
        return None

    stop = lo - p["atr_stop_mult"] * atr
    # strength: sıkışma ne kadar dar (persentil düşük) + hacim patlaması + gün-içi güç
    tightness = float(np.clip((p["bbwidth_pctile_max"] - pctile) / p["bbwidth_pctile_max"], 0, 1))
    vol_boost = float(np.clip((vol / avgvol - p["vol_mult"]) / p["vol_mult"], 0, 1))
    strength = 100.0 * (0.5 * tightness + 0.3 * vol_boost + 0.2 * close_pos)

    ctx_out = {
        "bb_width_pctile": round(pctile, 1), "prior20_max": round(prior_max, 4),
        "vol_mult": round(float(vol / avgvol), 2), "close_pos": round(float(close_pos), 2),
    }
    return _mk_result("squeeze_breakout", c, stop, p["rr"], p["time_exit_days"], p["valid_days"],
                      strength, ctx_out)


# --- 3) trend_pullback ("Trend İçi Düzeltme") --------------------------

def detect_trend_pullback(ind: pd.DataFrame, i: int, ctx: MarketContext,
                          cfg: dict | None = None) -> dict | None:
    """Yükseliş trendinde 20EMA'ya geri çekilme + dönüş sinyali.

    Tetik: close>ema50>ema200 AND roc20>=10 AND |close/ema20-1|<=0.02 AND 35<=rsi14<=55
    AND (yükseliş barı: close>open, VEYA prior 5 bara göre higher_low).
    Ek: mkt_above_ema50 OR breadth>0.45 (piyasa rejimi destekli).
    stop = son 5 barın min low'u - 0.5*atr14; target = 2R; time_exit=10; valid=2.
    """
    p = (cfg or _DEF_SETUPS)["trend_pullback"]
    if i < 5:
        return None
    c = ind["close"].iat[i]
    o = ind["open"].iat[i]
    ema20 = ind["ema20"].iat[i]
    ema50 = ind["ema50"].iat[i]
    ema200 = ind["ema200"].iat[i]
    rsi = ind["rsi14"].iat[i]
    atr = ind["atr14"].iat[i]
    if any(pd.isna(x) for x in (c, o, ema20, ema50, ema200, rsi, atr)) or atr <= 0:
        return None

    if not (c > ema50 > ema200):
        return None
    roc20 = _pct_change(ind["close"], i, 20)
    if roc20 is None or (roc20 * 100.0) < p["roc20_min"]:  # roc20 fraksiyon → yüzdeye
        return None
    if ema20 == 0 or abs(c / ema20 - 1.0) > p["ema20_band"]:
        return None
    if not (p["rsi_low"] <= rsi <= p["rsi_high"]):
        return None

    bullish = c > o
    prior5_low = float(ind["low"].iloc[max(0, i - 5): i].min())
    higher_low = ind["low"].iat[i] > prior5_low
    if not (bullish or higher_low):
        return None

    # rejim kapısı
    if not (ctx.mkt_above_ema50 or ctx.breadth > p["breadth_floor"]):
        return None

    low5 = float(ind["low"].iloc[max(0, i - 4): i + 1].min())
    stop = low5 - p["atr_stop_mult"] * atr

    # strength: trend gücü (roc20) + EMA20 yakınlığı (ne kadar sıkı düzeltme)
    trend_str = float(np.clip((roc20 * 100.0 - p["roc20_min"]) / 30.0, 0, 1))
    proximity = float(np.clip(1.0 - abs(c / ema20 - 1.0) / p["ema20_band"], 0, 1))
    strength = 100.0 * (0.6 * trend_str + 0.4 * proximity)

    ctx_out = {
        "roc20_pct": round(roc20 * 100.0, 1), "dist_ema20": round(float(c / ema20 - 1.0), 4),
        "rsi14": round(float(rsi), 1), "bullish": bool(bullish), "higher_low": bool(higher_low),
    }
    return _mk_result("trend_pullback", c, stop, p["rr"], p["time_exit_days"], p["valid_days"],
                      strength, ctx_out)


# --- 4) pead_drift ("Bilanço Sürprizi") — CANLI-ONLY -------------------

def detect_pead_drift(ind: pd.DataFrame, i: int, ctx: MarketContext,
                      cfg: dict | None = None, *, sue: float | None = None,
                      kap_recent: bool = False) -> dict | None:
    """PEAD (post-earnings-announcement drift). CANLI-ONLY: SUE latest-snapshot olduğundan
    (PIT açıklama tarihi yok) DÜRÜST event-study EDİLEMEZ — kanıt statüsü "deneysel (prior — PIT yok)".

    Tetik: fundamentals raw.sue >= 0.5 AND ticker için son 3 işlem günü içinde bir
    finansal_tablo KAP olayı (kap_recent=True; yoksa TETİKLEME) AND son bar close>=open.
    stop = son barın low'u - 0.5*atr14; time_exit=15; valid=3.

    sue/kap_recent canlı taramadan (setup_scan) enjekte edilir; olay-çalışmasında bu setup
    KOŞULMAZ (event_study.py atlar).
    """
    p = (cfg or _DEF_SETUPS)["pead_drift"]
    c = ind["close"].iat[i]
    o = ind["open"].iat[i]
    lo = ind["low"].iat[i]
    atr = ind["atr14"].iat[i]
    if any(pd.isna(x) for x in (c, o, lo, atr)) or atr <= 0:
        return None
    if sue is None or sue < p["sue_min"]:
        return None
    if not kap_recent:
        return None
    if not (c >= o):  # yükseliş/nötr bar
        return None

    stop = lo - p["atr_stop_mult"] * atr
    # strength: SUE büyüklüğü (0.5→0, 3.0→1) ile ölçekli
    strength = 100.0 * float(np.clip((sue - p["sue_min"]) / 2.5, 0.0, 1.0))
    ctx_out = {"sue": round(float(sue), 3), "kap_recent": True,
               "status": "deneysel (prior — PIT yok)"}
    return _mk_result("pead_drift", c, stop, p["rr"], p["time_exit_days"], p["valid_days"],
                      strength, ctx_out)


# --- 5) quiet_accumulation ("Sessiz Toplama") --------------------------

def detect_quiet_accumulation(ind: pd.DataFrame, i: int, ctx: MarketContext,
                              cfg: dict | None = None) -> dict | None:
    """Piyasa düşerken hacimli dirençli günler → sessiz alım (accumulation).

    Tetik: son 10 barda, piyasa eş-ağırlık ret < -0.005 iken hisse ret >= 0 ve
    vol_tl >= 1.3*avg_tl_vol_20 olan gün sayısı >= 3 AND son bar > -%1.
    stop = son 10 barın min low'u - 0.5*atr14; target = 2R; time_exit=10; valid=3.

    Piyasa günlük getiri serisi ctx.cross['mkt_ret_series'] ile (o tarihe kadar PIT)
    verilir; her barın piyasa getirisi indeks-hizalı olmalı.
    """
    p = (cfg or _DEF_SETUPS)["quiet_accumulation"]
    lb = p["lookback"]
    if i < lb:
        return None
    c = ind["close"].iat[i]
    atr = ind["atr14"].iat[i]
    if pd.isna(c) or pd.isna(atr) or atr <= 0:
        return None

    mkt_ret = ctx.cross.get("mkt_ret_series")  # pd.Series indeks-hizalı (piyasa günlük ret)
    if mkt_ret is None:
        return None

    # Verimlilik: olay-çalışması hot-path'i stock_ret'i önceden hesaplayıp context'e koyar
    # (ctx.cross['stock_ret']); yoksa (canlı tarama/test) burada bir kez hesaplanır.
    stock_ret = ctx.cross.get("stock_ret")
    if stock_ret is None:
        stock_ret = ind["close"].pct_change()
    count = 0
    for k in range(i - lb + 1, i + 1):
        if k < 1:
            continue
        sr = stock_ret.iat[k]
        idx = ind.index[k]
        mr = mkt_ret.get(idx)
        vol = ind["vol_tl"].iat[k] if "vol_tl" in ind else None
        avgvol = ind["avg_tl_vol_20"].iat[k]
        if any(v is None or pd.isna(v) for v in (sr, mr, vol, avgvol)) or avgvol <= 0:
            continue
        if mr < p["mkt_down_thr"] and sr >= 0 and vol >= p["vol_mult"] * avgvol:
            count += 1

    if count < p["min_count"]:
        return None
    last_ret = stock_ret.iat[i]
    if last_ret is not None and not pd.isna(last_ret) and last_ret < p["last_down_thr"]:
        return None  # son bar sert düşüş → alım değil

    low10 = float(ind["low"].iloc[max(0, i - lb + 1): i + 1].min())
    stop = low10 - p["atr_stop_mult"] * atr
    strength = 100.0 * float(np.clip(count / float(lb), 0.0, 1.0))
    ctx_out = {"divergence_days": int(count), "lookback": lb,
               "last_ret": round(float(last_ret), 4) if last_ret is not None and not pd.isna(last_ret) else None}
    return _mk_result("quiet_accumulation", c, stop, p["rr"], p["time_exit_days"], p["valid_days"],
                      strength, ctx_out)


# --- 6) htf_squeeze_breakout ("Uzun Sıkışma Kırılımı") — v2 -------------

def detect_htf_squeeze_breakout(ind: pd.DataFrame, i: int, ctx: MarketContext,
                                cfg: dict | None = None) -> dict | None:
    """Kanıtlı squeeze edge'inin (net PF 1.44) uzun-ufuk analojisi: 60g fiyat aralığı kendi
    252g tarihinde aşırı darsa (persentil ≤ 20, DÜNKÜ değer) ve fiyat 60g zirveyi hacimle
    kırarsa. Daha nadir, daha büyük hedefli sinyal — mevcut 20g Bollinger squeeze'le çakışmaz.

    Refüte edilen 52h-yakınlık tetiğine benzemesin diye tetik BİLEŞİKtir (sıkışma + kırılım);
    zirveye-yakınlık tek başına asla tetiklemez. day_gain_max: tavana yakın kapanışlar dışlanır
    (limit-devam literatürü refüte). stop = son 10 barın min low'u − 0.5·ATR (uzun tutuş için
    2-3 ATR bandı; sinyal-barı-low kısa tutuşta işler, uzun time_exit'te gürültüyle stoplanırdı).
    """
    p = (cfg or _DEF_SETUPS)["htf_squeeze_breakout"]
    rlb = int(p["range_lookback_days"])
    blb = int(p["breakout_lookback_days"])
    need = max(rlb, blb) + 1
    if i < need:
        return None
    c = ind["close"].iat[i]
    hi = ind["high"].iat[i]
    lo = ind["low"].iat[i]
    atr = ind["atr14"].iat[i]
    vol = ind["vol_tl"].iat[i] if "vol_tl" in ind else None
    avgvol = ind["avg_tl_vol_20"].iat[i]
    if any(pd.isna(x) for x in (c, hi, lo, atr)) or atr <= 0 or hi <= lo:
        return None
    if vol is None or pd.isna(vol) or pd.isna(avgvol) or avgvol <= 0:
        return None

    # 60g aralık genişliği serisi = (60g max high − 60g min low) / close
    hi_roll = ind["high"].rolling(rlb).max()
    lo_roll = ind["low"].rolling(rlb).min()
    width = ((hi_roll - lo_roll) / ind["close"]).replace([np.inf, -np.inf], np.nan)
    win = width.iloc[max(0, i - int(p["pctile_window_days"])): i]  # i hariç → DÜNKÜ, look-ahead yok
    if len(win) < 120 or pd.isna(win.iloc[-1]):
        return None
    yday_width = win.iloc[-1]
    # persentil YALNIZ geçerli değerler üzerinden (birleşik-takvim NaN'ları paydayı şişirir
    # → persentil aşağı sapar → yanlış sıkışma; squeeze_breakout ile aynı düzeltme).
    w = win.dropna()
    if len(w) < 120:
        return None
    pctile = float((w <= yday_width).mean() * 100.0)
    if not (pctile <= p["width_pctile_max"]):
        return None

    # 60g zirve kırılımı (i hariç prior max close)
    prior_max = float(ind["close"].iloc[max(0, i - blb): i].max())
    if not (c > prior_max):
        return None
    if not (vol >= p["vol_mult"] * avgvol):
        return None
    if not ((c - lo) / (hi - lo) >= p["close_range_min"]):
        return None
    # tavana yakın kapanış (limit-devam) hariç
    day_ret = _pct_change(ind["close"], i, 1)
    if day_ret is not None and day_ret * 100.0 > p["day_gain_max_pct"]:
        return None

    low_n = float(ind["low"].iloc[max(0, i - int(p["stop_lookback_low_days"]) + 1): i + 1].min())
    stop = low_n - p["atr_stop_mult"] * atr

    tightness = float(np.clip((p["width_pctile_max"] - pctile) / p["width_pctile_max"], 0, 1))
    vol_boost = float(np.clip((vol / avgvol - p["vol_mult"]) / p["vol_mult"], 0, 1))
    strength = 100.0 * (0.55 * tightness + 0.30 * vol_boost + 0.15 * ((c - lo) / (hi - lo)))

    ctx_out = {
        "range60_width_pctile": round(pctile, 1), "prior60_max": round(prior_max, 4),
        "vol_mult": round(float(vol / avgvol), 2),
        "day_ret_pct": round(day_ret * 100.0, 1) if day_ret is not None else None,
    }
    return _mk_result("htf_squeeze_breakout", c, stop, p["rr"], p["time_exit_days"],
                      p["valid_days"], strength, ctx_out)


# --- 7) gap_hold_continuation ("Boşluk Tutunması") — v2 ----------------

def detect_gap_hold_continuation(ind: pd.DataFrame, i: int, ctx: MarketContext,
                                 cfg: dict | None = None) -> dict | None:
    """Gün boyu hiç kapanmayan %3-7 hacimli yukarı gap = tek günde tüketilemeyen bilgili akış
    (kurumsal başlatma) izi. Gap-dolumu (gap günü low'u) yapısal, GENİŞ bir stop verir —
    dar-stop scalp değil. Seri ADJUSTED olduğu için temettü/bedelli düzeltme gap'leri zaten
    normalize; buradaki gap GERÇEK fiyat gap'idir (ek ex-div kontrolü gereksiz).

    Tetik: prev_close×1.03 ≤ open ≤ prev_close×1.07 AND low ≥ prev_close (gap hiç dolmadı)
    AND close>open AND close_pos≥0.75 AND vol≥2.5×avg AND günlük getiri ≤ %9 (tavan hariç)
    AND close>ema50 (düşen trendde ölü-kedi gap'i alma). Stop = gap günü low − 0.5·ATR.
    (pead-aktif çifte-sayım engeli CANLI taramada uygulanır — event-study'de tüm gapler test.)
    """
    p = (cfg or _DEF_SETUPS)["gap_hold_continuation"]
    if i < 21:
        return None
    o = ind["open"].iat[i]
    c = ind["close"].iat[i]
    hi = ind["high"].iat[i]
    lo = ind["low"].iat[i]
    atr = ind["atr14"].iat[i]
    prev_c = ind["close"].iat[i - 1]
    ema_len = int(p["ema_filter_len"])
    ema_col = f"ema{ema_len}" if f"ema{ema_len}" in ind else "ema50"
    ema_f = ind[ema_col].iat[i]
    vol = ind["vol_tl"].iat[i] if "vol_tl" in ind else None
    avgvol = ind["avg_tl_vol_20"].iat[i]
    if any(pd.isna(x) for x in (o, c, hi, lo, atr, prev_c, ema_f)) or atr <= 0 or hi <= lo or prev_c <= 0:
        return None
    if vol is None or pd.isna(vol) or pd.isna(avgvol) or avgvol <= 0:
        return None

    gap = o / prev_c - 1.0
    if not (p["gap_min_pct"] / 100.0 <= gap <= p["gap_max_pct"] / 100.0):
        return None
    if not (lo >= prev_c):                 # gap gün içinde hiç dolmadı
        return None
    if not (c > o):                        # yükseliş barı
        return None
    if not ((c - lo) / (hi - lo) >= p["close_range_min"]):
        return None
    if not (vol >= p["vol_mult"] * avgvol):
        return None
    day_ret = c / prev_c - 1.0
    if day_ret * 100.0 > p["day_gain_max_pct"]:   # tavan kapanışı hariç
        return None
    if not (c > ema_f):                    # düşen trendde alma
        return None

    stop = lo - p["atr_stop_mult"] * atr
    gap_str = float(np.clip((gap - p["gap_min_pct"] / 100.0) /
                            ((p["gap_max_pct"] - p["gap_min_pct"]) / 100.0), 0, 1))
    vol_boost = float(np.clip((vol / avgvol - p["vol_mult"]) / p["vol_mult"], 0, 1))
    strength = 100.0 * (0.45 * gap_str + 0.35 * vol_boost + 0.20 * ((c - lo) / (hi - lo)))

    ctx_out = {"gap_pct": round(gap * 100.0, 2), "vol_mult": round(float(vol / avgvol), 2),
               "close_pos": round(float((c - lo) / (hi - lo)), 2)}
    return _mk_result("gap_hold_continuation", c, stop, p["rr"], p["time_exit_days"],
                      p["valid_days"], strength, ctx_out)


# --- 8) rs_shield ("Düşüşte Dirençli Lider") — v2 ----------------------

def detect_rs_shield(ind: pd.DataFrame, i: int, ctx: MarketContext,
                     cfg: dict | None = None) -> dict | None:
    """Piyasa %5+ düşerken yatay/pozitif kalan + EMA50 üstü tutunan isim = satış baskısını
    emen büyük alıcı izi (kesitsel FİYAT direnci; devre-dışı quiet_accumulation'ın hacim
    yaklaşımından farklı). Tetik = piyasanın İLK stabilizasyon günü (piyasa bugün ≥ +%1).
    Toparlanmada düşüşte en dirençli kalanlar tipik olarak önce ve sert kırar.

    Kayıplar bağımsız DEĞİL (piyasa 2. bacak → tüm shield'lar birlikte stoplanır); canlı
    tarama/risk katmanı bunları korelasyon-kovası sayar. stop = son 10g min low − 0.5·ATR.
    """
    p = (cfg or _DEF_SETUPS)["rs_shield"]
    lb = int(p["mkt_ret_lookback_days"])
    if i < lb:
        return None
    c = ind["close"].iat[i]
    ema50 = ind["ema50"].iat[i]
    atr = ind["atr14"].iat[i]
    if any(pd.isna(x) for x in (c, ema50, atr)) or atr <= 0:
        return None

    # piyasa koşulları (ctx): 15g piyasa ≤ −%5 AND piyasa bugün ≥ +%1 (stabilizasyon)
    if not (ctx.mkt_ret_15d <= p["mkt_ret_max"]):
        return None
    if not (ctx.mkt_day_ret >= p["trigger_mkt_day_gain_min"]):
        return None

    # hisse koşulları
    ret15 = _pct_change(ind["close"], i, lb)
    day_ret = _pct_change(ind["close"], i, 1)
    if ret15 is None or day_ret is None:
        return None
    if not (ret15 >= p["stock_ret_min"]):     # düşüşte yatay/pozitif kaldı
        return None
    if not (c > ema50):                        # trend tutuyor
        return None
    if not (day_ret >= 0.0):                   # tetik günü yeşil
        return None

    # kesitsel: göreli güç (ret15 − piyasa15) evren p90 eşiğini geçmeli
    rs = ret15 - ctx.mkt_ret_15d
    rs_cut = ctx.cross.get("rs15_p90")
    if rs_cut is None or rs < rs_cut:
        return None

    low_n = float(ind["low"].iloc[max(0, i - int(p["stop_lookback_low_days"]) + 1): i + 1].min())
    stop = low_n - p["atr_stop_mult"] * atr

    # strength: göreli güç ne kadar yüksekse + trend ne kadar üstündeyse
    rs_str = float(np.clip(rs / 0.10, 0, 1))               # rs 0→10% bandı
    trend_str = float(np.clip((c / ema50 - 1.0) / 0.10, 0, 1))
    strength = 100.0 * (0.65 * rs_str + 0.35 * trend_str)

    ctx_out = {"rs15": round(rs, 4), "rs15_cut": round(float(rs_cut), 4),
               "stock_ret15": round(ret15, 4), "mkt_ret15": round(ctx.mkt_ret_15d, 4),
               "mkt_day_ret": round(ctx.mkt_day_ret, 4)}
    return _mk_result("rs_shield", c, stop, p["rr"], p["time_exit_days"],
                      p["valid_days"], strength, ctx_out)


# --- kayıt (event-study edilen dedektörler) -----------------------------
# pead_drift HARİÇ (PIT yok — dürüst event-study edilemez).
EVENT_STUDY_DETECTORS: dict = {
    "snapback": detect_snapback,
    "squeeze_breakout": detect_squeeze_breakout,
    "trend_pullback": detect_trend_pullback,
    "quiet_accumulation": detect_quiet_accumulation,
    # v2 — mekanizma-temelli, event-study ile TEK koşumda doğrulanır (§9.5)
    "htf_squeeze_breakout": detect_htf_squeeze_breakout,
    "gap_hold_continuation": detect_gap_hold_continuation,
    "rs_shield": detect_rs_shield,
}

# canlı taramada koşulan tüm dedektörler (pead ayrı ele alınır — sue/kap_recent enjekte)
ALL_DETECTORS: dict = dict(EVENT_STUDY_DETECTORS)
