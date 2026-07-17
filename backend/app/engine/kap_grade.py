"""KAP yorum karnesi — AI'ın direction çağrılarını gerçekleşen getiriyle notla.

Skoru gerçekten oynatabilen tek AI-türevi sayı (direction×magnitude×confidence×20)
şimdiye dek notsuzdu. Bu modül thesis_grade desenini KAP olaylarına uygular:

- Yönlü olaylar (|direction| ≥ 0.2) yayın günü adjusted kapanışından +N işlem barı
  sonrasının adjusted kapanışıyla karşılaştırılır (temettü/bedelli-güvenli).
- Notlama thesis_grade.grade ile AYNI (±%1 nötr band; hit/miss/neutral).
- Vade dolmadıysa 'pending' kalır; bar/isim yoksa 'no_data'.
- kap_scorecard: genel + tip bazında isabet (haber faktörünün dürüst sicili).

Çok-hisseli açıklamada tickers[0] notlanır (thesis primary_ticker konvansiyonu).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from app.db.upsert import upsert
from sqlalchemy.orm import Session

from app.db.models import DailyBar, KapEvent, KapOutcome
from app.engine.thesis_grade import grade

log = logging.getLogger(__name__)

_MIN_DIRECTION = 0.2   # |direction| altı "yönsüz" sayılır — karneye girmez (nötr gürültü)
_HORIZON = 5           # işlem barı (thesis kısa-vade vadesiyle aynı)


def _adj_pair(session: Session, ticker: str, as_of: date,
              horizon_days: int) -> tuple[float | None, float | None]:
    """(giriş_adj, vade_adj) — ikisi de bugünkü adjusted seriden (thesis_grade ile aynı)."""
    entry = session.execute(
        select(DailyBar.adj_close, DailyBar.close)
        .where(DailyBar.ticker == ticker, DailyBar.date <= as_of)
        .order_by(DailyBar.date.desc()).limit(1)
    ).first()
    fwd = session.execute(
        select(DailyBar.adj_close, DailyBar.close)
        .where(DailyBar.ticker == ticker, DailyBar.date > as_of)
        .order_by(DailyBar.date).limit(horizon_days)
    ).all()
    if entry is None:
        return None, None
    e = entry[0] if entry[0] is not None else entry[1]
    if len(fwd) < horizon_days:
        return (float(e) if e is not None else None), None  # vade dolmadı
    f = fwd[-1][0] if fwd[-1][0] is not None else fwd[-1][1]
    return (float(e) if e is not None else None), (float(f) if f is not None else None)


def evaluate_kap_outcomes(session: Session, horizon_days: int = _HORIZON) -> dict:
    """Final olmayan yönlü KAP olaylarını notla → KapOutcome upsert. Özet döner."""
    existing = {
        eid: status
        for eid, status in session.execute(
            select(KapOutcome.event_id, KapOutcome.status)).all()
    }
    events = session.execute(
        select(KapEvent).where(KapEvent.interpreted.is_(True))
    ).scalars().all()

    graded = pending = skipped = 0
    now = datetime.now(timezone.utc)
    for e in events:
        prev = existing.get(e.id)
        if prev in ("hit", "miss", "neutral", "no_data"):
            continue  # final — dokunma
        tickers = e.tickers or []
        d = e.direction or 0.0
        if not tickers or abs(d) < _MIN_DIRECTION:
            skipped += 1  # yönsüz/isimsiz olay karneye girmez
            continue
        ticker = tickers[0].upper()
        as_of = e.published_at.date()
        entry, fwd = _adj_pair(session, ticker, as_of, horizon_days)
        if entry is None:
            status, ret = "no_data", None
        elif fwd is None:
            status, ret = "pending", None
            pending += 1
        else:
            direction = "up" if d > 0 else "down"
            status, ret = grade(direction, fwd / entry - 1.0)
            graded += 1
        stmt = upsert(KapOutcome).values(
            event_id=e.id, ticker=ticker,
            type=e.type.value if e.type else None,
            direction=d, horizon_days=horizon_days,
            entry_close=entry, outcome_ret=round(ret, 4) if ret is not None else None,
            status=status, graded_at=now if status not in ("pending",) else None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[KapOutcome.event_id],
            set_={"entry_close": stmt.excluded.entry_close,
                  "outcome_ret": stmt.excluded.outcome_ret,
                  "status": stmt.excluded.status,
                  "graded_at": stmt.excluded.graded_at},
        )
        session.execute(stmt)
    session.commit()
    return {"graded": graded, "pending": pending, "skipped_directionless": skipped}


def kap_scorecard(session: Session) -> dict:
    """KAP yorum karnesi — genel + tip bazında isabet. SALT-OKUR."""
    rows = session.execute(select(KapOutcome)).scalars().all()
    closed = [r for r in rows if r.status in ("hit", "miss", "neutral")]
    directional = [r for r in closed if r.status in ("hit", "miss")]
    hits = [r for r in directional if r.status == "hit"]

    def _bucket(subset: list[KapOutcome]) -> dict:
        d = [r for r in subset if r.status in ("hit", "miss")]
        h = [r for r in d if r.status == "hit"]
        return {"directional": len(d), "hits": len(h),
                "hit_rate": round(len(h) / len(d), 3) if d else None}

    by_type: dict[str, dict] = {}
    for t in sorted({r.type or "diger" for r in closed}):
        by_type[t] = _bucket([r for r in closed if (r.type or "diger") == t])

    return {
        "total": len(rows),
        "pending": sum(1 for r in rows if r.status == "pending"),
        "graded": len(closed),
        "directional": len(directional),
        "hits": len(hits),
        "hit_rate": round(len(hits) / len(directional), 3) if directional else None,
        "by_type": by_type,
        "note": ("KAP yorumunun (AI direction) 5 işlem-günü gerçekleşen isabeti; ±%1 nötr band, "
                 "adjusted seri. Az örnekte temkinli oku. Haber faktörü skora ±20 puana kadar "
                 "girer — bu karne o sayının dürüst sicili."),
    }
