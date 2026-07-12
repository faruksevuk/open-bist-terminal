"""İşlem ledger + portföy + boyutlandırma API'si (M6).

İşlem giriş modalı (frontend) ve Telegram (M8) bunu kullanır. Sistem emir GÖNDERMEZ;
yalnızca kullanıcının manuel Midas işlemini ledger'a yazar (decision_snapshot ile).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.data.history import load_daily
from app.db.base import get_session
from app.db.models import Position, Side, Trade, TradeSource
from app.engine.indicators import latest_snapshot
from app.risk.portfolio import portfolio_snapshot
from app.risk.sizing import position_size

router = APIRouter(prefix="/api", tags=["trades"])

_DEF_RISK = {"base_r": 0.01, "k_atr": 2.0, "edge_factor_cap": 0.90,
             "max_name_pct": 0.30, "max_heat_pct": 0.06}


class TradeIn(BaseModel):
    ticker: str
    side: str  # buy | sell
    qty: int
    price: float
    fees: float = 0.0
    note: str | None = None
    stop: float | None = None
    source: str = "dashboard"


@router.get("/portfolio")
def portfolio(session: Session = Depends(get_session)) -> dict:
    return portfolio_snapshot(session)


@router.get("/positions")
def positions(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(select(Position)).scalars().all()
    return [{"ticker": p.ticker, "qty": p.qty, "avg_cost": p.avg_cost, "stop": p.stop} for p in rows]


@router.get("/trades")
def trades(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(select(Trade).order_by(Trade.executed_at.desc())).scalars().all()
    return [{
        "id": t.id, "ticker": t.ticker, "side": t.side.value, "qty": t.qty, "price": t.price,
        "fees": t.fees, "executed_at": t.executed_at.isoformat(), "note": t.note,
    } for t in rows]


@router.post("/portfolio/reset")
def reset_portfolio(session: Session = Depends(get_session)) -> dict:
    """Tüm işlemleri + pozisyonları sil (ledger sıfırla). Kendi portföyünü temiz girmek için."""
    n = session.query(Trade).delete()
    session.query(Position).delete()
    session.commit()
    return {"ok": True, "deleted_trades": n}


@router.post("/trades")
def add_trade(body: TradeIn, session: Session = Depends(get_session)) -> dict:
    try:
        side = Side(body.side)
        source = TradeSource(body.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if body.qty <= 0 or body.price <= 0:
        raise HTTPException(status_code=400, detail="qty/price > 0 olmalı")

    t = Trade(
        ticker=body.ticker.upper(), side=side, qty=body.qty, price=body.price,
        executed_at=datetime.now(timezone.utc), fees=body.fees, note=body.note,
        decision_snapshot={"stop": body.stop} if body.stop else None,
        source=source, confirmed=True,
    )
    session.add(t)
    session.commit()
    return {"ok": True, "trade_id": t.id, "portfolio": portfolio_snapshot(session)}


@router.get("/size/{ticker}")
def size_preview(ticker: str, session: Session = Depends(get_session)) -> dict:
    """İşlem modalı için ATR-bazlı boyut önizlemesi (edge OFF → saf ATR)."""
    d = load_daily(session, ticker.upper())
    snap = latest_snapshot(d)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"{ticker}: veri yok")
    risk_cfg = get_config(session, "risk") or _DEF_RISK
    pf = portfolio_snapshot(session, reconcile=False)  # salt-okuma: DB'ye yazmaz
    equity = pf["total_try"]
    from app.data.quotes import current_price
    price = current_price(ticker) or snap["close"]  # canlı, yoksa daily close
    sizing = position_size(equity, price, snap["atr14"], risk_cfg, open_heat_pct=pf["open_heat_pct"])
    return {"ticker": ticker.upper(), "equity": equity, "price": price,
            "daily_close": snap["close"], "atr14": snap["atr14"], "atr_pct": snap["atr_pct"], **sizing}
