"""Skorlama motoru — SCORING v0.2 §3-5 (TEK MODEL).

core = w_q·kalite + w_o·oversold + w_c·sebep + w_s·stabilizasyon
score = clamp( (core + news_pos)·risk_governor + news_neg , 0, 100 )

Oversold & stabilizasyon EVREN-İÇİ percentile (rejim-robust). Kalite & sebep mutlak.
Band, mutlak KAPIDAN SONRA hesaplanır; ≥75 "Güçlü Al" tavanı korunur.

M4 interim (v0.2'ye sadık, M7'de tamamlanır): news=0 (LLM/KAP yok, defansif başlangıç),
pead_term=0 (gerçek SUE M7), valuation neutral (PE/PB kaynağı M7/Midas detail).
Bunlar yapıyı değiştirmez — sadece o bileşenler nötr başlar.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from app.db.upsert import upsert
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.models import Horizon, Score, Signal
from app.engine.features import build_features
from app.engine.gates import apply_gates
from app.seed_config import SEED_CONFIG

# config yoksa kullanılacak varsayılanlar (seed §10 ile aynı)
_DEF_WEIGHTS = {"w_q": 0.30, "w_o": 0.25, "w_c": 0.25, "w_s": 0.20,
                "quality": {"wf": 0.6, "wa": 0.2, "wv": 0.2}}
_DEF_THR = {"base_abs_threshold": 60, "abs_adapt": {"alpha": 4.0, "beta": 3.0,
            "floor_thr": 55, "ceil_thr": 72}, "min_cause": 55, "min_stab": 50,
            "min_fscore": 5, "min_liq_tl": 50_000_000, "min_move_atr_pct": 0.005}  # seed ile aynı
_DEF_VALVE = {"rsi_overbought": 80, "today_spike_pct": 0.08, "extreme_atr_pct": 0.12,
              "mult_rsi": 0.6, "mult_spike": 0.5, "mult_atr": 0.6, "mult_thin_liq": 0.7,
              # aşırı-uzama / ATH-kovalama guard'ı (kullanıcı geri bildirimi: TEHOL RSI80+EMA%25üstü)
              "rsi_high": 72, "mult_rsi_high": 0.85,
              "extended_ema_pct": 0.18, "mult_extended": 0.7,
              "near_high_pct": -0.03, "mult_near_high": 0.8,
              "floor": 0.2}
# Çok-faktör havuzu ağırlıkları — TEK KAYNAK seed_config (fallback ile ASLA sapmaz).
# ESKİ BUG: bu literal momentum'du ama canlı motor DB'deki seed'i (low_vol) okuyordu →
# kullanıcının değişikliği fiilen ölüydü. Artık ikisi de aynı dict. NOT: canlı motor
# get_config('factor_weights')'i okur; bu SADECE DB'de hiç config yokken (soğuk başlangıç)
# devreye giren fallback'tir. Ağırlıklar placeholder PRIOR — ölçümle (factor diagnostic) doğrulanacak.
_DEF_FACTOR_WEIGHTS = SEED_CONFIG["factor_weights"]


# --- istatistik yardımcıları -------------------------------------------

def _z(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=0)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / sd).fillna(0.0)


def _pct(s: pd.Series) -> pd.Series:
    return (s.rank(pct=True) * 100).fillna(50.0)


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi == lo:
        return pd.Series(0.5, index=s.index)
    return ((s - lo) / (hi - lo)).fillna(0.5)


# --- alt-skorlar --------------------------------------------------------

def _sub_quality(df: pd.DataFrame, w: dict) -> pd.Series:
    q = w.get("quality", _DEF_WEIGHTS["quality"])
    f_term = (df["f_score"] / 9).where(df["f_score"].notna(), 0.5).clip(0, 1)
    accrual_score = 1 - _minmax(df["accrual"])      # düşük accrual iyi
    accrual_score = accrual_score.where(df["accrual"].notna(), 0.5)
    valuation_score = pd.Series(0.5, index=df.index)  # interim (PE/PB M7)
    return 100 * (q["wf"] * f_term + q["wa"] * accrual_score + q["wv"] * valuation_score)


def _sub_oversold(df: pd.DataFrame) -> pd.Series:
    # Düşen bıçak filtresi: Fiyat EMA50'nin altındaysa oversold = 0
    is_uptrend = (df["dist_ema50"].fillna(-1) > 0.0)
    raw = (
        _z(-df["dist_ema50"])       # 50EMA altında
        + _z(-df["rsi14"])          # düşük RSI
        + _z(-df["drawdown_20d"])   # sert düşüş
        + _z(-df["dist_52w_high"])  # zirveden uzak
    )
    return _pct(raw).where(is_uptrend, 0.0)


def _sub_stab(df: pd.DataFrame) -> pd.Series:
    raw = (
        _z(df["rsi_delta"].fillna(0))      # RSI dönüyor
        + _z(-df["vol_dryup"].fillna(1))   # hacim kuruyor (düşük vol_dryup iyi)
        + _z(df["bullish_bar"].fillna(0))  # yükseliş mumu
        + _z(df["higher_low"].fillna(0))   # daha yüksek dip
    )
    return _pct(raw)


def _momentum_strength(df: pd.DataFrame) -> pd.Series:
    """Bileşik trend-gücü — diagnostic 'momentum(strength)' ile birebir (IC +0.016, t=1.60).
    EMA50-mesafesi + RSI + 20g ROC + 52h-mesafesinin z-toplamının evren-içi percentile'ı.
    Düz roc20'den (IC +0.008, t=0.89) daha güçlü ölçüldü."""
    raw = (
        _z(df["dist_ema50"].fillna(0.0))
        + _z(df["rsi14"].fillna(50.0))
        + _z(df["roc20"].fillna(0.0))
        + _z(df["dist_52w_high"].fillna(0.0))
    )
    return _pct(raw)


