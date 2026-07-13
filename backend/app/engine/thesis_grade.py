"""Analist tezi KARNE — AI'in yon cagrilarini gercekle notla (durust sicil).

evaluate_theses: vadesi dolan 'pending' notlari, birincil hissenin gerceklesen getirisine gore
notlar (hit/miss/neutral). thesis_scorecard: toplu isabet + ortalama getiri (macro/ticker kirilim).

DURUSTLUK: notr band (+-%1) alti hareket 'neutral' sayilir (cagri ne net dogru ne yanlis);
isabet orani yalniz YONLU (up/down) tezler uzerinden. Az ornek -> temkinli oku (UI bunu yazar).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalystNote, DailyBar

_NEUTRAL_BAND = 0.01  # |getiri| < %1 => sonuc notr (yon cagrisi ne net dogru ne yanlis)


def grade(direction: str | None, ret: float | None) -> tuple[str, float | None]:
    """(status, ret) — yonlu cagriyi gerceklesen getiriye gore notla. SAF; test edilebilir."""
    if ret is None:
        return "no_data", None
    if direction in ("neutral", "mixed", None):
        return "neutral", ret
    if abs(ret) < _NEUTRAL_BAND:
        return "neutral", ret          # anlamli hareket yok → cozulmedi
    up = ret > 0
    if direction == "up":
        return ("hit" if up else "miss"), ret
    if direction == "down":
        return ("hit" if not up else "miss"), ret
    return "neutral", ret


def _forward_close(session: Session, ticker: str, as_of: date, horizon_days: int) -> float | None:
    """as_of'tan SONRAKI horizon_days'inci islem barinin kapanisi. Vade dolmadiysa None."""
    rows = session.execute(
        select(DailyBar.close).where(DailyBar.ticker == ticker, DailyBar.date > as_of)
        .order_by(DailyBar.date).limit(horizon_days)
    ).scalars().all()
    if len(rows) < horizon_days:
        return None  # yeterli ileri-bar yok → vade henuz dolmadi
    c = rows[-1]
    return float(c) if c is not None else None


def evaluate_theses(session: Session, as_of: date | None = None) -> dict:
    """Vadesi dolan 'pending' tezleri notla. Ozet dict doner (run_scoring/scheduler cagirir)."""
    pending = session.execute(
        select(AnalystNote).where(AnalystNote.status == "pending")
    ).scalars().all()
    graded = 0
    for n in pending:
        if not n.primary_ticker or not n.entry_close or not n.horizon_days:
            n.status = "no_data"
            continue
        fc = _forward_close(session, n.primary_ticker, n.as_of, n.horizon_days)
        if fc is None:
            continue  # vade dolmadi → pending kalir
        ret = fc / n.entry_close - 1.0
        status, r = grade(n.direction, ret)
        n.status = status
        n.outcome_ret = round(ret, 4)
        n.graded_at = datetime.now(timezone.utc)
        graded += 1
    session.commit()
    left = sum(1 for n in pending if n.status == "pending")
    return {"graded": graded, "pending_left": left}


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def thesis_scorecard(session: Session) -> dict:
    """AI tez karnesi — isabet + ortalama getiri (macro/ticker kirilim). SALT-OKUR."""
    notes = session.execute(select(AnalystNote)).scalars().all()
    closed = [n for n in notes if n.status in ("hit", "miss", "neutral")]
    directional = [n for n in closed if n.status in ("hit", "miss")]
    hits = [n for n in directional if n.status == "hit"]
    n_dir = len(directional)

    def _bucket(subset: list[AnalystNote]) -> dict:
        d = [n for n in subset if n.status in ("hit", "miss")]
        h = [n for n in d if n.status == "hit"]
        return {
            "directional": len(d),
            "hits": len(h),
            "hit_rate": round(len(h) / len(d), 3) if d else None,
        }

    return {
        "total_notes": len(notes),
        "pending": sum(1 for n in notes if n.status == "pending"),
        "graded": len(closed),
        "directional": n_dir,
        "hits": len(hits),
        "hit_rate": round(len(hits) / n_dir, 3) if n_dir else None,
        "avg_ret_hit": _avg([n.outcome_ret for n in directional if n.status == "hit" and n.outcome_ret is not None]),
        "avg_ret_miss": _avg([n.outcome_ret for n in directional if n.status == "miss" and n.outcome_ret is not None]),
        "by_scope": {
            "macro": _bucket([n for n in closed if n.scope_type == "macro"]),
            "ticker": _bucket([n for n in closed if n.scope_type == "ticker"]),
        },
        "note": ("Durust karne: AI yon cagrilarinin gerceklesen isabeti (notr band +-%1). "
                 "Ornek az oldukca guvenilmez — temkinli oku. Yatirim tavsiyesi degil."),
    }
