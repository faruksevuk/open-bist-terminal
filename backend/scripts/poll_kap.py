"""KAP açıklamalarını çek + Gemini ile yorumla + sakla (watchlist-scoped, M7).

Watchlist = eşik-geçen skorlar + açık pozisyonlar (Gemini free-tier'ı korumak için).
Sonra run_scoring haber faktörünü otomatik uygular.
Kullanım: python scripts/poll_kap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.pipeline import poll_news  # noqa: E402


def main() -> None:
    with SessionLocal() as session:
        res = poll_news(session)
    if res.get("skipped"):
        print(f"[uyarı] {res['skipped']}")
        return
    print(f"[done] watchlist={res.get('watchlist')} | kaynak={res.get('source')} | "
          f"piyasa-açıklama={res.get('market_items')} | {res.get('stored')} olay saklandı.")
    if res.get("source") == "pykap-fallback":
        print("[not] Doğrudan KAP erişilemedi (bu makineden). pykap kısmi coverage verir.")
    print("Şimdi: python scripts/run_scoring.py (haber faktörü uygulanır)")


if __name__ == "__main__":
    main()
