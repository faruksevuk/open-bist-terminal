"""Sağlık kontrolü: DB erişilebilir mi. Cache in-process (Redis yok).

/api/heartbeat: veri damgaları — frontend'in TEK hafif poll'u. Damga değişince ilgili
sorgular invalidate edilir; büyük payload'lar (scores 470KB) artık körlemesine çekilmez.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.base import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    db_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 — sağlık kontrolünde sustur
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "cache": "memory",  # in-process TTL (Redis kaldırıldı)
        "version": "0.3.0",
    }


@router.get("/api/heartbeat")
def heartbeat(session: Session = Depends(get_session)) -> dict:
    """Veri damgaları (ucuz tek sorgu seti) — frontend poll'larını olaya çevirir.

    scores_as_of değişti → skor/fırsat/setup/bağlam/karne sorguları tazelenir;
    last_kap değişti → digest/haber; brain_at/outlook_at → ilgili paneller.
    """
    from app.config_store import get_config
    from app.db.models import Horizon, KapEvent, Score

    out: dict = {"ok": True}
    try:
        out["scores_as_of"] = str(session.execute(
            select(func.max(Score.as_of)).where(Score.horizon == Horizon.swing)).scalar() or "")
        out["last_kap"] = str(session.execute(
            select(func.max(KapEvent.published_at))).scalar() or "")
        out["brain_at"] = (get_config(session, "brain_brief") or {}).get("generated_at")
        out["outlook_at"] = (get_config(session, "outlook_brief") or {}).get("generated_at")
    except Exception:  # noqa: BLE001 — damga alınamazsa frontend fallback aralığıyla devam eder
        out["ok"] = False
    return out
