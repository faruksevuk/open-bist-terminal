"""Portföy — ledger'dan pozisyon reconcile + reel/USD/TRY P&L + heat (v0.2 §6).

Pozisyonlar trades'ten (append-only ledger) türetilir. P&L üç biçimde: nominal TRY,
USD (FX ile), reel (CPI ile) — nominal TRY tek başına yanıltıcı (v0.2 §0.1/§6.3).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.data.fx_macro import cpi_deflator, get_usdtry
from app.db.models import DailyBar, Horizon, Position, Score, Side, Trade

_DEF_ACCOUNT = {"starting_cash_try": 10_000.0, "start_date": "2026-06-22", "annual_cpi": 0.35}


def _compute_positions(session: Session) -> list[dict]:
    """trades → açık pozisyonlar (ağırlıklı ort. maliyet). SALT HESAP — DB'ye dokunmaz."""
    trades = session.execute(select(Trade).order_by(Trade.executed_at)).scalars().all()
    pos: dict[str, dict] = {}
    for t in trades:
        p = pos.setdefault(t.ticker, {"qty": 0, "cost": 0.0, "opened_at": t.executed_at, "stop": None})
        if t.side == Side.buy:
            p["cost"] += t.qty * t.price + (t.fees or 0.0)
            p["qty"] += t.qty
            if t.decision_snapshot and t.decision_snapshot.get("stop"):
                p["stop"] = t.decision_snapshot["stop"]
            if p["qty"] == t.qty:
                p["opened_at"] = t.executed_at
        elif p["qty"] > 0:  # sell
            avg = p["cost"] / p["qty"]
            sold = min(t.qty, p["qty"])      # asla eldekinden fazla düşme (fat-finger guard)
            p["cost"] -= avg * sold
            p["qty"] -= sold                 # qty de min(sold) kadar — negatife inmez
            if p["qty"] <= 0:                # tam kapanış → sonraki alım temiz başlasın
                p["qty"] = 0
                p["cost"] = 0.0
                p["stop"] = None             # kapanan pozisyonun stop'u yeni pozisyona sızmasın

    return [
        {"ticker": tk, "qty": p["qty"], "avg_cost": p["cost"] / p["qty"], "stop": p["stop"],
         "opened_at": p["opened_at"]}
        for tk, p in pos.items() if p["qty"] > 0
    ]


def reconcile_positions(session: Session) -> list[dict]:
    """_compute_positions sonucunu positions tablosuna yazar (DELETE+INSERT+commit)."""
    out = _compute_positions(session)
    now = datetime.now(timezone.utc)
    session.query(Position).delete()
    for p in out:
        session.add(Position(ticker=p["ticker"], qty=p["qty"], avg_cost=p["avg_cost"],
                             stop=p["stop"], opened_at=p["opened_at"], last_reconciled_at=now))
    session.commit()
    return out


def _last_close(session: Session, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    sub = (
        select(DailyBar.ticker, func.max(DailyBar.date).label("d"))
        .where(DailyBar.ticker.in_(tickers)).group_by(DailyBar.ticker).subquery()
    )
    rows = session.execute(
        select(DailyBar.ticker, DailyBar.close)
        .join(sub, (DailyBar.ticker == sub.c.ticker) & (DailyBar.date == sub.c.d))
    ).all()
    return {t: float(c) for t, c in rows}


def portfolio_snapshot(session: Session, reconcile: bool = True) -> dict:
    acct = get_config(session, "account") or _DEF_ACCOUNT
    starting = float(acct.get("starting_cash_try", 10_000))
    annual_cpi = float(acct.get("annual_cpi", 0.35))
    try:
        start = date.fromisoformat(acct.get("start_date", _DEF_ACCOUNT["start_date"]))
    except ValueError:
        start = date.fromisoformat(_DEF_ACCOUNT["start_date"])

    trades = session.execute(select(Trade)).scalars().all()
    cash = starting
    for t in trades:
        flow = t.qty * t.price
        cash += (-flow if t.side == Side.buy else flow) - (t.fees or 0.0)

    # Salt-okunur GET'lerde (ticker detay / sizing önizleme) reconcile=False → DB'ye yazma yok.
    positions = reconcile_positions(session) if reconcile else _compute_positions(session)
    held = [p["ticker"] for p in positions]
    prices = _last_close(session, held)
    # canlı (15dk) fiyatla güncelle; erişilemezse daily close kalır
    from app.data.quotes import current_prices
    prices = {**prices, **current_prices(held)}
    # pozisyonların güncel skoru (en son swing skoru)
    scores: dict[str, tuple] = {}
    if held:
        for tk, sc, sig in session.execute(
            select(Score.ticker, Score.score, Score.signal)
            .where(Score.ticker.in_(held), Score.horizon == Horizon.swing)
            .order_by(Score.as_of.desc())
        ).all():
            if tk not in scores:
                scores[tk] = (sc, sig.value if sig else None)

    invested = 0.0
    open_risk = 0.0
    pos_rows = []
    for p in positions:
        last = prices.get(p["ticker"], p["avg_cost"])
        mv = p["qty"] * last
        invested += mv
        if p.get("stop"):
            open_risk += max(0.0, p["qty"] * (last - p["stop"]))
        sc, sig = scores.get(p["ticker"], (None, None))
        pos_rows.append({
            "ticker": p["ticker"], "qty": p["qty"], "avg_cost": round(p["avg_cost"], 4),
            "last": round(last, 4), "stop": p.get("stop"),
            "pnl_try": round(mv - p["qty"] * p["avg_cost"], 2),
            "pnl_pct": round((last / p["avg_cost"] - 1) * 100, 2) if p["avg_cost"] else 0.0,
            "score": round(sc, 1) if sc is not None else None, "signal": sig,
        })

    total_try = cash + invested
    fx = get_usdtry()
    usdtry = fx["rate"]
    years = (date.today() - start).days / 365.25
    deflator = cpi_deflator(annual_cpi, years)

    snap = {
        "cash_try": round(cash, 2),
        "invested_try": round(invested, 2),
        "total_try": round(total_try, 2),
        "total_usd": round(total_try / usdtry, 2) if usdtry else None,
        "total_real_try": round(total_try / deflator, 2),  # bugünkü TL'nin başlangıç alım gücü
        "usdtry": round(usdtry, 4),
        "usdtry_stale": fx["stale"],
        "open_heat_pct": round(open_risk / total_try, 4) if total_try else 0.0,
        "cash_pct": round(cash / total_try, 4) if total_try else 1.0,
        "pnl_total_try": round(total_try - starting, 2),
        "pnl_total_pct": round((total_try / starting - 1) * 100, 2) if starting else 0.0,
        "positions": pos_rows,
        # GÜN damgası (denetim düzeltmesi): now() her yanıtı benzersiz yapıp react-query
        # structural sharing'i kırıyordu → veri değişmese de 30sn'de bir TAM dashboard
        # re-render. Gün içinde sabit; fiyat değişimi zaten kendi alanlarını değiştirir.
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    return snap
