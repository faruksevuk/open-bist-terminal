"""Risk profili API'si — tek anahtarla risk iştahı + dürüst kayıp-serisi matematiği.

GET /api/risk/profile         → aktif profil + tüm profillerin matematiği (canlı isabetle).
PUT /api/risk/profile {name}  → profili uygula ('risk' config'ine merge; sizing anında okur).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.base import get_session
from app.risk.profiles import apply_profile, profile_overview

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _live_hit_rate(session: Session) -> float | None:
    """Canlı OOS'tan net isabet (n_closed>=10 ise); yoksa None → muhafazakâr varsayılan."""
    from app.engine.setup_outcomes import outcome_summary

    try:
        overall = outcome_summary(session)["overall"]
    except Exception:  # noqa: BLE001 — takip tablosu yoksa profil matematiği yine çalışsın
        return None
    if (overall.get("n_closed") or 0) < 10:
        return None
    return overall.get("isabet_net") if overall.get("isabet_net") is not None else overall.get("isabet")


@router.get("/profile")
def get_profile(session: Session = Depends(get_session)) -> dict:
    ov = profile_overview(session, live_hit_rate=_live_hit_rate(session))
    risk = get_config(session, "risk") or {}
    ov["applied_risk"] = {k: risk.get(k) for k in
                          ("base_r", "max_heat_pct", "daily_stop_pct", "weekly_dd_pct")}
    # devre kesici durumu — profil parametreleri artık vitrin değil, uygulanıyor
    from app.risk.circuit import circuit_state
    try:
        ov["circuit"] = circuit_state(session)
    except Exception:  # noqa: BLE001
        session.rollback()
        ov["circuit"] = {"active": False, "error": "hesaplanamadı"}
    return ov


class ProfileBody(BaseModel):
    profile: str


@router.put("/profile")
def put_profile(body: ProfileBody, session: Session = Depends(get_session)) -> dict:
    try:
        risk = apply_profile(session, body.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ok": True, "active": body.profile,
            "applied_risk": {k: risk.get(k) for k in
                             ("base_r", "max_heat_pct", "daily_stop_pct", "weekly_dd_pct")}}
