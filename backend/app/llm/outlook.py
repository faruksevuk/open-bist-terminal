"""Serbest AI görüşü ("broker görüşü") — grounding'li, portföy-farkında, senaryo kuran anlatı.

Kullanıcının isteği (2026-07-17): brain'in şablonlu skor-yorumundan FAZLASI — "yaklaşan şu
etkinlikler var (kendi araştırmasıyla), ben olsam şunu yapardım" diyen, broker masası gibi
konuşan bir katman. Brain deterministik defteri sentezler; outlook ise Google Search
grounding ile DIŞ DÜNYAYA bakar (TCMB/PPK, Fed, bilanço sezonu, temettü, jeopolitik...)
ve serbest bir anlatı kurar.

DÜRÜSTLÜK ÇİZGİSİ (gevşetilen şey ÜSLUP, ilke değil):
- Yaklaşan olay İDDİASI ancak arama kaynağıyla yazılır (grounded-or-silent burada da geçerli;
  kaynak listesi UI'da gösterilir). Grounding çalışmazsa açıkça "kaynaksız mod" etiketlenir
  ve dış-dünya iddiası yasaklanır — yalnız verilen iç veriden senaryo kurulur.
- "Ben olsam" bölümü GÖRÜŞTÜR, emir/tavsiye değil; sayı (hedef fiyat/adet/stop) üretmez —
  onlar deterministik motordan gelir. Yatırım tavsiyesi değildir.
- Bütçe: try_consume (tam havuz — akşam/kullanıcı-tetikli çağrı, rezerv kısıtı yok).
- Dayanıklılık: üretim başarısızsa önceki görüş korunur + stale bayrağı (brain deseni).

Grounding JSON moduyla birlikte kullanılamaz → çıktı serbest metin; UI markdown-vari
başlıkları ("## ...") olduğu gibi render eder. Parse yok = kırılganlık yok.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.engine.event_digest import daily_event_digest
from app.llm.brain import build_facts

log = logging.getLogger(__name__)

_SYSTEM_GROUNDED = (
    "Sen tecrübeli bir BIST broker'ı / masa stratejistisin. Google Search ile GERÇEK ve GÜNCEL "
    "bilgiyi ara: yaklaşan makro olaylar (TCMB/PPK faiz kararı, enflasyon verisi, Fed/ECB), "
    "bilanço takvimi, temettü/bedelli duyuruları, jeopolitik gelişmeler, sektör haberleri.\n"
    "KURALLAR:\n"
    "- Dış-dünya iddiası (olay, tarih, haber) YALNIZ aramada bulduğunla yazılır; bulamadıysan "
    "o konuda sus. Tarihleri belirsizse 'yaklaşık/beklenen' de.\n"
    "- OLGU ile YORUM ayrılır: önce ne olacak/oldu (kaynaklı), sonra senin stratejist görüşün.\n"
    "- SAYI ÜRETME: hedef fiyat, adet, stop, yüzde tahmini YOK — yön/senaryo/mekanizma anlat. "
    "Kullanıcının hedef bandı ve stop'u deterministik motordan geliyor.\n"
    "- 'Ben olsam' bölümünde net ol: hangi pozisyonu azaltırdım/korurdum, nakitle verilen "
    "adaylardan hangisini öne alırdım, neyi BEKLERDİM ve hangi olay öncesi risk almazdım. "
    "Defter dışından hisse önerme; sektör/tema konuşabilirsin.\n"
    "- Format: TAM OLARAK şu markdown başlıklarını kullan:\n"
    "## Yaklaşan olaylar\n## Piyasa senaryosu\n## Ben olsam\n## Radarımda olurdu\n"
    "- Türkçe, canlı ama abartısız bir masa-trader üslubu. En fazla ~350 kelime.\n"
    "- Bu YATIRIM TAVSİYESİ DEĞİL; olasılık konuşan bir stratejistsin, karar kullanıcının."
)

_SYSTEM_FALLBACK = (
    "Sen tecrübeli bir BIST masa stratejistisin. Web aramasına ERİŞİMİN YOK — bu yüzden "
    "DIŞ DÜNYA İDDİASI (yaklaşan olay, haber, tarih) YAZMA. Yalnız aşağıda verilen iç veriden "
    "(rejim, defter, adaylar, KAP olayları, hareketler) senaryo kur.\n"
    "- SAYI ÜRETME (hedef fiyat/adet/stop yok); yön/mekanizma anlat.\n"
    "- Format: TAM OLARAK şu markdown başlıklarını kullan:\n"
    "## Piyasa senaryosu\n## Ben olsam\n## Radarımda olurdu\n"
    "- Türkçe, en fazla ~250 kelime. Yatırım tavsiyesi değil."
)


def _facts_block(session: Session) -> str:
    """Deterministik bağlam bloğu: rejim + defter + adaylar + günün dikkat çekenleri."""
    f = build_facts(session)
    r = f.get("regime") or {}
    L = [
        f"PIYASA REJIMI: {r.get('regime')} (skor {r.get('regime_score')}/100, "
        f"EMA50-üstü genişlik {r.get('breadth_ema50')}).",
        f"NAKIT: %{(f.get('cash_pct') or 0) * 100:.0f}, heat %{(f.get('open_heat_pct') or 0) * 100:.1f}, "
        f"toplam K/Z %{f.get('pnl_total_pct')}.",
        "DEFTER:",
    ]
    for h in f.get("holdings") or []:
        extra = "".join([
            f", setup {h['setup']}" if h.get("setup") else "",
            f", KAP: {h['kap']}" if h.get("kap") else "",
            f", {h['move']}" if h.get("move") else "",
        ])
        L.append(f"- {h['ticker']}: K/Z %{h['pnl_pct']}, skor {h['score']}, sinyal {h['signal']}{extra}")
    if not (f.get("holdings") or []):
        L.append("- (pozisyon yok — tümüyle nakit)")
    L.append("ADAYLAR (eşik-geçen; 'ben olsam'da yalnız bunlar + defter konuşulur):")
    for c in f.get("candidates") or []:
        extra = "".join([
            f", KAP: {c['kap']}" if c.get("kap") else "",
            f", {c['move']}" if c.get("move") else "",
        ])
        L.append(f"- {c['ticker']}: skor {c['score']}, sinyal {c['signal']}"
                 f"{', ' + c['sector'] if c.get('sector') else ''}{extra}")
    if not (f.get("candidates") or []):
        L.append("- (aday yok — sistem 'nakitte bekle' diyor)")
    # günün dikkat çekenleri (deterministik digest) — modelin arama sorgularına yön verir
    top = daily_event_digest(session, top_n=8)
    if top:
        L.append("BUGÜN DİKKAT ÇEKENLER (iç ölçüm): " + ", ".join(
            f"{b.ticker}({'; '.join(b.reasons[:2])})" for b in top))
    return "\n".join(L)


def _user_prompt(session: Session, grounded: bool) -> str:
    ask = (
        "GÖREV: Önce yaklaşan 1-2 haftanın BIST'i etkileyecek GERÇEK olaylarını araştır "
        "(PPK/enflasyon/Fed/bilanço sezonu/temettü/jeopolitik). Sonra yukarıdaki defter ve "
        "rejimle birleştir: senaryo kur ve 'ben olsam' görüşünü ver."
        if grounded else
        "GÖREV: Yukarıdaki iç veriden senaryo kur ve 'ben olsam' görüşünü ver "
        "(dış-dünya iddiası YOK)."
    )
    return f"{_facts_block(session)}\n\n{ask}"


def generate_outlook(session: Session) -> dict:
    """Serbest görüş üret → config 'outlook_brief'e sakla + döndür. Bütçe-gate'li.

    Önce grounding'li dener; grounding başarısızsa (kota/model desteği) AYNI bütçe
    birimiyle kaynaksız moda düşer (dış-dünya iddiası yasak, etiketli).
    """
    from app.llm import budget as _budget
    from app.llm import gemini_client

    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "text": None, "citations": [], "queries": [], "grounded": False,
        "stale": False, "ai_error": None,
        "disclaimer": ("Serbest AI görüşü — sayı/stop/adet deterministik motordan gelir; "
                       "dış-dünya iddiaları kaynaklıdır (kaynak yoksa yazılmaz). "
                       "Yatırım tavsiyesi değildir — kararı sen verirsin."),
    }

    if not gemini_client.available():
        out["ai_error"] = "AI anahtarı yok — Ayarlar > AI API Anahtarları'ndan ekle"
    elif gemini_client.active_provider().get("provider") != "gemini":
        # grounding Gemini'ye özgü; OpenAI-uyumlu sağlayıcıda kaynaksız mod kullanılır
        if _budget.try_consume(session):
            try:
                out["text"] = gemini_client.generate(_SYSTEM_FALLBACK, _user_prompt(session, False))
            except gemini_client.GeminiUnavailable as exc:
                out["ai_error"] = f"AI çağrısı başarısız: {exc}"
        else:
            st = _budget.status(session)
            out["ai_error"] = f"günlük AI kotası doldu ({st['used']}/{st['cap']})"
    elif not _budget.try_consume(session):
        st = _budget.status(session)
        out["ai_error"] = f"günlük AI kotası doldu ({st['used']}/{st['cap']}) — yarın sıfırlanır"
    else:
        try:
            res = gemini_client.grounded_generate(_SYSTEM_GROUNDED, _user_prompt(session, True))
            out["text"] = res.get("text")
            out["citations"] = res.get("citations") or []
            out["queries"] = res.get("queries") or []
            out["grounded"] = bool(out["citations"])
            if not out["grounded"]:
                # arama döndü ama kaynak yok → dış-dünya iddialarına güvenilmez; etiketle
                out["ai_error"] = ("arama kaynağı dönmedi — metindeki dış-dünya iddialarına "
                                   "temkinli yaklaş (kaynaksız mod)")
        except gemini_client.GeminiUnavailable as exc:
            log.warning("outlook grounded başarısız, kaynaksız moda düşülüyor: %s", exc)
            try:
                out["text"] = gemini_client.generate(_SYSTEM_FALLBACK, _user_prompt(session, False))
                out["ai_error"] = "grounding kullanılamadı — kaynaksız mod (dış-dünya iddiası yok)"
            except gemini_client.GeminiUnavailable as exc2:
                out["ai_error"] = f"AI çağrısı başarısız: {exc2}"

    # DAYANIKLILIK (brain deseni): üretim yoksa önceki iyi görüşü koru + bayat işaretle
    if not out["text"]:
        prev = get_config(session, "outlook_brief")
        if prev and prev.get("text"):
            keep_err = out["ai_error"]
            out = {**prev, "stale": True, "ai_error": keep_err}
    set_config(session, "outlook_brief", out)
    session.commit()
    return out
