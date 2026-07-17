"""AI ticker yorumu (MASTER §14 AI chat / §18.2). LLM yalnızca hesaplanmış bağlamı
yorumlar — yeni sayı/skor üretmez. Key yoksa available:false döner (sistem çalışır)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.db.base import get_session
from app.db.models import Fundamental, Horizon, KapEvent, Position, Score
from app.llm import budget, gemini_client

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _mask(k: str) -> str:
    """Anahtarı maskele — UI'a tam key ASLA gitmez (ilk 3 + son 4)."""
    return f"{k[:3]}••••{k[-4:]}" if len(k) > 8 else "••••"


@router.get("/keys")
def get_keys(session: Session = Depends(get_session)) -> dict:
    """Kayıtlı AI anahtarları — MASKELİ (tam anahtar asla dönmez) + aktif sağlayıcı. source: db/env/none."""
    db_keys = (get_config(session, "ai_keys") or {}).get("keys")
    active = gemini_client.active_keys()
    source = "db" if db_keys else ("env" if active else "none")
    prov = gemini_client.active_provider()
    return {"count": len(active), "keys": [_mask(k) for k in active],
            "source": source, "enabled": bool(active),
            "provider": prov["provider"], "base_url": prov["base_url"], "model": prov["model"],
            "providers": ["gemini", "openai"]}


class KeysBody(BaseModel):
    keys: list[str]  # 4 slot; boş slot = mevcut korunur (max 4 saklanır)
    provider: str | None = None      # "gemini" | "openai" (OpenAI-uyumlu)
    base_url: str | None = None      # OpenAI-uyumlu: OpenAI/DeepSeek/Groq/yerel base URL
    model: str | None = None         # OpenAI-uyumlu: model adı (ör. gpt-4o-mini, deepseek-chat)


@router.put("/keys")
def put_keys(body: KeysBody, session: Session = Depends(get_session)) -> dict:
    """Gemini key'lerini kaydet (DB config ai_keys; .env'i runtime ezer). Boş slot = koru.

    Anahtarlar bist.db'de (git-ignore'lu) saklanır, loglanmaz, UI'a maskeli döner.
    """
    prev_prov = gemini_client.active_provider()["provider"]
    new_prov = (body.provider or prev_prov)
    # Sağlayıcı DEĞİŞİYORSA eski anahtarları taşıma (Gemini anahtarı OpenAI'da geçmez) → sıfırdan gir.
    active = [] if new_prov != prev_prov else gemini_client.active_keys()
    merged: list[str] = []
    for i in range(max(len(body.keys), len(active))):
        provided = body.keys[i].strip() if i < len(body.keys) and body.keys[i] else ""
        if provided and "•" not in provided:      # maskeli placeholder'ı anahtar sanma
            merged.append(provided)
        elif i < len(active):
            merged.append(active[i])               # boş/maskeli → mevcut korunur
    merged = [k for k in merged if k][:4]          # anahtar zinciri: max 4
    set_config(session, "ai_keys", {"keys": merged})
    gemini_client.set_runtime_keys(merged)
    # Sağlayıcı yapılandırması (gönderildiyse)
    if body.provider is not None:
        prov_cfg = {"provider": new_prov, "base_url": (body.base_url or "").strip(),
                    "model": (body.model or "").strip()}
        set_config(session, "ai_provider", prov_cfg)
        gemini_client.set_provider(prov_cfg)
    p = gemini_client.active_provider()
    return {"ok": True, "count": len(merged), "keys": [_mask(k) for k in merged],
            "enabled": bool(merged),
            "provider": p["provider"], "base_url": p["base_url"], "model": p["model"]}

