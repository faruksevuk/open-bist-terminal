"""Trader-Brain narrative engine — grounded analist tezleri (Gemini Google Search + KAP).

AKIS: digest'ten konu seç (makro tema + KAP'lı hisse) → her biri icin GROUNDED cagri
(gercek guncel olay + kaynak) → prozayi + meta'yi (yon/vade/guven) ayikla → AnalystNote sakla.
Sonra thesis_grade cagrilari notlar → durust karne.

DURUSTLUK (projenin cani):
- grounded-or-silent: AI hafizadan olay uydurmaz; yalniz Google Search'ten okudugunu yazar.
  Kaynak (citation) yoksa tez SAKLANMAZ (kaynaksiz iddia yok).
- olgu/yorum ayri; SAYI uretmez (hedef fiyat/skor deterministik koddan gelir, AI'dan degil).
- yatirim tavsiyesi degil; olasilik konusur, kahin degil.
- butce: her cagri try_consume (gunluk cap) + narrative_max_per_run tavani.

Parse/store SAF fonksiyonlar (grounded cagri disi) → kotasiz birim-test edilebilir.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.config_store import get_config
from app.db.models import AnalystNote
from app.engine.event_digest import EventBundle, _latest_bar_date, daily_event_digest

log = logging.getLogger(__name__)

_MAX_PER_RUN = 8  # config 'narrative_max_per_run' ezebilir; gunluk cap (ai_budget) ayrica siniri

# Makro tema → bilinen BIST hisse ipuclari (AI grounded arama ile teyit/duzeltir).
_THEMES: list[dict] = [
    {"key": "savunma", "label": "Savunma sanayii / jeopolitik",
     "tickers": ["ASELS", "OTKAR", "SDTTR", "ASGYO"]},
    {"key": "bankacilik", "label": "Bankacilik / faiz",
     "tickers": ["GARAN", "AKBNK", "YKBNK", "ISCTR"]},
    {"key": "altin_maden", "label": "Degerli madenler / altin",
     "tickers": ["KOZAL", "KOZAA", "GLDTR"]},
    {"key": "enerji", "label": "Enerji / petrol / dogalgaz",
     "tickers": ["TUPRS", "PETKM", "AKSEN"]},
    {"key": "makro_tcmb", "label": "TCMB / kur / enflasyon (makro)", "tickers": []},
]

_SYSTEM = (
    "Sen deneyimli bir BIST analistisin. Google Search ile YALNIZ gercek, guncel olaylari oku.\n"
    "KURALLAR (ihlal etme):\n"
    "- UYDURMA. Kaynagi olmayan olay/iddia YAZMA. Belirgin/dogrulanabilir gelisme yoksa "
    "'belirgin gelisme yok' de ve kisa kes.\n"
    "- OLGU ile YORUM'u ayir: once NE OLDU (kaynakli), sonra senin analist yorumun.\n"
    "- Somut BIST hisse/sektor adi ver; yon + vade + kisa gerekce.\n"
    "- SAYI URETME (hedef fiyat, yuzde, skor yok) — yalniz nitel yon + neden.\n"
    "- Bu YATIRIM TAVSIYESI DEGIL; kesin konusan bir kahin degil, olasilik konusan analistsin.\n"
    "- Turkce, kisa (3-6 cumle).\n"
    "- EN SONA TEK SATIR JSON ekle (baska aciklama olmadan): "
    '{"direction":"up|down|neutral|mixed","horizon_days":5,"tickers":["XXX"],"confidence":0.55}'
)


def _theme_user(theme: dict, ctx: dict) -> str:
    hint = ", ".join(theme["tickers"]) if theme["tickers"] else "(genel piyasa)"
    regime = ctx.get("regime") or "?"
    return (
        f"KONU: {theme['label']}.\n"
        f"Bu hafta bu konuyla ilgili Turkiye/dunya GERCEK guncel gelismesi var mi "
        f"(zirve, karar, veri, jeopolitik olay)? Varsa: ne oldu (kaynakli), hangi BIST "
        f"hisse/sektor nasil etkilenir, hangi yonde, hangi vadede.\n"
        f"Ipucu hisseler: {hint}. Piyasa rejimi: {regime}. "
        f"Belirgin gelisme yoksa acikca soyle."
    )


def _ticker_user(b: EventBundle, name: str | None) -> str:
    facts: list[str] = []
    for e in b.corporate[:3]:
        yon = "olumlu" if (e.get("direction") or 0) > 0 else ("olumsuz" if (e.get("direction") or 0) < 0 else "notr")
        facts.append(f"KAP {e.get('type')} ({yon}): {e.get('title') or ''}".strip())
    if b.move:
        facts.append(f"bugun {b.move['ret']:+.1%}, hacim {b.move['vol_z']:+.1f} sigma")
    if b.stance and b.stance.get("score") is not None:
        facts.append(f"ic skor {b.stance['score']:.0f}/100")
    fact_str = " | ".join(facts) or "belirgin ic sinyal yok"
    return (
        f"HISSE: {b.ticker}{f' ({name})' if name else ''}.\n"
        f"Ic verimiz: {fact_str}.\n"
        f"Bu hisse/sirketle ilgili GERCEK guncel haber/gelisme ara. Varsa kisa-vadeli tez: "
        f"ne oldu (kaynakli), neden onemli, hangi yonde, hangi vadede. Yoksa 'belirgin gelisme yok' de."
    )


def _parse_meta(text: str) -> tuple[str, dict]:
    """Prozadan SONDAKI tek-satir JSON meta'yi ayikla. (temiz_proza, meta) doner. SAF."""
    meta = {"direction": "neutral", "horizon_days": 5, "tickers": [], "confidence": None}
    clean = (text or "").strip()
    lines = clean.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i].strip().strip("`").removeprefix("json").strip()
        if ln.startswith("{") and ln.endswith("}"):
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            d = str(obj.get("direction", "neutral")).lower()
            meta["direction"] = d if d in ("up", "down", "neutral", "mixed") else "neutral"
            try:
                meta["horizon_days"] = max(1, min(60, int(obj.get("horizon_days") or 5)))
            except (TypeError, ValueError):
                pass
            tk = obj.get("tickers")
            if isinstance(tk, list):
                meta["tickers"] = [str(x).upper().strip() for x in tk if x][:6]
            c = obj.get("confidence")
            try:
                meta["confidence"] = max(0.0, min(1.0, float(c))) if c is not None else None
            except (TypeError, ValueError):
                pass
            clean = "\n".join(lines[:i]).strip()  # JSON satirini prozadan cikar
            break
    return clean, meta


