"""AI Brain — portföy-farkında masa analisti.

Sistemin KENDİ ölçülmüş sinyallerini (skor/sinyal/setup/rejim) kullanıcının defterine göre
sentezler: her pozisyonu kısa-orta vadede değerlendirir (koru/azalt/cik) + nakitle sistemin
KENDİ adaylarından ne alınabileceğini yorumlar.

DÜRÜSTLÜK (projenin çizgisi):
- AI kâhin değil; yalnız VERİLEN sistem sinyallerinden konuşur — yeni isim/sayı/haber UYDURMAZ.
- Sayı/stop/adet deterministik motordan gelir (AI'dan değil); AI yön + gerekçe üretir.
- `build_facts` AI'sız da tam çalışır (deterministik duruş) → "boş panel" olmaz; AI bonus katman.
- Yatırım tavsiyesi değildir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.db.models import Horizon, Score, Security
from app.engine.event_digest import _active_setups, _latest_bar_date

log = logging.getLogger(__name__)

_CANDIDATE_LIMIT = 8


def _stance_from_signal(signal: str | None, score: float | None) -> str:
    """Sistemin sinyalinden deterministik duruş (AI olmadan da her pozisyonun bir duruşu olsun)."""
    if signal == "sell":
        return "cik"
    if signal == "reduce":
        return "azalt"
    if signal in ("strong_buy", "buy"):
        return "koru"
    return "koru" if (score or 0) >= 55 else "izle"  # hold → skora göre


def _setup_labels(session: Session, as_of) -> dict[str, str]:
    """ticker → en güçlü aktif setup adı (held + aday zenginleştirme)."""
    out: dict[str, str] = {}
    for t, sigs in _active_setups(session, as_of).items():
        if sigs:
            out[t] = max(sigs, key=lambda x: x["strength"])["setup"]
    return out


def _top_candidates(session: Session, exclude: set[str], limit: int = _CANDIDATE_LIMIT) -> list[dict]:
    """Nakitle alınabilecek adaylar: en güncel, mutlak eşiği geçen yüksek skorlar (elde olmayanlar)."""
    latest = session.execute(
        select(func.max(Score.as_of)).where(Score.horizon == Horizon.swing)
    ).scalar()
    if latest is None:
        return []
    rows = session.execute(
        select(Score.ticker, Score.score, Score.signal, Security.sector)
        .join(Security, Security.ticker == Score.ticker, isouter=True)
        .where(Score.horizon == Horizon.swing, Score.as_of == latest,
               Score.meets_absolute_threshold.is_(True))
        .order_by(Score.score.desc())
    ).all()
    out: list[dict] = []
    for tk, sc, sig, sector in rows:
        if tk in exclude:
            continue
        out.append({"ticker": tk, "score": round(float(sc), 1),
                    "signal": sig.value if hasattr(sig, "value") else sig, "sector": sector})
        if len(out) >= limit:
            break
    return out


def build_facts(session: Session) -> dict:
    """Deterministik "defter durumu": pozisyonlar (sistem duruşuyla) + nakit + adaylar + rejim. AI'sız."""
    from app.risk.portfolio import portfolio_snapshot

    as_of = _latest_bar_date(session)
    snap = portfolio_snapshot(session, reconcile=False)
    positions = snap.get("positions", [])
    held = {p["ticker"] for p in positions}
    setups = _setup_labels(session, as_of) if as_of else {}

    holdings = [{
        "ticker": p["ticker"], "qty": p["qty"], "pnl_pct": p["pnl_pct"],
        "score": p["score"], "signal": p["signal"], "setup": setups.get(p["ticker"]),
        "stance": _stance_from_signal(p["signal"], p["score"]),
    } for p in positions]
    for c in (candidates := _top_candidates(session, held)):
        c["setup"] = setups.get(c["ticker"])

    macro = (get_config(session, "market_context") or {}).get("macro") or {}
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "cash_try": snap.get("cash_try"), "cash_pct": snap.get("cash_pct"),
        "open_heat_pct": snap.get("open_heat_pct"), "total_try": snap.get("total_try"),
        "pnl_total_pct": snap.get("pnl_total_pct"),
        "regime": {"regime": macro.get("regime"), "regime_score": macro.get("regime_score"),
                   "breadth_ema50": macro.get("breadth_ema50")},
        "holdings": holdings, "candidates": candidates,
    }


