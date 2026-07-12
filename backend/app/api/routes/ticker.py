"""Ticker 'Pro View' — chart serileri + agregat detay (terminal sayfası için)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.data.history import load_daily
from app.data.quotes import current_price, valuation
from app.db.base import get_session
from app.db.models import Fundamental, Horizon, KapEvent, Position, Score
from app.engine.indicators import compute_indicators
from app.risk.portfolio import portfolio_snapshot
from app.risk.sizing import position_size

router = APIRouter(prefix="/api/ticker", tags=["ticker"])

_DEF_RISK = {"base_r": 0.01, "k_atr": 2.0, "edge_factor_cap": 0.90,
             "max_name_pct": 0.30, "max_heat_pct": 0.06}


def _f(v):
    if v is None or (isinstance(v, float) and (pd.isna(v) or v != v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/{symbol}/chart")
def chart(symbol: str, session: Session = Depends(get_session), bars: int = 250) -> dict:
    """Mum + EMA20/50/200 + RSI + hacim serileri (lightweight-charts formatı)."""
    ind = compute_indicators(load_daily(session, symbol.upper()))
    if ind.empty:
        raise HTTPException(status_code=404, detail=f"{symbol}: bar yok")
    tail = ind.tail(bars)
    t = [str(d) for d in tail.index]
    candles, volume, ema20, ema50, ema200, rsi = [], [], [], [], [], []
    for i, (idx, r) in enumerate(tail.iterrows()):
        candles.append({"time": t[i], "open": _f(r["open"]), "high": _f(r["high"]),
                        "low": _f(r["low"]), "close": _f(r["close"])})
        up = r["close"] >= r["open"]
        volume.append({"time": t[i], "value": _f(r["volume"]), "color": "#5E8C6A55" if up else "#A23B4355"})
        if pd.notna(r["ema20"]): ema20.append({"time": t[i], "value": _f(r["ema20"])})
        if pd.notna(r["ema50"]): ema50.append({"time": t[i], "value": _f(r["ema50"])})
        if pd.notna(r["ema200"]): ema200.append({"time": t[i], "value": _f(r["ema200"])})
        if pd.notna(r["rsi14"]): rsi.append({"time": t[i], "value": _f(r["rsi14"])})
    return {"symbol": symbol.upper(), "candles": candles, "volume": volume,
            "ema20": ema20, "ema50": ema50, "ema200": ema200, "rsi": rsi}


@router.get("/{symbol}")
def detail(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Tek hissenin tüm hesaplanmış bağlamı: skor/faktör + fundamental + valuation + getiriler + sizing."""
    t = symbol.upper()
    ind = compute_indicators(load_daily(session, t))
    if ind.empty:
        raise HTTPException(status_code=404, detail=f"{t}: veri yok")
    last = ind.iloc[-1]
    adj = ind["close"]

    score = session.execute(
        select(Score).where(Score.ticker == t, Score.horizon == Horizon.swing)
        .order_by(Score.as_of.desc()).limit(1)
    ).scalar()
    fund = session.execute(
        select(Fundamental).where(Fundamental.ticker == t).order_by(Fundamental.as_of.desc()).limit(1)
    ).scalar()
    pos = session.get(Position, t)
    val = valuation(t)
    price = val.get("last") or current_price(t) or _f(last["close"])

    # getiriler + 52h konumu (pro metrikler)
    def ret(n):
        return _f(adj.pct_change(n).iloc[-1]) if len(adj) > n else None
    yh, yl = val.get("year_high"), val.get("year_low")
    range_pos = None
    if yh and yl and yh > yl and price:
        range_pos = round((price - yl) / (yh - yl), 3)

    # sizing — salt-okunur snapshot (reconcile=False: DB'ye yazmaz), açık-heat sizing'e beslenir
    risk_cfg = get_config(session, "risk") or _DEF_RISK
    pf = portfolio_snapshot(session, reconcile=False)
    sizing = position_size(pf["total_try"], price or 0, _f(last["atr14"]) or 0, risk_cfg,
                           open_heat_pct=pf["open_heat_pct"])

    # tickers artık JSON dizisi (dialect-agnostik) → son olayları çekip Python'da filtrele
    _recent_kap = session.execute(
        select(KapEvent).order_by(KapEvent.published_at.desc()).limit(500)
    ).scalars().all()
    kap = [e for e in _recent_kap if t in (e.tickers or [])][:8]
    now = datetime.now(timezone.utc)

    return {
        "ticker": t,
        "price": _f(price), "change_pct": _f(val.get("change_pct")),
        "score": _f(score.score) if score else None,
        "signal": score.signal.value if score and score.signal else None,
        "meets_absolute_threshold": score.meets_absolute_threshold if score else None,
        "factors": (score.reasoning or {}).get("factors") if score else None,
        "factor_weights": (score.reasoning or {}).get("factor_weights") if score else None,
        "news_pos": _f(score.news_pos) if score else None,
        "news_neg": _f(score.news_neg) if score else None,
        "risk_governor": _f(score.risk_governor) if score else None,
        "indicators": {
            "rsi14": _f(last["rsi14"]), "atr_pct": _f(last["atr_pct"]), "adx14": _f(last["adx14"]),
            "ema50": _f(last["ema50"]), "ema200": _f(last["ema200"]),
            "dist_ema50": _f(last["dist_ema50"]), "dist_52w_high": _f(last["dist_52w_high"]),
            "macd_hist": _f(last["macd_hist"]),
        },
        "returns": {"1w": ret(5), "1m": ret(21), "3m": ret(63)},
        "range_position_52w": range_pos,
        "fundamentals": {
            "f_score": fund.piotroski_f if fund else None,
            "pe": _f(fund.pe) if fund else None, "pb": _f(fund.pb) if fund else None,
            "accrual": _f(fund.accrual_ratio) if fund else None,
            "roa": (fund.raw or {}).get("roa") if fund else None,
            "pead_sign": (fund.raw or {}).get("pead_sign") if fund else None,
        },
        "valuation": {k: _f(v) for k, v in val.items()},
        "sizing": sizing,
        "position": ({"qty": pos.qty, "avg_cost": _f(pos.avg_cost), "stop": _f(pos.stop)} if pos else None),
        "kap": [{"title": e.title, "direction": _f(e.direction), "mechanism": e.mechanism,
                 "active": bool(e.effective_until and e.effective_until > now), "url": e.raw_url}
                for e in kap],
    }
