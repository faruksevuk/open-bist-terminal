"""Kâğıt-portföy API — otonominin sınav defteri (gerçek para/emir YOK).

GET  /api/paper          → karne: equity, haftalık %, hedef & ölçülen kapasite kıyası,
                           açık/bekleyen/kapanan sanal işlemler.
POST /api/paper/step     → adımı şimdi koştur (normalde her skorlama sonunda otomatik).
POST /api/paper/config   → {enabled?, start_cash?} — aç/kapat.
POST /api/paper/reset    → sanal defteri sıfırla (start_cash'ten yeniden başlar).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.db.base import get_session
from app.engine.paper_trader import paper_stats, paper_step

router = APIRouter(prefix="/api", tags=["paper"])


@router.get("/paper")
def paper(session: Session = Depends(get_session)) -> dict:
    out = paper_stats(session)
    # hedef vs ölçülen kapasite (kaba üst sınır) — dürüst kıyas tek çağrıda
    try:
        from app.engine.setup_outcomes import outcome_summary
        summ = outcome_summary(session)
        out["capacity"] = summ.get("capacity")
        out["expectancy"] = summ.get("expectancy")
    except Exception:  # noqa: BLE001
        session.rollback()
    return out


@router.post("/paper/step")
def step_now(session: Session = Depends(get_session)) -> dict:
    return paper_step(session)


class PaperCfg(BaseModel):
    enabled: bool | None = None
    start_cash: float | None = None


@router.post("/paper/config")
def paper_config(body: PaperCfg, session: Session = Depends(get_session)) -> dict:
    cfg = get_config(session, "auto_paper") or {}
    if body.enabled is not None:
        cfg["enabled"] = bool(body.enabled)
    if body.start_cash is not None and body.start_cash > 0:
        cfg["start_cash"] = float(body.start_cash)
    set_config(session, "auto_paper", cfg)
    session.commit()
    return {"ok": True, "auto_paper": cfg}


@router.post("/paper/reset")
def paper_reset(session: Session = Depends(get_session)) -> dict:
    """Sanal defteri sıfırla — paper_state silinir; sonraki adımda start_cash'le kurulur."""
    from sqlalchemy import text
    session.execute(text("DELETE FROM config WHERE key='paper_state'"))
    session.commit()
    return {"ok": True, "note": "kâğıt portföy sıfırlandı"}