_SYSTEM = (
    "Sen bir BIST masa analistisin. SANA VERİLEN sistem sinyallerini (skor/sinyal/setup/rejim) "
    "kullanıcının defterine göre yorumla. KURALLAR:\n"
    "- Kendi tahminini/haberini/sayını UYDURMA — yalnız verilen verilerden konuş.\n"
    "- Her pozisyon için kısa-orta vade duruş (koru/azalt/cik) + KISA gerekçe (sistem sinyaline dayalı).\n"
    "- Nakit için SADECE verilen adaylardan hangileri uygun, neden. Yeni isim İCAT ETME.\n"
    "- Hedef fiyat/stop/adet VERME (deterministik motordan gelir). Nitel konuş.\n"
    "- Rejim risk-off ise temkinli ol; nakit de pozisyondur.\n"
    "- Yatırım tavsiyesi DEĞİL; olasılık konuşan bir analistsin, kullanıcı karar verir. Türkçe, kısa."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "holdings": {"type": "array", "items": {"type": "object", "properties": {
            "ticker": {"type": "string"},
            "stance": {"type": "string", "enum": ["koru", "azalt", "cik", "izle"]},
            "note": {"type": "string"}}, "required": ["ticker", "stance", "note"]}},
        "buys": {"type": "array", "items": {"type": "object", "properties": {
            "ticker": {"type": "string"}, "note": {"type": "string"}},
            "required": ["ticker", "note"]}},
        "cash_note": {"type": "string"},
    },
    "required": ["summary", "holdings", "buys", "cash_note"],
}


def _facts_to_prompt(f: dict) -> str:
    r = f["regime"]
    L = [
        f"PIYASA REJIMI: {r.get('regime')} (skor {r.get('regime_score')}/100).",
        f"NAKIT: ₺{f.get('cash_try') or 0:.0f} (portföyün %{(f.get('cash_pct') or 0) * 100:.0f}'i), "
        f"açık risk (heat) %{(f.get('open_heat_pct') or 0) * 100:.1f}. Toplam K/Z %{f.get('pnl_total_pct')}.",
        "\nELDEKI POZISYONLAR (sistem sinyaliyle):",
    ]
    if f["holdings"]:
        for h in f["holdings"]:
            L.append(f"- {h['ticker']}: K/Z %{h['pnl_pct']}, skor {h['score']}, sinyal {h['signal']}, "
                     f"sistem-duruş {h['stance']}" + (f", aktif setup {h['setup']}" if h.get("setup") else ""))
    else:
        L.append("- (açık pozisyon yok — portföy tümüyle nakit)")
    L.append("\nNAKITLE ALINABILECEK ADAYLAR (sistemin eşik-geçen yüksek skorları — YALNIZ bunlardan seç):")
    if f["candidates"]:
        for c in f["candidates"]:
            L.append(f"- {c['ticker']}: skor {c['score']}, sinyal {c['signal']}"
                     f"{', ' + c['sector'] if c.get('sector') else ''}"
                     + (f", setup {c['setup']}" if c.get("setup") else ""))
    else:
        L.append("- (mutlak eşiği geçen aday yok — sistem 'nakitte bekle' diyor)")
    L.append("\nGÖREV: (1) her pozisyonu kısa-orta vadede değerlendir (koru/azalt/cik + kısa gerekçe). "
             "(2) nakitle YALNIZ yukarıdaki adaylardan hangileri şu an uygun, neden (rejim + heat'i dikkate al). "
             "(3) summary: 1-2 cümle genel durum; cash_note: nakit duruşu.")
    return "\n".join(L)


def generate_brief(session: Session) -> dict:
    """Deterministik defter + (bütçe varsa) AI sentezi → config 'brain_brief'e sakla, döndür."""
    facts = build_facts(session)
    ai = None
    from app.llm import gemini_client
    from app.llm.budget import try_consume

    if gemini_client.available() and try_consume(session):
        try:
            ai = gemini_client.generate_json(_SYSTEM, _facts_to_prompt(facts), schema=_SCHEMA)
        except gemini_client.GeminiUnavailable as exc:
            log.warning("brain AI çağrısı başarısız: %s", exc)
            ai = None
        except Exception as exc:  # noqa: BLE001 — AI parse vb.; deterministik defter yine döner
            log.warning("brain AI beklenmedik hata: %s", exc)
            ai = None

    brief = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facts": facts, "ai": ai, "ai_stale": False,
        "disclaimer": ("Sistemin ölçülmüş sinyallerinin senin defterine göre AI sentezi. "
                       "Sayı/stop/adet deterministik motordan; AI yön + gerekçe üretir. "
                       "Yatırım tavsiyesi değildir — kararı sen verirsin."),
    }
    # DAYANIKLILIK: AI çağrısı başarısızsa (kota/hata) ÖNCEKİ iyi AI yorumunu SİLME — facts'i
    # tazele ama son geçerli AI'ı koru + bayat işaretle. Başarısız 'tazele' değerlendirmeyi uçurmasın.
    if ai is None:
        prev = get_config(session, "brain_brief")
        if prev and prev.get("ai"):
            brief["ai"] = prev["ai"]
            brief["ai_stale"] = True
            brief["generated_at"] = prev.get("generated_at")
    set_config(session, "brain_brief", brief)
    session.commit()  # set_config kendi commit etmez
    return brief