def _factor_value(df: pd.DataFrame) -> pd.Series:
    """Ucuzluk (düşük PE+PB). Her metrik KENDİ dolu alt-kümesinde percentile'a çevrilir;
    eksik metrik için z=0 (nötr) ENJEKTE EDİLMEZ — yalnız var olan metriklerin ortalaması
    alınır. İkisi de yoksa gerçek nötr (50). Negatif/sıfır PE/PB → veri yok sayılır."""
    out = pd.Series(50.0, index=df.index)
    if "pe" not in df or "pb" not in df:
        return out
    pe = df["pe"].where(df["pe"] > 0)
    pb = df["pb"].where(df["pb"] > 0)
    if int(pe.notna().sum()) + int(pb.notna().sum()) < 3:
        return out
    # düşük PE/PB → yüksek percentile (ucuz). NaN metrik rank'ta NaN kalır (na_option='keep').
    pe_pct = (-pe).rank(pct=True) * 100
    pb_pct = (-pb).rank(pct=True) * 100
    val = pd.concat([pe_pct, pb_pct], axis=1).mean(axis=1)  # skipna → yalnız var olan metrikler
    out.loc[val.notna()] = val[val.notna()]
    return out


def _sub_cause(df: pd.DataFrame) -> pd.Series:
    base_clean = df["corr_market"].clip(lower=0, upper=1).fillna(0.0)  # rotasyon/genel kâr-satışı
    pead_term = pd.Series(0.0, index=df.index)  # interim (gerçek SUE M7)
    # idiyosenkratik ceza: piyasadan çok düştüyse + pozitif katalist yoksa (M4'te hep uygula)
    idio = (df["market_ret_20d"] - df["ret_20d"]).clip(lower=0, upper=1).fillna(0.0)
    return (100 * (base_clean + pead_term - idio).clip(0, 1)).astype(float)


def _risk_governor(df: pd.DataFrame, v: dict, thr: dict) -> pd.Series:
    rg = pd.Series(1.0, index=df.index)
    rg *= np.where(df["rsi14"] > v["rsi_overbought"], v["mult_rsi"], 1.0)            # >80 aşırı-alım
    rg *= np.where((df["rsi14"] > v.get("rsi_high", 72)) & (df["rsi14"] <= v["rsi_overbought"]),
                   v.get("mult_rsi_high", 0.85), 1.0)                                # 72-80 yumuşak
    rg *= np.where(df["last_ret"].fillna(0) > v["today_spike_pct"], v["mult_spike"], 1.0)
    rg *= np.where(df["atr_pct"] > v["extreme_atr_pct"], v["mult_atr"], 1.0)
    # AŞIRI-UZAMA: EMA50'nin çok üstünde → "yukarıyı kovalama" cezası (TEHOL tipi)
    rg *= np.where(df["dist_ema50"] > v.get("extended_ema_pct", 0.18), v.get("mult_extended", 0.7), 1.0)
    # 52-HAFTA ZİRVESİNE çok yakın → ATH-kovalama cezası
    rg *= np.where(df["dist_52w_high"] > v.get("near_high_pct", -0.03), v.get("mult_near_high", 0.8), 1.0)
    # DÜŞEN BIÇAK / TREND KIRILIM CEZASI: EMA50'nin %5 altında ise
    rg *= np.where(df["dist_ema50"].fillna(0) < -0.05, 0.6, 1.0)
    # HACİMLİ SERT DÜŞÜŞ (Kaçış Modu): Düşüş > %3 ve Hacim > 1.5x ortalama
    rg *= np.where((df["last_ret"].fillna(0) < -0.03) & (df["vol_dryup"].fillna(0) > 1.5), 0.2, 1.0)
    # SEKTÖR ROTASYON BARİYERİ: Zayıf sektörden alım yapmayı reddet (RS Percentile < 30)
    if "sector_rs_percentile" in df:
        rg *= np.where(df["sector_rs_percentile"].fillna(50) < 30.0, 0.2, 1.0)
    
    min_liq = thr.get("min_liq_tl", 50_000_000)
    marginal = (df["avg_tl_vol_20"] >= min_liq) & (df["avg_tl_vol_20"] < 1.5 * min_liq)
    rg *= np.where(marginal, v["mult_thin_liq"], 1.0)
    return pd.Series(rg, index=df.index).clip(lower=v["floor"], upper=1.0)


