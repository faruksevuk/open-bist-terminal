"""Faktör ağırlıkları + ölçülen kanıt — dashboard 'Ayarlar' sekmesi.

GET  /api/factors          → faktör havuzu: etiket + canlı ağırlık + ölçülen rank-IC/t/isabet.
PUT  /api/factors/weights  → ağırlıkları kaydet (config factor_weights; motor anında okur).
POST /api/factors/measure  → factor diagnostic'i koştur + sakla (uzun; on-demand tazeleme).

DÜRÜSTLÜK: ölçülen IC tek rejim (2y) — kanıt değil, işaret. quality/pead/value/cause
fiyattan ölçülemez (research prior). UI her faktörün yanında bunu gösterir.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.db.base import get_session

router = APIRouter(prefix="/api/factors", tags=["factors"])

# Havuz sırası + Türkçe etiket + tür (measured=fiyattan ölçülür / prior=research).
_FACTORS: list[tuple[str, str, str]] = [
    ("low_vol", "Düşük Volatilite (BAB)", "measured"),
    ("rev5", "Kısa-Vade Dönüş (5g)", "measured"),
    ("momentum", "Trend Gücü (bileşik)", "measured"),
    ("roc20", "20g Momentum (düz)", "measured"),
    ("pead", "Kazanç Sürprizi (PEAD)", "prior"),
    ("quality", "Kalite (F-Score)", "prior"),
    ("value", "Ucuzluk (PE/PB)", "prior"),
    ("reversal", "Aşırı-Satılmış (reversal)", "measured"),
    ("stab", "Stabilizasyon", "measured"),
    ("cause", "Sebep (ko-hareket)", "prior"),
]
_VALID = {k for k, _, _ in _FACTORS}


@router.get("")
def factors(session: Session = Depends(get_session)) -> dict:
    weights = get_config(session, "factor_weights") or {}
    diag = get_config(session, "factor_diagnostic") or {}
    measured = diag.get("measured") or {}
    priors = diag.get("priors") or {}

    rows: list[dict] = []
    for key, label, kind in _FACTORS:
        m = measured.get(key) or {}
        ic = m.get("ic") if kind == "measured" else priors.get(key)
        rows.append({
            "key": key,
            "label": label,
            "kind": kind,
            "weight": float(weights.get(key, 0.0)),
            "ic": ic,
            "t": m.get("t") if kind == "measured" else None,
            "hit": m.get("hit") if kind == "measured" else None,
            # renk-kodu: t>=2 ya da CI sıfırı hariç → güçlü; measured ama zayıf → weak
            "strong": bool(kind == "measured" and (m.get("ci_excludes_zero")
                        or (m.get("t") is not None and abs(m["t"]) >= 2.0))),
        })
    return {
        "diagnostic_as_of": diag.get("as_of"),
        "diagnostic_params": diag.get("params"),
        "weight_sum": round(sum(r["weight"] for r in rows), 4),
        "factors": rows,
        "note": diag.get("note") or ("Ölçüm henüz yok — POST /api/factors/measure "
                                     "ya da haftalık kalibrasyon çalışınca dolar."),
    }


class WeightsBody(BaseModel):
    weights: dict[str, float]


@router.put("/weights")
def put_weights(body: WeightsBody, session: Session = Depends(get_session)) -> dict:
    unknown = [k for k in body.weights if k not in _VALID]
    if unknown:
        raise HTTPException(status_code=422, detail=f"bilinmeyen faktör: {unknown}")
    if any(v < 0 for v in body.weights.values()):
        raise HTTPException(status_code=422, detail="ağırlık negatif olamaz")
    if sum(body.weights.values()) <= 0:
        raise HTTPException(status_code=422, detail="en az bir ağırlık > 0 olmalı")
    # eksik anahtarları 0 ile tamamla (havuz tam olsun; motor wsum ile normalize eder)
    full = {k: float(body.weights.get(k, 0.0)) for k in _VALID}
    set_config(session, "factor_weights", full)
    return {"ok": True, "weights": full}


@router.post("/measure")
def measure(session: Session = Depends(get_session)) -> dict:
    """Factor diagnostic'i koştur + sakla (UZUN — evren geneli rank-IC). Sonuç config'e yazılır."""
    from app.backtest.calibration import store_factor_diagnostic

    blob = store_factor_diagnostic(session)
    return {"ok": True, "as_of": blob["as_of"], "measured": blob["measured"]}
