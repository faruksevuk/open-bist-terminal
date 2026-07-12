"""Canlı setup taraması — SETUPS v0.1. En güncel barda dedektörleri koştur + persist.

scan_universe(session):
- Her ticker için indikatör paneli kur (load_daily + compute_indicators; <min_bars atla).
- Kesitsel piyasa bağlamını (mkt_ret_5d, mkt_above_ema50, breadth) + roc3 alt-desil eşiğini
  bir kez hesapla (features.py eş-ağırlık mantığıyla).
- Son barda tüm dedektörleri koştur; ortak kapılar (likidite, bars, excluded) + taze negatif
  haber bloğu (news_map) uygula.
- pead_drift CANLI-only: fundamentals raw.sue + son 3 işlem günü finansal_tablo KAP olayı
  enjekte edilir.
- setup_signals'e upsert (aynı (ticker,setup,triggered_at) → on_conflict_do_nothing).
- valid_until < son bar tarihi olan sinyalleri deaktive et.
Döner: özet DataFrame.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import and_, select, update
from app.db.upsert import upsert
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.data.history import load_daily
from app.db.models import Fundamental, KapEvent, KapType, Security, SetupSignal
from app.engine.indicators import compute_indicators
from app.engine.setups import (
    _DEF_SETUPS,
    ALL_DETECTORS,
    MarketContext,
    detect_pead_drift,
)
from app.news.events import news_map

log = logging.getLogger(__name__)


def _pead_inputs(session: Session, tickers: list[str], last_date) -> dict[str, dict]:
    """Ticker → {sue, kap_recent}. SUE latest-snapshot fundamentals.raw; kap_recent =
    son 3 işlem günü içinde finansal_tablo KAP olayı var mı."""
    # SUE (latest per ticker)
    sue: dict[str, float] = {}
    rows = session.execute(
        select(Fundamental.ticker, Fundamental.as_of, Fundamental.raw)
    ).all()
    latest: dict[str, tuple] = {}
    for tk, as_of, raw in rows:
        if tk not in latest or as_of > latest[tk][0]:
            latest[tk] = (as_of, raw)
    for tk, (_, raw) in latest.items():
        v = (raw or {}).get("sue")
        if v is not None:
            try:
                sue[tk] = float(v)
            except (TypeError, ValueError):
                pass

    # son 3 işlem günü ~ 5 takvim günü penceresi (KAP finansal_tablo)
    window_start = pd.Timestamp(last_date) - timedelta(days=6)
    kap_rows = session.execute(
        select(KapEvent.tickers).where(
            KapEvent.type == KapType.finansal_tablo,
            KapEvent.published_at >= window_start,
        )
    ).all()
    kap_recent: set[str] = set()
    for (tks,) in kap_rows:
        for t in (tks or []):
            kap_recent.add(t.upper())

    return {t: {"sue": sue.get(t), "kap_recent": t in kap_recent} for t in tickers}


def scan_universe(session: Session, min_bars: int | None = None) -> pd.DataFrame:
    """Evreni tara → setup_signals upsert. Özet DataFrame döner."""
    setups_cfg = get_config(session, "setups") or _DEF_SETUPS
    common_cfg = setups_cfg.get("common", _DEF_SETUPS["common"])
    mb = int(min_bars if min_bars is not None else common_cfg.get("min_bars", 200))
    block_news_neg = float(common_cfg.get("block_news_neg", -5))
    min_liq = float(common_cfg.get("min_liq_tl", 50_000_000))

    secs = session.execute(select(Security.ticker, Security.excluded)).all()
    excluded_map = {t: bool(x) for t, x in secs}
    tickers = [t for t, _ in secs]

    panels: dict[str, pd.DataFrame] = {}
    for t in tickers:
        d = load_daily(session, t)
        if d.empty or len(d) < mb:
            continue
        ind = compute_indicators(d)
        if not ind.empty and len(ind) >= mb:
            panels[t] = ind
    if not panels:
        log.warning("setup taraması: panel yok")
        return pd.DataFrame()

    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    last_date = common[-1]

    # --- kesitsel piyasa bağlamı (son bar) ---
    ret_df = pd.DataFrame({t: p["close"].reindex(common).pct_change() for t, p in panels.items()})
    market_ret = ret_df.mean(axis=1)
    eq_index = (1.0 + market_ret.fillna(0.0)).cumprod()
    eq_ema50 = eq_index.ewm(span=50, adjust=False).mean()
    mkt_above_ema50 = bool(eq_index.iloc[-1] > eq_ema50.iloc[-1])
    mkt_ret_5d = float(np.expm1(np.log1p(market_ret).rolling(5).sum()).iloc[-1])
    mkt_ret_15d = float(np.expm1(np.log1p(market_ret).rolling(15).sum()).iloc[-1])
    mkt_day_ret = float(market_ret.iloc[-1]) if not pd.isna(market_ret.iloc[-1]) else 0.0
    last_ret = ret_df.iloc[-1]
    breadth = float((last_ret > 0).sum() / max(1, last_ret.notna().sum()))

    roc3_df = pd.DataFrame({t: p["close"].reindex(common).pct_change(3) for t, p in panels.items()})
    decile = setups_cfg.get("snapback", {}).get("roc3_decile", 0.10)
    roc3_p10 = roc3_df.iloc[-1].quantile(decile)
    roc3_min = float(roc3_df.iloc[-1].min())

    # rs_shield kesitsel eşiği: (hisse 15g − piyasa 15g) evren p90
    ret15_last = pd.DataFrame(
        {t: p["close"].reindex(common).pct_change(15) for t, p in panels.items()}).iloc[-1]
    rs15_last = ret15_last - (mkt_ret_15d if not pd.isna(mkt_ret_15d) else 0.0)
    rs_pctile = setups_cfg.get("rs_shield", {}).get("rs_pctile_min", 90) / 100.0
    rs15_p90 = rs15_last.quantile(rs_pctile)

    ctx = MarketContext(
        mkt_ret_5d=mkt_ret_5d if not pd.isna(mkt_ret_5d) else 0.0,
        mkt_ret_15d=mkt_ret_15d if not pd.isna(mkt_ret_15d) else 0.0,
        mkt_day_ret=mkt_day_ret,
        mkt_above_ema50=mkt_above_ema50,
        breadth=breadth,
        cross={
            "roc3_p10": float(roc3_p10) if not pd.isna(roc3_p10) else None,
            "roc3_min": roc3_min if not pd.isna(roc3_min) else None,
            "rs15_p90": float(rs15_p90) if not pd.isna(rs15_p90) else None,
            "mkt_ret_series": market_ret,
        },
    )

    # taze negatif haber → blok
    nm = news_map(session)
    # PEAD canlı girdileri
    pead_in = _pead_inputs(session, list(panels.keys()), last_date)

    rows: list[dict] = []
    for ticker, ind in panels.items():
        pan = ind.reindex(common)
        i = len(common) - 1
        # ortak kapılar
        if excluded_map.get(ticker):
            continue
        if not _last_liquid(pan, i, min_liq):
            continue
        news_neg = nm.get(ticker, (0.0, 0.0))[1]
        if news_neg <= block_news_neg:
            continue  # taze negatif haber → setup bloke

        found: list[dict] = []
        for name, detector in ALL_DETECTORS.items():
            res = detector(pan, i, ctx, setups_cfg)
            if res is not None:
                found.append(res)
        # pead_drift canlı-only (sue + kap enjekte)
        pin = pead_in.get(ticker, {})
        pres = detect_pead_drift(pan, i, ctx, setups_cfg,
                                 sue=pin.get("sue"), kap_recent=bool(pin.get("kap_recent")))
        if pres is not None:
            found.append(pres)

        # çifte-sayım engeli: aynı isimde hem PEAD hem Boşluk Tutunması tetiklerse, kazanç
        # gap'i PEAD'e bırakılır (gap sinyali düşer) — kazanç sürprizi gap'ini iki kez sayma.
        if pres is not None and any(f["setup"] == "gap_hold_continuation" for f in found):
            found = [f for f in found if f["setup"] != "gap_hold_continuation"]

        for res in found:
            valid_until = last_date + _trading_days(res["valid_days"])
            rows.append({
                "ticker": ticker,
                "setup": res["setup"],
                "triggered_at": last_date,
                "strength": res["strength"],
                "entry_ref": res["entry_ref"],
                "stop": res["stop"],
                "target": res["target"],
                "time_exit_days": res["time_exit_days"],
                "valid_until": valid_until,
                "context": res["context"],
                "active": True,
            })

    # --- upsert (aynı key → do nothing; sinyal parametreleri triggered_at gününde sabittir) ---
    if rows:
        stmt = upsert(SetupSignal).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[SetupSignal.ticker, SetupSignal.setup, SetupSignal.triggered_at]
        )
        session.execute(stmt)

    # --- süresi dolan sinyalleri deaktive et ---
    session.execute(
        update(SetupSignal)
        .where(and_(SetupSignal.active.is_(True), SetupSignal.valid_until < last_date))
        .values(active=False)
    )
    session.commit()

    # --- güncel piyasa bağlamını API için sakla (GET /api/setups → market) ---
    set_config(session, "setup_market", {
        "as_of": str(last_date),
        "mkt_ret_5d": round(ctx.mkt_ret_5d, 4),
        "mkt_above_ema50": ctx.mkt_above_ema50,
        "breadth": round(ctx.breadth, 3),
    })

    if not rows:
        return pd.DataFrame(columns=["ticker", "setup", "strength", "entry_ref", "stop", "target"])
    return pd.DataFrame(rows)[
        ["ticker", "setup", "strength", "entry_ref", "stop", "target", "triggered_at", "valid_until"]
    ].sort_values("strength", ascending=False)


def _last_liquid(pan: pd.DataFrame, i: int, min_liq: float) -> bool:
    v = pan["avg_tl_vol_20"].iat[i] if "avg_tl_vol_20" in pan else None
    return bool(v is not None and not pd.isna(v) and v >= min_liq)


def _trading_days(n: int) -> pd.Timedelta:
    """valid_days'i takvim günü penceresine yaklaşık çevir (n işlem günü ~ n*7/5 takvim)."""
    return pd.Timedelta(days=int(round(n * 7 / 5)) + 1)
