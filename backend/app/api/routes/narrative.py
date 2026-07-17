"""Trader-Brain API — grounded analist tezleri + karne + gunluk "dikkat cekenler" digest.

GET /api/narrative            → son analist tezleri (proza + kaynak + yon/vade/guven + durum) + karne
GET /api/narrative/scorecard  → AI yon-cagrisi karnesi (isabet, macro/ticker kirilim)
GET /api/digest               → bugun dikkat cekenler (KAP + setup + fiyat/hacim, materiality sirali)

Tezler NITELIKSEL + kaynakli; sayi (skor/boyut) buradan gelmez. Yatirim tavsiyesi degil.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.base import get_session
from app.db.models import AnalystNote
from app.engine.event_digest import EventBundle, daily_event_digest
from app.engine.thesis_grade import thesis_scorecard

router = APIRouter(prefix="/api", tags=["narrative"])


@router.get("/brain")
def brain(session: Session = Depends(get_session)) -> dict:
    """AI Brain — portföy-farkında değerlendirme. Saklı brief varsa onu; yoksa deterministik defter (AI'sız)."""
    stored = get_config(session, "brain_brief")
    if stored:
        return stored
    from app.llm.brain import build_facts
    return {
        "generated_at": None,
        "facts": build_facts(session),
        "ai": None,
        "disclaimer": ("Sistemin ölçülmüş sinyallerinin senin defterine göre değerlendirmesi. "
                       "Sayı/stop deterministik motordan; AI yön + gerekçe üretir. Yatırım tavsiyesi değildir."),
        "note": "AI sentezi henüz üretilmedi — 'AI ile tazele' de (Gemini anahtarı + kota gerekir). Deterministik defter aşağıda hazır.",
    }


@router.post("/brain/refresh")
def brain_refresh(session: Session = Depends(get_session)) -> dict:
    """AI Brain'i şimdi üret (bütçe-gate'li; kota yoksa deterministik defter döner, ai=null)."""
    from app.llm.brain import generate_brief
    return generate_brief(session)


@router.get("/outlook")
def outlook(session: Session = Depends(get_session)) -> dict:
    """Serbest AI görüşü ("broker görüşü") — saklı son üretim; yoksa dürüst boş durum."""
    stored = get_config(session, "outlook_brief")
    if stored:
        return stored
    return {
        "generated_at": None, "text": None, "citations": [], "queries": [],
        "grounded": False, "stale": False,
        "ai_error": None,
        "note": "Henüz üretilmedi — 'Şimdi üret' de ya da akşam 19:15 otonom koşumunu bekle "
                "(Gemini anahtarı + kota gerekir).",
        "disclaimer": "Serbest AI görüşü. Yatırım tavsiyesi değildir — kararı sen verirsin.",
    }


@router.post("/outlook/refresh")
def outlook_refresh(session: Session = Depends(get_session)) -> dict:
    """Serbest görüşü şimdi üret (bütçe-gate'li; grounding yoksa kaynaksız moda düşer)."""
    from app.llm.outlook import generate_outlook
    return generate_outlook(session)


def _note_dict(n: AnalystNote) -> dict:
    return {
        "id": n.id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "as_of": n.as_of.isoformat() if n.as_of else None,
        "scope_type": n.scope_type,
        "scope": n.scope,
        "tickers": n.tickers or [],
        "direction": n.direction,
        "horizon_days": n.horizon_days,
        "confidence": n.confidence,
        "text": n.text,
        "citations": n.citations or [],
        "queries": n.queries or [],
        "primary_ticker": n.primary_ticker,
        "status": n.status,
        "outcome_ret": n.outcome_ret,
        "graded_at": n.graded_at.isoformat() if n.graded_at else None,
    }


def _bundle_dict(b: EventBundle) -> dict:
    return {
        "ticker": b.ticker,
        "materiality": b.materiality,
        "reasons": b.reasons,
        "corporate": [
            {"type": e.get("type"), "direction": e.get("direction"),
             "title": e.get("title")}
            for e in (b.corporate or [])
        ],
        "technical": [
            {"setup": s.get("setup"), "strength": s.get("strength")}
            for s in (b.technical or [])
        ],
        "move": b.move,
        "stance": b.stance,
    }


@router.get("/narrative")
def narrative(limit: int = 30, session: Session = Depends(get_session)) -> dict:
    """Son analist tezleri (yeni → eski) + karne. Boşsa boş liste (grounding henüz koşmadı)."""
    rows = session.execute(
        select(AnalystNote).order_by(AnalystNote.created_at.desc()).limit(max(1, min(limit, 100)))
    ).scalars().all()
    return {
        "count": len(rows),
        "notes": [_note_dict(n) for n in rows],
        "scorecard": thesis_scorecard(session),
        "disclaimer": "Nitel, kaynaklı analist yorumu. Sayı/pozisyon AI'dan gelmez. Yatırım tavsiyesi değildir.",
    }


@router.get("/narrative/scorecard")
def scorecard(session: Session = Depends(get_session)) -> dict:
    """AI yön-çağrısı karnesi (isabet oranı; macro/ticker kırılım). SALT-OKUR."""
    return thesis_scorecard(session)


@router.get("/digest")
def digest(top: int = 30, session: Session = Depends(get_session)) -> dict:
    """Bugün dikkat çekenler — KAP + setup + fiyat/hacim, materiality sıralı (deterministik, AI'sız)."""
    bundles = daily_event_digest(session, top_n=max(1, min(top, 100)))
    return {
        "as_of": bundles[0].as_of.isoformat() if bundles else None,
        "count": len(bundles),
        "items": [_bundle_dict(b) for b in bundles],
    }