def _adaptive_threshold(df: pd.DataFrame, thr: dict) -> float:
    """Breadth/volatiliteye göre uyarlanabilir mutlak eşik (interim; tam z-score M10)."""
    a = thr.get("abs_adapt", _DEF_THR["abs_adapt"])
    base = thr.get("base_abs_threshold", 60)
    breadth = df.attrs.get("breadth", 0.5)
    mvol = df.attrs.get("market_vol_20d", 0.0)
    vol_adj = a["alpha"] * max(0.0, mvol - 0.02) * 100      # yüksek vol → eşik yüksel
    breadth_adj = a["beta"] * (0.5 - breadth) * 20          # zayıf breadth → yüksel
    return float(np.clip(base + vol_adj + breadth_adj, a["floor_thr"], a["ceil_thr"]))


_BANDS = [(75, "strong_buy"), (60, "buy"), (45, "hold"), (30, "reduce"), (0, "sell")]


def _signal_after_gate(score: float, meets: bool) -> str:
    """Band KAPIDAN SONRA: kapı geçilmezse Al tarafına çıkamaz (v0.2 §5.3)."""
    if meets:
        return "strong_buy" if score >= 75 else "buy"
    if score < 30:
        return "sell"
    if score < 45:
        return "reduce"
    return "hold"


# --- ana skorlama -------------------------------------------------------

def score_universe(session: Session) -> pd.DataFrame:
    weights = get_config(session, "weights") or _DEF_WEIGHTS
    thr = get_config(session, "thresholds") or _DEF_THR
    valve = get_config(session, "risk_valve") or _DEF_VALVE
    news_cfg = get_config(session, "news") or {}

    df = build_features(session)
    if df.empty:
        return df

    df = apply_gates(df, thr)

    # --- faktör havuzu (tez-bağımsız; ağırlıklar kalibrasyonla öğrenilir) ---
    df["sub_quality"] = _sub_quality(df, weights)
    df["sub_oversold"] = _sub_oversold(df)          # reversal faktörü
    df["sub_cause"] = _sub_cause(df)
    df["sub_stab"] = _sub_stab(df)
    df["factor_low_vol"] = _pct(-df["atr_pct"])     # KANITLI edge (low-vol/BAB; IC +0.056 t=4.25)
    # momentum = BİLEŞİK trend-gücü (dist_ema50 + rsi + roc20 + dist_52w) — diagnostic
    # 'momentum(strength)' ile birebir (IC +0.016 t=1.60; düz roc20'den güçlü).
    df["factor_momentum"] = _momentum_strength(df)
    df["factor_roc20"] = _pct(df["roc20"])          # düz 20g momentum (ayrı; IC +0.008 t=0.89, zayıf)
    df["factor_pead"] = _pct(df["sue"]) if "sue" in df else pd.Series(50.0, index=df.index)  # kazanç sürprizi (katalist)
    df["factor_value"] = _factor_value(df)  # ucuzluk (PE/PB)
    # rev5: kısa-vade dönüş (5-günün kaybedenleri sıçrıyor — ölçülü edge IC +0.016 t=2.14)
    df["factor_rev5"] = _pct(-df["roc5"]) if "roc5" in df else pd.Series(50.0, index=df.index)
    df["risk_governor"] = _risk_governor(df, valve, thr)
    # KAP/haber faktörü (aktif olaylardan, decay'li; olay yoksa 0 — defansif)
    from app.news.events import news_map  # lokal import (opsiyonel katman)
    nm = news_map(session)
    df["news_pos"] = [nm.get(t, (0.0, 0.0))[0] for t in df.index]
    df["news_neg"] = [nm.get(t, (0.0, 0.0))[1] for t in df.index]

    factors = {
        "low_vol": df["factor_low_vol"],
        "momentum": df["factor_momentum"],   # bileşik güç
        "roc20": df["factor_roc20"],         # düz 20g momentum (ayrı, zayıf)
        "quality": df["sub_quality"],
        "reversal": df["sub_oversold"],
        "stab": df["sub_stab"],
        "cause": df["sub_cause"],
        "pead": df["factor_pead"],
        "value": df["factor_value"],
        "rev5": df["factor_rev5"],
    }
    fw = get_config(session, "factor_weights") or _DEF_FACTOR_WEIGHTS
    wsum = sum(fw.get(f, 0.0) for f in factors) or 1.0
    core = sum(fw.get(f, 0.0) * factors[f] for f in factors) / wsum
    df["core"] = core
    df["score"] = ((core + df["news_pos"]) * df["risk_governor"] + df["news_neg"]).clip(0, 100)
    df.attrs["factor_weights"] = fw

    thr_eff = _adaptive_threshold(df, thr)
    df.attrs["abs_threshold_eff"] = thr_eff
    min_stab = thr.get("min_stab", 50)
    # mutlak kapı: skor eşiği + stabilizasyon tabanı ("bıçak yakalama" guard).
    # min_cause hard-gate'i KALDIRILDI — sebep artık ağırlıklı bir faktör (tez-bağımsız).
    df["meets_absolute_threshold"] = (
        df["passed_gates"] & (df["score"] >= thr_eff) & (df["sub_stab"] >= min_stab)
    )
    df["signal"] = [
        _signal_after_gate(s, m)
        for s, m in zip(df["score"], df["meets_absolute_threshold"], strict=True)
    ]
    return df.sort_values("score", ascending=False)


