"""KAP açıklama çekme. İki yol:

- ``fetch_market_disclosures``: TÜM piyasa, doğrudan KAP HTTP (byCriteria) — ANA yol,
  ekstra bağımlılık yok (yalnız requests).
- ``fetch_recent``: watchlist-scoped, per-ticker; OPSİYONEL ``pykap`` ile (kısmi coverage:
  özel-durum/finansal/yönetişim). pykap yoksa graceful [] döner, market yolu etkilenmez.

pipeline kaynak-bağımsız — ileride daha iyi KAP kaynağı tek dosyada değişir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

try:  # pykap OPSİYONEL (extra: pip install -e ".[kap]"). Temiz kurulumda yoksa app yine
    import pykap  # import edilir; yalnız fetch_recent devre dışı kalır (market yolu çalışır).
except ImportError:  # noqa: F401
    pykap = None

log = logging.getLogger(__name__)

# Doğrudan KAP tüm-piyasa sorgusu (taze özel-durum/ÖDA akışı). pykap'ın kullandığı
# çalışan endpoint (byCriteria) — doğrulandı: son 5 günde ~597 açıklama döner.
_KAP_QUERY = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"

# KDP=Özel Durum (material), FAR=Finansal/Faaliyet, SUR=Sürdürülebilirlik, KYUR=Kurumsal Yönetim
DISCLOSURE_TYPES = ["KDP", "FAR", "SUR", "KYUR"]
KAP_URL = "https://www.kap.org.tr/tr/Bildirim/{id}"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d.%m.%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def fetch_recent(ticker: str, days: int = 30, max_items: int = 8) -> list[dict]:
    """Bir hissenin son `days` gün KAP açıklamaları (dedupe, tarihe göre yeni→eski)."""
    if pykap is None:  # opsiyonel bağımlılık yok → per-ticker yol atlanır (market yolu etkilenmez)
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen: set[str] = set()
    out: list[dict] = []
    for dt in DISCLOSURE_TYPES:
        try:
            rows = pykap.BISTCompany(ticker).get_disclosures(dt)
        except Exception as exc:  # noqa: BLE001
            log.debug("KAP %s/%s alınamadı: %s", ticker, dt, exc)
            continue
        for r in rows or []:
            did = str(r.get("disclosureId") or "")
            pub = _parse_dt(r.get("publishDate"))
            if not did or did in seen or pub is None or pub < cutoff:
                continue
            seen.add(did)
            out.append({
                "disclosure_id": did,
                "ticker": ticker.upper(),
                "title": r.get("title"),
                "summary": r.get("summary"),
                "kap_class": r.get("disclosureClass"),
                "published_at": pub,
                "url": KAP_URL.format(id=did),
            })
    out.sort(key=lambda x: x["published_at"], reverse=True)
    return out[:max_items]


def fetch_market_disclosures(days: int = 5, disclosure_class: str = "ODA",
                             timeout: int = 30) -> list[dict]:
    """TÜM piyasa taze açıklamalar (varsayılan ÖDA=özel durum) — doğrudan KAP. Hata → []."""
    today = datetime.now(timezone.utc).date()
    body = {
        "fromDate": str(today - timedelta(days=days)), "toDate": str(today),
        "disclosureClass": disclosure_class, "subjectList": [], "mkkMemberOidList": [],
        "inactiveMkkMemberOidList": [], "bdkMemberOidList": [], "fromSrc": False,
        "disclosureIndexList": [],
    }
    try:
        r = requests.post(_KAP_QUERY, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — erişilemez → pykap fallback
        log.info("Doğrudan KAP erişilemedi (%s) → pykap fallback", type(exc).__name__)
        return []

    # Aynı açıklama (disclosureIndex) birden çok hisseyi etkileyebilir → did bazında
    # GRUPLA, tickers'ı tek listede topla. Eskiden her ticker için aynı url'li ayrı satır
    # üretiliyordu; url-bazlı dedupe ilk ticker dışındakileri sessizce düşürüyordu.
    by_did: dict[str, dict] = {}
    for r in data if isinstance(data, list) else []:
        codes = r.get("stockCodes") or r.get("relatedStocks") or ""
        tickers = [c.strip().upper() for c in str(codes).replace(";", ",").split(",") if c.strip()]
        pub = _parse_dt(r.get("publishDate"))
        did = str(r.get("disclosureIndex") or "")
        title = r.get("subject") or r.get("kapTitle")  # KAP'ta başlık 'subject' alanında
        if not tickers or pub is None or not did:
            continue
        if did in by_did:
            by_did[did]["tickers"].extend(tickers)
            continue
        by_did[did] = {
            "disclosure_id": did, "tickers": tickers,
            "title": title, "summary": r.get("summary") or title,
            "kap_class": r.get("disclosureClass") or disclosure_class,
            "published_at": pub, "url": KAP_URL.format(id=did),
        }
    out: list[dict] = []
    for d in by_did.values():
        d["tickers"] = sorted(set(d["tickers"]))
        d["ticker"] = ", ".join(d["tickers"])  # interpret() bağlamı için temsili etiket
        out.append(d)
    return out

