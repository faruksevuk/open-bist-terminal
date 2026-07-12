"""Skor/fırsat API'si — frontend fırsat tablosu (§14.2) bunu okur. Sektör-cap dahil."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.base import get_session
from app.db.models import DailyBar, Horizon, Score, Security

router = APIRouter(prefix="/api", tags=["scores"])


def _latest_as_of(session: Session, horizon: Horizon):
    return session.execute(
        select(Score.as_of).where(Score.horizon == horizon).order_by(Score.as_of.desc()).limit(1)
    ).scalar()


def _serialize(s: Score, sector: str | None) -> dict:
    return {
        "ticker": s.ticker,
        "sector": sector,
        "score": s.score,
        "signal": s.signal.value if s.signal else None,
        "passed_gates": s.passed_gates,
        "meets_absolute_threshold": s.meets_absolute_threshold,
        "sub_quality": s.sub_quality,
        "sub_oversold": s.sub_oversold,
        "sub_cause": s.sub_cause,
        "sub_stab": s.sub_stab,
        "risk_governor": s.risk_governor,
        "news_pos": s.news_pos,
        "news_neg": s.news_neg,
        "reasoning": s.reasoning,
    }


def _rows(session: Session, horizon: Horizon, only_opportunities: bool) -> list[dict]:
    as_of = _latest_as_of(session, horizon)
    if as_of is None:
        return []
    stmt = (
        select(Score, Security.sector)
        .join(Security, Security.ticker == Score.ticker)
        .where(Score.horizon == horizon, Score.as_of == as_of)
    )
    if only_opportunities:
        stmt = stmt.where(Score.meets_absolute_threshold.is_(True))
    rows = session.execute(stmt.order_by(Score.score.desc())).all()
    return [_serialize(s, sector) for s, sector in rows]


@router.get("/scores")
def scores(session: Session = Depends(get_session)) -> dict:
    as_of = _latest_as_of(session, Horizon.swing)
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "scores": _rows(session, Horizon.swing, only_opportunities=False),
    }


class SparkReq(BaseModel):
    tickers: list[str]


@router.post("/sparklines")
def sparklines(body: SparkReq, session: Session = Depends(get_session)) -> dict:
    """1-yıllık mini grafik verisi (downsample adj_close + 1y değişim). Kartlar için, hafif."""
    out: dict[str, dict] = {}
    for t in body.tickers[:80]:
        rows = session.execute(
            select(DailyBar.adj_close).where(DailyBar.ticker == t.upper())
            .order_by(DailyBar.date.desc()).limit(252)
        ).scalars().all()
        closes = [float(x) for x in reversed(rows) if x is not None]
        if len(closes) < 5:
            continue
        step = max(1, len(closes) // 52)
        pts = [round(v, 4) for v in closes[::step]]
        # change_1y yalnızca ~1 yıllık geçmiş varsa anlamlı; yeni/kısa-geçmiş hissede
        # closes[0] gerçek 1y öncesi DEĞİL → "1 yıl" etiketiyle yanlış oran döndürme.
        change_1y = round(closes[-1] / closes[0] - 1, 4) if (len(closes) >= 240 and closes[0]) else None
        out[t.upper()] = {"points": pts, "change_1y": change_1y, "last": closes[-1]}
    return {"sparklines": out}


@router.get("/opportunities")
def opportunities(session: Session = Depends(get_session)) -> dict:
    """Eşik geçenler, SEKTÖR-CAP uygulanmış (çeşitlilik; finansal kümelenmeyi kırar)."""
    as_of = _latest_as_of(session, Horizon.swing)
    full = _rows(session, Horizon.swing, only_opportunities=True)
    cap = int((get_config(session, "thresholds") or {}).get("sector_cap", 3))

    seen: dict[str, int] = {}
    capped: list[dict] = []
    dropped: list[dict] = []
    for r in full:  # skor azalan sıralı
        sec = r.get("sector") or "Diğer"
        if seen.get(sec, 0) < cap:
            capped.append(r)
            seen[sec] = seen.get(sec, 0) + 1
        else:
            dropped.append(r)

    return {
        "as_of": as_of.isoformat() if as_of else None,
        "count": len(capped),
        "total_before_cap": len(full),
        "sector_cap": cap,
        "opportunities": capped,
        "capped_out": [{"ticker": d["ticker"], "sector": d.get("sector"), "score": d["score"]} for d in dropped],
    }
