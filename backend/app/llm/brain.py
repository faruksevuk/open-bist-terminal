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
from app.engine.event_digest import _active_kap, _active_setups, _latest_bar_date, _moves

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


def _kap_summary(events: list[dict]) -> str | None:
    """Bir hissenin aktif KAP olaylarını tek kısa satıra indir (prompt + UI)."""
    if not events:
        return None
    parts: list[str] = []
    for e in sorted(events, key=lambda x: abs((x.get("direction") or 0) * (x.get("magnitude") or 0)),
                    reverse=True)[:2]:
        yon = "pozitif" if (e.get("direction") or 0) > 0.1 else (
            "negatif" if (e.get("direction") or 0) < -0.1 else "nötr")
        title = (e.get("title") or "").strip()
        parts.append(f"{e.get('type')} ({yon})" + (f": {title[:80]}" if title else ""))
    return " | ".join(parts) or None


def _move_summary(mv: dict | None) -> str | None:
    """Günün fiyat/hacim hareketi tek satır (belirginse)."""
    if not mv:
        return None
    if abs(mv.get("ret") or 0) < 0.02 and (mv.get("vol_z") or 0) < 1.5:
        return None
    return f"bugün {mv['ret']:+.1%}, hacim {mv['vol_z']:+.1f}σ"


def build_facts(session: Session) -> dict:
    """Deterministik "defter durumu": pozisyonlar (sistem duruşuyla) + nakit + adaylar + rejim
    + AKTİF KAP OLAYLARI + günün fiyat/hacim hareketi. AI'sız da tam çalışır.

    KAP/hareket eklendi (denetim bulgusu): brain daha önce yalnız skor/sinyal görüyordu →
    notları skorun cümleye çevrilmiş halinden öteye geçemiyordu. Artık model gerçek olay
    bağlamıyla konuşur (yine de yalnız VERİLEN veriden — uydurma yok).
    """
    from datetime import datetime, timezone as _tz

    from app.risk.portfolio import portfolio_snapshot

    as_of = _latest_bar_date(session)
    snap = portfolio_snapshot(session, reconcile=False)
    positions = snap.get("positions", [])
    held = {p["ticker"] for p in positions}
    setups = _setup_labels(session, as_of) if as_of else {}
    kap = _active_kap(session, datetime.now(_tz.utc))
    moves = _moves(session, as_of) if as_of else {}

    holdings = [{
        "ticker": p["ticker"], "qty": p["qty"], "pnl_pct": p["pnl_pct"],
        "score": p["score"], "signal": p["signal"], "setup": setups.get(p["ticker"]),
        "stance": _stance_from_signal(p["signal"], p["score"]),
        "kap": _kap_summary(kap.get(p["ticker"], [])),
        "move": _move_summary(moves.get(p["ticker"])),
    } for p in positions]
    for c in (candidates := _top_candidates(session, held)):
        c["setup"] = setups.get(c["ticker"])
        c["kap"] = _kap_summary(kap.get(c["ticker"], []))
        c["move"] = _move_summary(moves.get(c["ticker"]))

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
    "Sen tecrübeli bir BIST masa trader'ısın. SANA VERİLEN veriyi (skor/sinyal/setup/rejim + "
    "aktif KAP olayları + günün fiyat/hacim hareketi) kullanıcının defterine göre sentezle. KURALLAR:\n"
    "- Veri dışı olay/haber/sayı UYDURMA — ama verilen veriler ÜZERİNDE serbestçe düşün: "
    "KAP olayı + hareket + rejimden senaryo kur, 'skor X' demekle yetinme; skoru zaten görüyor.\n"
    "- Her pozisyon için duruş (koru/azalt/cik) + gerekçe: MEKANİZMA anlat (neden bu yön: "
    "olay mı, momentum kaybı mı, rejim mi), sayı tekrarı değil.\n"
    "- Nakit için SADECE verilen adaylardan seç; hangisi neden şimdi, hangisi bekletilir söyle. "
    "Yeni isim İCAT ETME.\n"
    "- Hedef fiyat/stop/adet VERME (deterministik motordan gelir). Nitel konuş.\n"
    "- Rejim risk-off ise temkinli ol; nakit de pozisyondur.\n"
    "- Yatırım tavsiyesi DEĞİL; olasılık konuşan bir trader'sın, kullanıcı karar verir. Türkçe, net."
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
            extra = "".join([
                f", aktif setup {h['setup']}" if h.get("setup") else "",
                f", KAP: {h['kap']}" if h.get("kap") else "",
                f", {h['move']}" if h.get("move") else "",
            ])
            L.append(f"- {h['ticker']}: K/Z %{h['pnl_pct']}, skor {h['score']}, sinyal {h['signal']}, "
                     f"sistem-duruş {h['stance']}{extra}")
    else:
        L.append("- (açık pozisyon yok — portföy tümüyle nakit)")
    L.append("\nNAKITLE ALINABILECEK ADAYLAR (sistemin eşik-geçen yüksek skorları — YALNIZ bunlardan seç):")
    if f["candidates"]:
        for c in f["candidates"]:
            extra = "".join([
                f", setup {c['setup']}" if c.get("setup") else "",
                f", KAP: {c['kap']}" if c.get("kap") else "",
                f", {c['move']}" if c.get("move") else "",
            ])
            L.append(f"- {c['ticker']}: skor {c['score']}, sinyal {c['signal']}"
                     f"{', ' + c['sector'] if c.get('sector') else ''}{extra}")
    else:
        L.append("- (mutlak eşiği geçen aday yok — sistem 'nakitte bekle' diyor)")
    L.append("\nGÖREV: (1) her pozisyonu kısa-orta vadede değerlendir (koru/azalt/cik + MEKANİZMALI gerekçe — "
             "KAP olayı/hareket/rejim varsa onlara dayan). "
             "(2) nakitle YALNIZ yukarıdaki adaylardan hangileri şu an uygun, neden (rejim + heat'i dikkate al). "
             "(3) summary: 1-2 cümle genel durum; cash_note: nakit duruşu.")
    return "\n".join(L)


def generate_brief(session: Session) -> dict:
    """Deterministik defter + (bütçe varsa) AI sentezi → config 'brain_brief'e sakla, döndür.

    AI üretilemezse NEDENİ brief'e yazılır (ai_error) — sessiz başarısızlık kullanıcıda
    "AI çalışmıyor" algısı yaratıyordu (denetim bulgusu).
    """
    facts = build_facts(session)
    ai = None
    ai_error: str | None = None
    from app.llm import budget as _budget
    from app.llm import gemini_client

    if not gemini_client.available():
        ai_error = "AI anahtarı yok — Ayarlar > AI API Anahtarları'ndan ekle"
    elif not _budget.try_consume(session):
        st = _budget.status(session)
        ai_error = f"günlük AI kotası doldu ({st['used']}/{st['cap']}) — yarın sıfırlanır"
    else:
        try:
            ai = gemini_client.generate_json(_SYSTEM, _facts_to_prompt(facts), schema=_SCHEMA)
        except gemini_client.GeminiUnavailable as exc:
            log.warning("brain AI çağrısı başarısız: %s", exc)
            ai_error = f"AI çağrısı başarısız: {exc}"
        except Exception as exc:  # noqa: BLE001 — AI parse vb.; deterministik defter yine döner
            log.warning("brain AI beklenmedik hata: %s", exc)
            ai_error = "AI yanıtı işlenemedi (geçici olabilir — tekrar dene)"

    brief = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facts": facts, "ai": ai, "ai_stale": False, "ai_error": ai_error,
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