def _ff(v) -> float | None:
    """numpy/NaN → JSON/DB-güvenli python float."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def persist_scores(session: Session, df: pd.DataFrame, horizon: str = "swing") -> int:
    """score_universe çıktısını `scores` tablosuna yaz (her run yeni as_of)."""
    if df.empty:
        return 0
    as_of = datetime.now(timezone.utc)
    thr_eff = df.attrs.get("abs_threshold_eff")
    rows = []
    for ticker, r in df.iterrows():
        rows.append(
            {
                "ticker": ticker,
                "as_of": as_of,
                "horizon": Horizon(horizon),
                "score": _ff(r["score"]),
                "passed_gates": bool(r["passed_gates"]),
                "signal": Signal(r["signal"]),
                "sub_quality": _ff(r["sub_quality"]),
                "sub_oversold": _ff(r["sub_oversold"]),
                "sub_cause": _ff(r["sub_cause"]),
                "sub_stab": _ff(r["sub_stab"]),
                "news_pos": _ff(r["news_pos"]),
                "news_neg": _ff(r["news_neg"]),
                "risk_governor": _ff(r["risk_governor"]),
                "meets_absolute_threshold": bool(r["meets_absolute_threshold"]),
                "reasoning": {
                    "f_score": r.get("f_score") if pd.notna(r.get("f_score")) else None,
                    "atr_pct": _ff(r.get("atr_pct")),
                    "gate_reasons": list(r.get("gate_reasons") or []),
                    "abs_threshold_eff": _ff(thr_eff),
                    "factors": {
                        "low_vol": _ff(r.get("factor_low_vol")),
                        "momentum": _ff(r.get("factor_momentum")),
                        "roc20": _ff(r.get("factor_roc20")),
                        "quality": _ff(r.get("sub_quality")),
                        "reversal": _ff(r.get("sub_oversold")),
                        "stab": _ff(r.get("sub_stab")),
                        "cause": _ff(r.get("sub_cause")),
                        "pead": _ff(r.get("factor_pead")),
                        "value": _ff(r.get("factor_value")),
                        "rev5": _ff(r.get("factor_rev5")),
                    },
                    "factor_weights": df.attrs.get("factor_weights"),
                },
            }
        )
    stmt = upsert(Score).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Score.ticker, Score.as_of, Score.horizon],
        set_={
            "score": stmt.excluded.score, "signal": stmt.excluded.signal,
            "passed_gates": stmt.excluded.passed_gates,
            "meets_absolute_threshold": stmt.excluded.meets_absolute_threshold,
            "reasoning": stmt.excluded.reasoning,
        },
    )
    session.execute(stmt)
    session.commit()
    return len(rows)
