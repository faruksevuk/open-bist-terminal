"""KAP/haber olay API'si — bir hissenin aktif yorumlanmış açıklamaları (link + yön)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import KapEvent

router = APIRouter(prefix="/api", tags=["news"])


@router.get("/kap/{ticker}")
def kap_events(ticker: str, session: Session = Depends(get_session)) -> dict:
    """Hissenin son KAP olayları (aktif olanlar önce). 'neden al/sat' gerekçesi."""
    t = ticker.upper()
    # tickers artık JSON dizisi (dialect-agnostik); son olayları çekip Python'da filtrele
    # (KAP hacmi düşük — 500 pencere yeterli, portable, ARRAY operatörüne bağımlı değil).
    recent = session.execute(
        select(KapEvent).order_by(KapEvent.published_at.desc()).limit(500)
    ).scalars().all()
    rows = [e for e in recent if t in (e.tickers or [])][:15]
    now = datetime.now(timezone.utc)
    return {
        "ticker": t,
        "events": [{
            "title": e.title,
            "type": e.type.value if e.type else None,
            "direction": e.direction,
            "magnitude": e.magnitude,
            "confidence": e.confidence,
            "mechanism": e.mechanism,
            "published_at": e.published_at.isoformat() if e.published_at else None,
            "active": bool(e.effective_until and e.effective_until > now),
            "url": e.raw_url,
        } for e in rows],
    }