def _last_close(session: Session, ticker: str) -> float | None:
    from app.data.history import load_daily
    df = load_daily(session, ticker)
    if df.empty:
        return None
    try:
        return float(df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError):
        return None


def build_note(session: Session, focus: dict, res: dict, as_of) -> AnalystNote | None:
    """Grounded yanittan AnalystNote kur (SAKLAMAZ; caller ekler). Kaynak yoksa None. SAF-ish."""
    citations = res.get("citations") or []
    prose, meta = _parse_meta(res.get("text") or "")
    # grounded-or-silent: kaynak YOKSA tez saklanmaz (uydurma/kaynaksiz iddiaya izin yok).
    if not citations:
        return None
    # etkilenen hisseler: AI'in verdigi + (ticker scope'ta) hissenin kendisi
    tickers = list(meta["tickers"])
    if focus["scope_type"] == "ticker" and focus["scope"] not in tickers:
        tickers.insert(0, focus["scope"])
    primary = tickers[0] if tickers else (focus["scope"] if focus["scope_type"] == "ticker" else None)
    entry = _last_close(session, primary) if primary else None
    return AnalystNote(
        as_of=as_of,
        scope_type=focus["scope_type"],
        scope=focus["scope"],
        tickers=tickers or None,
        direction=meta["direction"],
        horizon_days=meta["horizon_days"],
        confidence=meta["confidence"],
        text=prose or (res.get("text") or "").strip(),
        citations=citations,
        queries=res.get("queries") or None,
        primary_ticker=primary,
        entry_close=entry,
        status="pending" if (primary and entry) else "no_data",
    )


def _focuses(session: Session, digest: list[EventBundle], ctx: dict, max_calls: int) -> list[dict]:
    """Arastirilacak konular: once makro temalar, sonra KAP'li/hareketli top hisseler."""
    foc: list[dict] = [
        {"scope_type": "macro", "scope": t["key"], "label": t["label"],
         "system": _SYSTEM, "user": _theme_user(t, ctx)}
        for t in _THEMES
    ]
    # ticker odaklari: KAP olayi olan ya da belirgin hareketli isimler (grounded arama anlamli)
    from app.db.models import Security
    picked = [b for b in digest if b.corporate or (b.move and abs(b.move["ret"]) >= 0.04)]
    for b in picked[: max(0, max_calls)]:
        name = session.get(Security, b.ticker)
        foc.append({
            "scope_type": "ticker", "scope": b.ticker,
            "label": b.ticker, "system": _SYSTEM,
            "user": _ticker_user(b, name.name if name else None),
        })
    return foc


def generate_theses(session: Session, as_of=None, max_calls: int | None = None) -> dict:
    """Digest'ten grounded analist tezleri uret + sakla. Butce-gate'li. Ozet dict doner.

    Gemini saglayici + anahtar sart (grounding Gemini'ye ozgu). Kota/anahtar yoksa zarifce
    atlar (sistem devam). Her cagri gunluk ai_budget'ten yer; max_calls run-basi tavan.
    """
    from app.llm import gemini_client
    from app.llm.budget import try_consume

    out: dict = {"generated": 0, "skipped_budget": 0, "no_source": 0, "errors": 0}
    if not gemini_client.available() or gemini_client.active_provider().get("provider") != "gemini":
        out["note"] = "grounding icin Gemini saglayici + anahtar gerekli (Ayarlar)"
        return out

    if max_calls is None:
        max_calls = int((get_config(session, "narrative_max_per_run") or {}).get("value", _MAX_PER_RUN))
    as_of = as_of or _latest_bar_date(session)
    if as_of is None:
        out["note"] = "bar yok"
        return out
    ctx = (get_config(session, "market_context") or {}).get("macro") or {}
    digest = daily_event_digest(session, as_of=as_of, top_n=max_calls * 2)
    focuses = _focuses(session, digest, ctx, max_calls=max_calls)

    for foc in focuses:
        if out["generated"] >= max_calls:
            break
        if not try_consume(session):  # gunluk ai_budget tavani (KAP ile paylasimli)
            out["skipped_budget"] += 1
            break
        try:
            res = gemini_client.grounded_generate(foc["system"], foc["user"])
        except gemini_client.GeminiUnavailable as exc:
            log.warning("narrative grounded cagri basarisiz (%s): %s", foc["scope"], exc)
            out["errors"] += 1
            continue
        note = build_note(session, foc, res, as_of)
        if note is None:
            out["no_source"] += 1  # AI kaynak bulamadi/uydurmadi → saklamadik (dogru davranis)
            continue
        session.add(note)
        out["generated"] += 1
    session.commit()
    return out
