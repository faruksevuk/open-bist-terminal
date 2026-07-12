"""KAP olay deposu + decay'li haber faktörü (v0.2 §3.5/§8).

store_events: yeni açıklamaları (dedupe raw_url) Gemini ile yorumla → kap_events.
news_effect / news_map: aktif olayların asimetrik-cap'li, decay'li net etkisi → (news_pos, news_neg).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.models import KapEvent, KapType
from app.news.kap import fetch_market_disclosures, fetch_recent
from app.news.llm_interpret import interpret

log = logging.getLogger(__name__)

_DEF_HALFLIFE = {"finansal_tablo": 10, "onemli_sozlesme": 10, "yonetici_islem": 25, "diger": 2.5}
_DEF_NEWS = {"neg_cap": -20, "pos_cap": 12}


def _kap_type(s: str) -> KapType:
    try:
        return KapType(s)
    except ValueError:
        return KapType.diger


def _store_disclosure(session: Session, d: dict, existing: set[str]) -> bool:
    """Tek açıklamayı yorumla + sakla (dedupe raw_url). Çok-hisseli → tickers=[...] tek satır."""
    if d["url"] in existing:
        return False
    # tickers listesi (çok-hisse) yoksa tekil 'ticker'a düş (pykap fallback yolu)
    tickers = [t.upper() for t in (d.get("tickers") or ([d["ticker"]] if d.get("ticker") else [])) if t]
    if not tickers:
        return False
    # AI bütçe tavanı: key varsa VE kota yeterse yorumla; yoksa açıklama atlanır (sistem devam).
    # availability ÖNCE: key yokken bütçeyi boşa harcama (sayaç kirlenmesin).
    from app.llm import gemini_client
    from app.llm.budget import try_consume
    if not gemini_client.available() or not try_consume(session):
        return False
    ev = interpret(d)
    if ev is None:
        return False  # key yok / parse hatası → atla
    session.add(KapEvent(
        tickers=tickers,
        published_at=d["published_at"],
        type=_kap_type(ev["type"]),
        title=d.get("title"),
        raw_url=d["url"],
        interpreted=True,
        direction=ev["direction"],
        magnitude=ev["magnitude"],
        confidence=ev["confidence"],
        mechanism=ev["mechanism"],
        duration_days=ev["duration_days"],
        effective_until=d["published_at"] + timedelta(days=ev["duration_days"]),
    ))
    existing.add(d["url"])
    return True


def store_events(session: Session, ticker: str, days: int = 30) -> int:
    """Hissenin KAP açıklamalarını çek (pykap), yorumla, sakla. Sayı döndürür."""
    # GLOBAL dedupe (ticker-filtreli değil): başka hisse altında zaten kayıtlı bir
    # açıklamayı yeniden Gemini'ye yollamayı + mükerrer satırı önler (kota tasarrufu).
    existing = set(session.execute(select(KapEvent.raw_url)).scalars().all())
    stored = sum(_store_disclosure(session, d, existing) for d in fetch_recent(ticker, days=days))
    session.commit()
    return stored


def poll(session: Session, watchlist: list[str], days: int = 7) -> dict:
    """Önce doğrudan KAP (tüm-piyasa taze ÖDA), erişilemezse pykap per-ticker fallback."""
    wl = {t.upper() for t in watchlist}
    market = fetch_market_disclosures(days=days)
    existing = set(session.execute(select(KapEvent.raw_url)).scalars().all())
    stored = 0
    if market:
        for d in market:
            inter = [t for t in d.get("tickers", []) if t in wl]
            if not inter:
                continue  # watchlist kesişimi yoksa interpret() ÇAĞRILMAZ (Gemini maliyeti)
            stored += _store_disclosure(session, {**d, "tickers": inter}, existing)
        session.commit()
        return {"source": "direct-KAP", "market_items": len(market), "stored": stored}
    # fallback
    for t in watchlist:
        stored += store_events(session, t, days=max(days, 30))
    return {"source": "pykap-fallback", "market_items": 0, "stored": stored}


def _halflife(type_name: str, cfg: dict) -> float:
    return float(cfg.get(type_name, cfg.get("diger", _DEF_HALFLIFE["diger"])))


def news_map(session: Session, now: datetime | None = None) -> dict[str, tuple[float, float]]:
    """Aktif KAP olaylarından ticker → (news_pos, news_neg). Decay + asimetrik cap."""
    now = now or datetime.now(timezone.utc)
    hl_cfg = get_config(session, "decay_halflife_days") or _DEF_HALFLIFE
    news_cfg = get_config(session, "news") or _DEF_NEWS
    # İŞARET ZORLA: config'te neg_cap yanlışlıkla pozitif girilse bile negatif haber
    # skoru DÜŞÜRSÜN (yükseltmesin); pozitif tavan daima pozitif.
    pos_cap = abs(float(news_cfg.get("pos_cap", 12)))
    neg_cap = -abs(float(news_cfg.get("neg_cap", -20)))

    events = session.execute(
        select(KapEvent).where(KapEvent.interpreted.is_(True), KapEvent.effective_until > now)
    ).scalars().all()

    agg: dict[str, list[float]] = {}  # ticker -> [pos, neg]
    for e in events:
        if not e.tickers:
            continue
        age_days = max(0.0, (now - e.published_at).total_seconds() / 86400)
        hl = _halflife(e.type.value if e.type else "diger", hl_cfg)
        decay = 0.5 ** (age_days / hl) if hl > 0 else 0.0
        raw = (e.direction or 0.0) * (e.magnitude or 0.0) * (e.confidence or 0.0) * 20.0 * decay
        for t in e.tickers:
            slot = agg.setdefault(t, [0.0, 0.0])
            if raw >= 0:
                slot[0] += raw
            else:
                slot[1] += raw
    return {
        t: (min(p, pos_cap), max(n, neg_cap))
        for t, (p, n) in agg.items()
    }
