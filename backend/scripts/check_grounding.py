"""Gemini Google Search grounding CANLI calisiyor mu? (tani araci).

Kayitli Gemini anahtarinla gercek bir grounded cagri yapar; metin + kaynak linklerini yazar.
Trader-Brain'in dunya-haberi temellendirmesi buna dayanir (uydurma yok, kaynaksiz iddia yok).

    cd backend
    .venv/Scripts/python.exe scripts/check_grounding.py    # Windows
    .venv/bin/python       scripts/check_grounding.py       # macOS/Linux

Beklenen: analist metni + EN AZ birkac citation. 429 => free-tier gunluk kota dolu
(~gece yarisi Pasifik sifirlanir). Anahtar yok => Ayarlar > AI API Anahtarlari.
"""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_store import get_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.llm import gemini_client as g  # noqa: E402

_SYSTEM = (
    "Sen bir BIST analistisin. Google Search ile YALNIZ gercek, guncel olaylari kullan. "
    "Uydurma; kaynagi olmayan iddia kurma. Olgu (kaynakli) ile yorumu ayir. Yatirim tavsiyesi degil."
)
_USER = (
    "Bu hafta Turkiye ve savunma sanayii ile ilgili, BIST'i etkileyebilecek one cikan GERCEK "
    "guncel gelisme var mi? Varsa kisa: ne oldu (kaynakli), hangi sektor/hisse, hangi yon, hangi "
    "vade. Belirgin gelisme yoksa 'belirgin gelisme yok' de."
)


def main() -> int:
    with SessionLocal() as s:
        keys = (get_config(s, "ai_keys") or {}).get("keys") or []
    g.set_runtime_keys(keys)
    if not g.available():
        print("[x] Kayitli Gemini anahtari yok. Ayarlar > AI API Anahtarlari.")
        return 1
    print(f"[i] {len(g.active_keys())} anahtar aktif. Grounded cagri yapiliyor...\n")
    try:
        res = g.grounded_generate(_SYSTEM, _USER)
    except g.GeminiUnavailable as e:
        print(f"[x] GeminiUnavailable: {e}")
        print("    (429 => free-tier gunluk kota dolu; yarin tekrar dene.)")
        return 2
    print("=== ANALIST METNI ===")
    print(res["text"].strip())
    print("\n=== KAYNAKLAR ===")
    if res["citations"]:
        for c in res["citations"]:
            print(f"  - {c['title']}  |  {c['uri']}")
    else:
        print("  (citation gelmedi — metin akiyor ama kaynak yok; grounding kapali olabilir)")
    print("\nweb sorgulari :", res["queries"])
    print("suggestions   :", "var" if res["rendered_suggestions"] else "yok")
    ok = bool(res["citations"])
    print("\n[OK] grounding CANLI calisiyor." if ok else "\n[!] metin var, citation yok — kontrol et.")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
