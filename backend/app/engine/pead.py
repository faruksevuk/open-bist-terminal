"""PEAD kazanç-sürprizi (SUE) — KODDAN, Foster-tipi seasonal-naive (v0.2 §7).

isyatirim çeyreklikleri YIL-İÇİ KÜMÜLATİF (X/6 = 6 aylık, X/12 = yıllık) → önce
diskret çeyreğe ayrıştır, sonra mevsimsel-naif beklenti (geçen yıl aynı çeyrek):
  SUE = (gerçek − beklenti) / std(geçmiş tahmin hataları)
sign(SUE) → PEAD yönü (sub_cause pead_term'i besler). LLM SAYI ÜRETMEZ (§0.1).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from app.engine.fscore import ITEM, fetch_financials_df

log = logging.getLogger(__name__)

_QUARTER_ORDER = {3: 1, 6: 2, 9: 3, 12: 4}


def _quarter_cols(df: pd.DataFrame) -> list[tuple[int, int, str]]:
    """(yıl, çeyrek_no, kolon_adı) sıralı liste — X/3,X/6,X/9,X/12."""
    out = []
    for c in df.columns:
        if isinstance(c, str) and "/" in c and c.split("/")[0].isdigit():
            y, m = c.split("/")
            if m.isdigit() and int(m) in _QUARTER_ORDER:
                out.append((int(y), _QUARTER_ORDER[int(m)], c))
    return sorted(out)


def _discrete_quarterly(df: pd.DataFrame, code: str) -> dict[tuple[int, int], float]:
    """Kümülatif çeyreklikleri diskret çeyreğe çevir (Q2=H1-Q1, ...). NaN'lar atılır."""
    idx = df.set_index("FINANCIAL_ITEM_CODE")
    if code not in idx.index:
        return {}
    cum: dict[tuple[int, int], float] = {}
    for y, q, col in _quarter_cols(df):
        v = idx.loc[code, col]
        try:
            cum[(y, q)] = float(v) if v is not None and not pd.isna(v) else np.nan
        except (TypeError, ValueError):
            cum[(y, q)] = np.nan
    disc: dict[tuple[int, int], float] = {}
    for (y, q) in sorted(cum):
        v = cum[(y, q)]
        if q == 1:
            d = v
        else:
            prev = cum.get((y, q - 1), np.nan)
            d = v - prev if not (np.isnan(v) or np.isnan(prev)) else np.nan
        if not np.isnan(d):
            disc[(y, q)] = d
    return disc


def compute_sue(ticker: str, df: pd.DataFrame | None = None, lookback: int = 8) -> dict:
    """En güncel çeyreğin net-kâr SUE'si. sign=+1/0/-1 PEAD yönü."""
    if df is None:
        df = fetch_financials_df(ticker)
    if df is None or df.empty:
        return {"ticker": ticker, "sue": None, "sign": 0, "na_reason": "veri yok"}

    profit = _discrete_quarterly(df, ITEM["net_income"])
    if len(profit) < 6:
        return {"ticker": ticker, "sue": None, "sign": 0, "na_reason": "yetersiz çeyrek"}

    # mevsimsel-naif: beklenti(y,q) = diskret(y-1,q). errors ile err_keys PARALEL tutulur
    # ki latest_err ile etiketlenen çeyrek HER ZAMAN aynı dönem olsun.
    keys = sorted(profit)
    errors: list[float] = []
    err_keys: list[tuple[int, int]] = []
    for (y, q) in keys:
        if (y - 1, q) in profit:
            errors.append(float(profit[(y, q)] - profit[(y - 1, q)]))
            err_keys.append((y, q))
    if len(errors) < 4:
        return {"ticker": ticker, "sue": None, "sign": 0, "na_reason": "yetersiz mevsimsel geçmiş"}

    # PIT dürüstlüğü: SUE yalnızca EN GÜNCEL raporlanan çeyrek için anlamlı. O çeyreğin
    # önceki-yıl mevsimsel eşi yoksa (veri boşluğu) bayat bir çeyrekten katalist ÜRETME.
    if err_keys[-1] != keys[-1]:
        return {"ticker": ticker, "sue": None, "sign": 0,
                "na_reason": "en güncel çeyrek için mevsimsel eş yok"}

    hist = errors[-lookback:]
    sd = float(np.std(hist[:-1], ddof=1)) if len(hist) > 2 else float(np.std(hist, ddof=0))
    latest_err = errors[-1]
    if not sd or np.isnan(sd):
        return {"ticker": ticker, "sue": None, "sign": 0, "na_reason": "std=0"}
    sue = latest_err / sd
    sign = 1 if sue > 0.25 else (-1 if sue < -0.25 else 0)  # nötr bant
    return {
        "ticker": ticker,
        "sue": float(sue),
        "sign": sign,
        "latest_quarter": f"{err_keys[-1][0]}Q{err_keys[-1][1]}",
        "n_quarters": len(profit),
        "na_reason": None,
    }
