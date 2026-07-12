"""Rank-IC backtest — SCORING v0.2 §9 (M5 GERÇEK KAPI).

Tarihsel skoru look-ahead'siz hesaplar, forward-getiriyle Spearman IC ölçer.
PIT-dürüstlük (§9.1): F-Score backtest'e GİRMEZ (tarihsel PIT F yok); fiyat-temelli
bileşenler (oversold + sebep + stabilizasyon) test edilir. Giriş t+1 barında (§9.3).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.metrics import ic_summary, quintile_spread, spearman_ic
from app.data.history import load_daily
from app.db.models import Security
from app.engine.indicators import compute_indicators
from app.engine.scoring import _pct, _z

log = logging.getLogger(__name__)


def _ticker_panel(daily: pd.DataFrame) -> pd.DataFrame:
    ind = compute_indicators(daily)
    if ind.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=ind.index)
    out["adj_close"] = ind["close"]
    out["ret"] = ind["close"].pct_change()
    out["ret_20d"] = ind["close"].pct_change(20)
    out["dist_ema50"] = ind["dist_ema50"]
    out["rsi14"] = ind["rsi14"]
    out["drawdown_20d"] = ind["drawdown_20d"]
    out["dist_52w_high"] = ind["dist_52w_high"]
    out["rsi_delta"] = ind["rsi14"].diff()
    out["vol_dryup"] = ind["vol_tl"] / ind["vol_tl"].rolling(20).mean()
    out["bullish"] = (ind["close"] > ind["open"]).astype(float)
    out["higher_low"] = (ind["low"] > ind["low"].shift(1).rolling(5).min()).astype(float)
    out["roc20"] = ind["roc20"]
    out["roc5"] = ind["roc5"]
    out["macd_hist"] = ind["macd_hist"]
    out["atr_pct"] = ind["atr_pct"]
    return out


def run_factor_diagnostic(session: Session, horizon: int = 5, step: int = 5,
                          min_names: int = 30) -> dict:
    """Tek-faktör IC teşhisi: bu rejimde hangi yön/faktör edge taşıyor?

    Yön kalibrasyonu (research §2 'yönü backtest'le belirle') — reversal mı momentum mu?
    """
    from app.engine.scoring import _pct, _z  # yerel import (döngü önleme)

    tickers = list(session.execute(select(Security.ticker)).scalars().all())
    panels = {}
    for t in tickers:
        d = load_daily(session, t)
        if not d.empty and len(d) >= 220:
            p = _ticker_panel(d)
            if not p.empty:
                panels[t] = p
    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    aligned = {t: p.reindex(common) for t, p in panels.items()}

    factors = ["oversold(reversal)", "momentum(strength)", "roc20", "roc5", "rev5", "macd_hist",
               "low_atr", "stab"]
    ic: dict[str, list[float]] = {f: [] for f in factors}

    for i in range(210, len(common) - horizon - 1, step):
        j, k = i + 1, i + 1 + horizon
        rows = {}
        for tk, a in aligned.items():
            entry, exit_ = a["adj_close"].iat[j], a["adj_close"].iat[k]
            if pd.isna(a["adj_close"].iat[i]) or pd.isna(entry) or pd.isna(exit_) or entry == 0:
                continue
            rows[tk] = {
                "dist_ema50": _nz(a["dist_ema50"].iat[i]), "rsi": _nz(a["rsi14"].iat[i]),
                "dd": _nz(a["drawdown_20d"].iat[i]), "dist52": _nz(a["dist_52w_high"].iat[i]),
                "roc20": _nz(a["roc20"].iat[i]), "roc5": _nz(a["roc5"].iat[i]),
                "macd_hist": _nz(a["macd_hist"].iat[i]),
                "rsi_delta": _nz(a["rsi_delta"].iat[i]),
                "neg_voldry": -_nz(a["vol_dryup"].iat[i], 1.0),
                "bullish": _nz(a["bullish"].iat[i]), "higher_low": _nz(a["higher_low"].iat[i]),
                "atr_pct_neg": -_nz(a["atr_pct"].iat[i]),
                "fwd": exit_ / entry - 1.0,
            }
        if len(rows) < min_names:
            continue
        cs = pd.DataFrame.from_dict(rows, orient="index")
        over = _pct(_z(-cs["dist_ema50"]) + _z(-cs["rsi"]) + _z(-cs["dd"]) + _z(-cs["dist52"]))
        mom = _pct(_z(cs["dist_ema50"]) + _z(cs["rsi"]) + _z(cs["roc20"]) + _z(cs["dist52"]))
        stab = _pct(_z(cs["rsi_delta"]) + _z(cs["neg_voldry"]) + _z(cs["bullish"]) + _z(cs["higher_low"]))
        fac_vals = {
            "oversold(reversal)": over, "momentum(strength)": mom,
            "roc20": _pct(cs["roc20"]), "roc5": _pct(cs["roc5"]),
            "rev5": _pct(-cs["roc5"]),  # kısa-vade dönüş (scoring rev5 = pct(-roc5) ile aynı)
            "macd_hist": _pct(cs["macd_hist"]), "low_atr": _pct(cs["atr_pct_neg"]), "stab": stab,
        }
        for f, sc in fac_vals.items():
            v = spearman_ic(sc.values, cs["fwd"].values)
            if not np.isnan(v):
                ic[f].append(v)

    return {"params": {"horizon": horizon, "step": step, "n_tickers": len(panels)},
            "factors": {f: ic_summary(s, nw_lags=horizon) for f, s in ic.items()}}


def run_ic_backtest(
    session: Session,
    horizon: int = 5,
    step: int = 5,
    min_names: int = 8,
    weights: dict | None = None,
) -> dict:
    w = weights or {"w_o": 0.25, "w_c": 0.25, "w_s": 0.20}
    wsum = w["w_o"] + w["w_c"] + w["w_s"]

    tickers = list(session.execute(select(Security.ticker)).scalars().all())
    panels: dict[str, pd.DataFrame] = {}
    for t in tickers:
        d = load_daily(session, t)
        if not d.empty and len(d) >= 220:
            p = _ticker_panel(d)
            if not p.empty:
                panels[t] = p
    if len(panels) < min_names:
        return {"note": f"yetersiz panel ({len(panels)} ticker)", "n_tickers": len(panels)}

    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    aligned = {t: p.reindex(common) for t, p in panels.items()}
    ret_df = pd.DataFrame({t: a["ret"] for t, a in aligned.items()})
    market_ret = ret_df.mean(axis=1)
    # vektörel kümülatif çarpım (eski rolling.apply(np.prod) Python-callback'i yavaştı)
    market_ret_20d = np.expm1(np.log1p(market_ret).rolling(20).sum())
    corr_market = {t: a["ret"].rolling(40).corr(market_ret) for t, a in aligned.items()}

    ic_series: list[float] = []
    pairs: list[tuple[float, float]] = []
    n_names_used: list[int] = []
    start = 210

    for i in range(start, len(common) - horizon - 1, step):
        t = common[i]
        j, k = i + 1, i + 1 + horizon  # giriş t+1, çıkış t+1+h
        rows: dict[str, dict] = {}
        for tk, a in aligned.items():
            ac_t = a["adj_close"].iat[i]
            entry = a["adj_close"].iat[j]
            exit_ = a["adj_close"].iat[k]
            if pd.isna(ac_t) or pd.isna(entry) or pd.isna(exit_) or entry == 0:
                continue
            rows[tk] = {
                "neg_dist_ema50": -_nz(a["dist_ema50"].iat[i]),
                "neg_rsi": -_nz(a["rsi14"].iat[i]),
                "neg_dd": -_nz(a["drawdown_20d"].iat[i]),
                "neg_dist52": -_nz(a["dist_52w_high"].iat[i]),
                "rsi_delta": _nz(a["rsi_delta"].iat[i]),
                "neg_voldry": -_nz(a["vol_dryup"].iat[i], 1.0),
                "bullish": _nz(a["bullish"].iat[i]),
                "higher_low": _nz(a["higher_low"].iat[i]),
                "corr": _nz(corr_market[tk].iat[i]),
                "ret20": _nz(a["ret_20d"].iat[i]),
                "fwd": exit_ / entry - 1.0,
            }
        if len(rows) < min_names:
            continue
        cs = pd.DataFrame.from_dict(rows, orient="index")
        oversold = _pct(_z(cs["neg_dist_ema50"]) + _z(cs["neg_rsi"]) + _z(cs["neg_dd"]) + _z(cs["neg_dist52"]))
        stab = _pct(_z(cs["rsi_delta"]) + _z(cs["neg_voldry"]) + _z(cs["bullish"]) + _z(cs["higher_low"]))
        base_clean = cs["corr"].clip(0, 1)
        idio = (float(_nz(market_ret_20d.iat[i])) - cs["ret20"]).clip(0, 1)
        cause = (100 * (base_clean - idio).clip(0, 1))
        score = (w["w_o"] * oversold + w["w_c"] * cause + w["w_s"] * stab) / wsum

        ic = spearman_ic(score.values, cs["fwd"].values)
        if not np.isnan(ic):
            ic_series.append(ic)
            n_names_used.append(len(rows))
            pairs.extend(zip(score.values, cs["fwd"].values, strict=True))

    summary = ic_summary(ic_series, nw_lags=horizon)
    return {
        "params": {"horizon": horizon, "step": step, "n_tickers": len(panels),
                   "avg_names_per_date": round(float(np.mean(n_names_used)), 1) if n_names_used else 0},
        "ic": summary,
        "quintile": quintile_spread(pairs, q=5),
        "honesty_note": "F-Score backtest'e dahil değil (PIT yok, §9.1). 2-yıl/tek-rejim "
                        "(disinflasyon) → yön rejime bağlı; edge YARILANARAK yorumlanmalı "
                        "(§9.7 McLean-Pontiff).",
    }


def _nz(v, default: float = 0.0) -> float:
    if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
        return default
    return float(v)
