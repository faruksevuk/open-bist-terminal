"""Sektör & Makro bağlam API'si (v0.1).

GET /api/context      → derlenmiş makro rejim + sektör görece-gücü (config'ten, batch yazar).
GET /api/context/ai   → ON-DEMAND: Gemini derlenmiş SAYILARI 'günün durumu' diye yorumlar.
                        Haber uydurmaz — yalnız verilen hesaplanmış bağlamı yorumlar (§18.2).

Bu katman EDGE ÜRETMEZ; bağlamı görünür kılar (PRIOR tilt). Key yoksa AI available:false.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.base import get_session
from app.llm import gemini_client

router = APIRouter(prefix="/api/context", tags=["context"])

_DEF_PROMPT = (
    "Sen kullanıcının BIST karar-destek panelinin PİYASA-BAĞLAMI yorum asistanısın. Sana "
    "SİSTEM TARAFINDAN HESAPLANMIŞ makro rejim + sektör görece-güç tablosu verilecek "
    "(eş-ağırlık evrenden türetilmiş; haber DEĞİL, ham sayılar). Görevin: bu sayıları sade "
    "Türkçe 'günün durumu' olarak yorumlamak — piyasa risk-on mu risk-off mu, hangi sektörler "
    "önde/geride, ne tür setup'lara zemin uygun. HABER/OLAY UYDURMA, yeni sayı üretme, yalnız "
    "verilen bağlamı yorumla. Yatırım tavsiyesi verme; gözlem + risk sun. Kısa, net, 4-6 cümle. "
    "Nihai karar kullanıcıya aittir; bu katman edge değil, bağlamdır."
)


@router.get("")
def context(session: Session = Depends(get_session)) -> dict:
    """Derlenmiş makro rejim + sektör tablosu (batch'in yazdığı config['market_context'])."""
    ctx = get_config(session, "market_context")
    if not ctx:
        return {"available": False,
                "message": "Bağlam henüz derlenmedi — refresh.bat / run_scoring çalıştır."}
    return {"available": True, **ctx}


@router.get("/ai")
def context_ai(session: Session = Depends(get_session)) -> dict:
    """ON-DEMAND: derlenmiş makro+sektör bağlamını Gemini ile yorumla (günün durumu)."""
    ctx = get_config(session, "market_context")
    if not ctx:
        return {"available": False, "message": "Önce bağlam derlenmeli (run_scoring)."}
    if not gemini_client.available():
        return {"available": False, "context": ctx.get("macro"),
                "message": "Gemini API key yok. backend/.env içine GEMINI_API_KEY_1..4 ekle."}
    from app.llm import budget
    if not budget.try_consume(session):
        st = budget.status(session)
        return {"available": False, "context": ctx.get("macro"),
                "message": f"AI günlük kotası doldu ({st['used']}/{st['cap']}).", "budget": st}

    macro = ctx.get("macro", {})
    sectors = ctx.get("sectors", [])
    payload = {
        "as_of": ctx.get("as_of"),
        "makro": macro,
        "sektorler_ozet": [
            {"sektor": s["sector"], "skor": s["score"], "trend": s["trend"],
             "gorece_guc_20g": s.get("rel_strength_20d"), "mom_20g": s.get("mom_20d")}
            for s in sectors
        ],
    }
    system = (get_config(session, "prompts") or {}).get("market_comment") or _DEF_PROMPT
    user = (f"Piyasa bağlamı (JSON, hesaplanmış sayılar):\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\nBu bağlamı yorumla.")
    try:
        comment = gemini_client.generate(system, user)
        return {"available": True, "as_of": ctx.get("as_of"), "comment": comment}
    except gemini_client.GeminiUnavailable as e:
        return {"available": False, "message": str(e)}