_DEF_PROMPT = (
    "Sen kullanıcının BIST karar-destek panelinin yorum asistanısın. Sana bir hissenin "
    "SİSTEM TARAFINDAN HESAPLANMIŞ bağlamı verilecek (skor kırılımı, faktörler, fundamentaller, "
    "pozisyon). Görevin: bu bağlamı sade Türkçe yorumlamak ve setup'ın mantığını + risklerini "
    "açıklamak. Yeni sayı/skor URETME; sadece verileni yorumla. Bilmediğini uydurma. Yatırım "
    "tavsiyesi verme; gözlem ve gerekçe sun. Kısa ve net ol. Nihai karar kullanıcıya aittir."
)


def _context(session: Session, ticker: str) -> dict:
    ticker = ticker.upper()
    score = session.execute(
        select(Score).where(Score.ticker == ticker, Score.horizon == Horizon.swing)
        .order_by(Score.as_of.desc()).limit(1)
    ).scalar()
    fund = session.execute(
        select(Fundamental).where(Fundamental.ticker == ticker)
        .order_by(Fundamental.as_of.desc()).limit(1)
    ).scalar()
    pos = session.get(Position, ticker)
    # son KAP açıklamaları (Python filtre — JSON tickers dizisi, dialect-agnostik)
    recent = session.execute(
        select(KapEvent).order_by(KapEvent.published_at.desc()).limit(400)
    ).scalars().all()
    kap = [{"title": e.title, "direction": e.direction, "mechanism": e.mechanism}
           for e in recent if ticker in (e.tickers or [])][:4]
    return {
        "ticker": ticker,
        "score": score.score if score else None,
        "signal": score.signal.value if score and score.signal else None,
        "meets_threshold": score.meets_absolute_threshold if score else None,
        "factors": (score.reasoning or {}).get("factors") if score else None,
        "gate_reasons": (score.reasoning or {}).get("gate_reasons") if score else None,
        # DEĞERLEME (kullanıcı isteği: AI şirket/değer sorgulaması) — hesaplanmış, LLM uydurmaz
        "valuation": {
            "pe": fund.pe if fund else None, "pb": fund.pb if fund else None,
            "mcap": fund.mcap if fund else None, "foreign_pct": fund.foreign_pct if fund else None,
        },
        "piotroski_f": fund.piotroski_f if fund else None,
        "pead": (fund.raw or {}).get("pead_sign") if fund else None,
        "pead_quarter": (fund.raw or {}).get("pead_quarter") if fund else None,
        "kap_recent": kap or None,
        "position": ({"qty": pos.qty, "avg_cost": pos.avg_cost} if pos else None),
    }


@router.get("/budget")
def ai_budget(session: Session = Depends(get_session)) -> dict:
    """Günlük AI çağrı bütçesi durumu (UI göstergesi)."""
    return budget.status(session)


@router.get("/ticker/{ticker}")
def ticker_comment(ticker: str, session: Session = Depends(get_session)) -> dict:
    ctx = _context(session, ticker)
    if not gemini_client.available():
        return {
            "available": False,
            "context": ctx,
            "message": "AI anahtarı yok. Ayarlar > AI API Anahtarları'ndan ekleyince AI yorumu "
                       "aktifleşir (https://aistudio.google.com/apikey, ücretsiz).",
        }
    # SERT bütçe tavanı: kota dolduysa AI çağrısı YAPILMAZ (deterministik bağlam yine döner)
    if not budget.try_consume(session):
        st = budget.status(session)
        return {"available": False, "context": ctx,
                "message": f"AI günlük kotası doldu ({st['used']}/{st['cap']}). "
                           f"Yarın sıfırlanır ya da config → ai_budget.daily_cap artır.",
                "budget": st}
    # seed/config anahtarı 'ticker_comment' (eski 'ticker_comment_full' yanlış → override hep yok sayılırdı)
    system = (get_config(session, "prompts") or {}).get("ticker_comment") or _DEF_PROMPT
    user = f"Hisse bağlamı (JSON):\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\nBu bağlamı yorumla."
    try:
        comment = gemini_client.generate(system, user)
        return {"available": True, "context": ctx, "comment": comment, "budget": budget.status(session)}
    except gemini_client.GeminiUnavailable as e:
        return {"available": False, "context": ctx, "message": str(e)}
