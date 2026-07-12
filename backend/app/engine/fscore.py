"""Piotroski F-Score (0-9) — isyatirim yıllık bilançosundan, KODDAN (v0.2 §0.1, §3.1).

9 sinyal: 4 kârlılık (ROA>0, CFO>0, ΔROA>0, CFO>NI tahakkuk) + 3 kaldıraç/likidite
(↓uzun borç oranı, ↑cari oran, yeni hisse ihracı yok) + 2 operasyonel (↑brüt marj,
↑aktif devir). Banka/sigorta/GYO için sanayi kalemleri yoksa F = N/A.

PIT (v0.2 §9.1): yıllık sonuç ~ izleyen yıl Şubat-Nisan'da yayınlanır. published_at
≈ (mali_yıl+1)-04-01 PRIOR olarak işaretlenir; KAP modülü (M7) gerçek tarihle düzeltir.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import isyatirimhisse as iy
import pandas as pd
from app.db.upsert import upsert
from sqlalchemy.orm import Session

from app.db.models import Fundamental

log = logging.getLogger(__name__)

# isyatirim FINANCIAL_ITEM_CODE eşlemesi (sanayi/financial_group=1)
ITEM = {
    "net_income": "3L",      # NET PROFIT AFTER TAXES
    "total_assets": "1BL",   # TOTAL ASSETS
    "current_assets": "1A",  # CURRENT ASSETS
    "current_liab": "2A",    # SHORT TERM LIABILITIES
    "long_term_liab": "2B",  # LONG TERM LIABILITIES (kaldıraç proxy)
    "gross_profit": "3D",    # GROSS PROFIT (LOSS)
    "net_sales": "3C",       # Net Sales
    "cfo": "4C",             # Net Cash from Operations
    "share_capital": "2OA",  # Share Capital (ihraç tespiti)
    "rights_issue": "4CBC",  # Rights Issue
}

# PIT yayın gecikmesi (yıllık) — prior, M7'de gerçek KAP tarihiyle değişir
ANNUAL_PUBLISH_MONTH = 4
ANNUAL_PUBLISH_DAY = 1


def fetch_financials_df(ticker: str, years_back: int = 3) -> pd.DataFrame:
    this_year = datetime.now(timezone.utc).year
    try:
        return iy.fetch_financials(
            ticker, start_year=this_year - years_back, end_year=this_year
        )
    except ValueError:
        # isyatirim, sanayi grubunda (financial_group=1) veri yoksa ValueError atar
        # → banka/sigorta/finansal kuruluş; F-Score N/A muamelesi.
        return pd.DataFrame()


def _annual_table(df: pd.DataFrame) -> dict[int, pd.Series]:
    """Yıllık (X/12) kolonları → {yıl: code-indeksli seri}."""
    idx = df.set_index("FINANCIAL_ITEM_CODE")
    out: dict[int, pd.Series] = {}
    for col in idx.columns:
        if isinstance(col, str) and col.endswith("/12"):
            out[int(col.split("/")[0])] = idx[col]
    return out


def _v(series: pd.Series, code: str) -> float | None:
    if code not in series.index:
        return None
    val = series[code]
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_fscore(ticker: str, df: pd.DataFrame | None = None) -> dict:
    """En güncel iki yıllık dönemden Piotroski F. F=None → N/A (banka/sigorta/veri yok)."""
    if df is None:
        df = fetch_financials_df(ticker)
    if df is None or df.empty:
        return {"ticker": ticker, "f_score": None, "na_reason": "veri yok"}

    annual = _annual_table(df)
    years = sorted(y for y in annual if _v(annual[y], ITEM["total_assets"]))
    if len(years) < 2:
        return {"ticker": ticker, "f_score": None, "na_reason": "yetersiz yıllık dönem"}

    t, t1 = years[-1], years[-2]
    cur, prev = annual[t], annual[t1]

    def g(s, key):
        return _v(s, ITEM[key])

    # Sanayi kalemleri yoksa (banka/finansal) → N/A
    if g(cur, "net_sales") is None or g(cur, "current_assets") is None:
        return {"ticker": ticker, "f_score": None, "na_reason": "banka/finansal — sanayi kalemi yok",
                "fiscal_year": t}

    ni_t, ni_p = g(cur, "net_income"), g(prev, "net_income")
    ta_t, ta_p = g(cur, "total_assets"), g(prev, "total_assets")
    cfo_t = g(cur, "cfo")
    ca_t, ca_p = g(cur, "current_assets"), g(prev, "current_assets")
    cl_t, cl_p = g(cur, "current_liab"), g(prev, "current_liab")
    ltl_t, ltl_p = g(cur, "long_term_liab"), g(prev, "long_term_liab")
    gp_t, gp_p = g(cur, "gross_profit"), g(prev, "gross_profit")
    sales_t, sales_p = g(cur, "net_sales"), g(prev, "net_sales")
    cap_t, cap_p = g(cur, "share_capital"), g(prev, "share_capital")
    rights_t = g(cur, "rights_issue") or 0.0

    def safe_div(a, b):
        if a is None or b is None or b == 0:
            return None
        return a / b

    roa_t = safe_div(ni_t, ta_t)
    roa_p = safe_div(ni_p, ta_p)
    cr_t = safe_div(ca_t, cl_t)
    cr_p = safe_div(ca_p, cl_p)
    lev_t = safe_div(ltl_t, ta_t)
    lev_p = safe_div(ltl_p, ta_p)
    gm_t = safe_div(gp_t, sales_t)
    gm_p = safe_div(gp_p, sales_p)
    ato_t = safe_div(sales_t, ta_t)
    ato_p = safe_div(sales_p, ta_p)

    signals: dict[str, int] = {
        "roa_pos": int(roa_t is not None and roa_t > 0),
        "cfo_pos": int(cfo_t is not None and cfo_t > 0),
        "d_roa": int(roa_t is not None and roa_p is not None and roa_t > roa_p),
        "accrual": int(cfo_t is not None and ni_t is not None and ta_t and (cfo_t / ta_t) > (ni_t / ta_t)),
        "d_lever": int(lev_t is not None and lev_p is not None and lev_t < lev_p),
        "d_liquid": int(cr_t is not None and cr_p is not None and cr_t > cr_p),
        "no_issue": int((cap_t is not None and cap_p is not None and cap_t <= cap_p) and rights_t <= 0),
        "d_margin": int(gm_t is not None and gm_p is not None and gm_t > gm_p),
        "d_turn": int(ato_t is not None and ato_p is not None and ato_t > ato_p),
    }
    f = sum(signals.values())
    # accrual_ratio (sub_quality accrual terimi): (NI - CFO)/TA, düşük iyi
    accrual_ratio = None
    if ni_t is not None and cfo_t is not None and ta_t:
        accrual_ratio = (ni_t - cfo_t) / ta_t

    return {
        "ticker": ticker,
        "f_score": f,
        "fiscal_year": t,
        "signals": signals,
        "roa": roa_t,
        "accrual_ratio": accrual_ratio,
        "net_profit": ni_t,
        "na_reason": None,
    }


def populate_fundamentals(session: Session, tickers: list[str]) -> dict[str, int | None]:
    """Tickerlar için F-Score hesapla + fundamentals'a upsert. ticker→F (None=N/A)."""
    result: dict[str, int | None] = {}
    from app.engine.pead import compute_sue  # lokal: döngüsel import önleme

    for t in tickers:
        try:
            df = fetch_financials_df(t)
            r = compute_fscore(t, df)
            sue = compute_sue(t, df)
        except Exception as exc:  # noqa: BLE001
            log.warning("fundamentals hata %s: %s", t, exc)
            result[t] = None
            continue
        result[t] = r.get("f_score")
        fy = r.get("fiscal_year")
        as_of = datetime(fy, 12, 31, tzinfo=timezone.utc) if fy else datetime.now(timezone.utc)
        published = (
            datetime(fy + 1, ANNUAL_PUBLISH_MONTH, ANNUAL_PUBLISH_DAY, tzinfo=timezone.utc)
            if fy else None
        )
        # idempotent: aynı ticker'ın eski satırlarını sil, en güncelini tut
        session.query(Fundamental).filter(Fundamental.ticker == t).delete()
        stmt = upsert(Fundamental).values(
            ticker=t,
            as_of=as_of,
            published_at=published,
            net_profit=r.get("net_profit"),
            piotroski_f=r.get("f_score"),
            accrual_ratio=r.get("accrual_ratio"),
            raw={"signals": r.get("signals"), "na_reason": r.get("na_reason"), "roa": r.get("roa"),
                 "sue": sue.get("sue"), "pead_sign": sue.get("sign"),
                 "pead_quarter": sue.get("latest_quarter")},
        )
        session.execute(stmt)
        session.commit()
    return result
