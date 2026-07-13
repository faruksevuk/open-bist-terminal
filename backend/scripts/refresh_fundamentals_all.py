"""Fundamentals (F-Score + SUE) + valuation (PE/PB) doldur, sonra yeniden skorla.

RESUME-SAFE: ticker-basi commit; surec olse bile tekrar calistir = kaldigi yerden devam.
Uzun surer (isyatirimhisse + fast_info, ticker-basi cekim). Kokten:
    backend\\.venv\\Scripts\\python.exe scripts\\refresh_fundamentals_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.data.quotes import populate_valuation  # noqa: E402
from app.db.base import SessionLocal, engine  # noqa: E402
from app.engine.fscore import populate_fundamentals  # noqa: E402


def _ticks() -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("select distinct ticker from daily_bars order by ticker"))]


def main() -> None:
    all_t = _ticks()

    # 1) Fundamentals (F-Score + SUE) — sadece eksik olanlar (resume)
    with engine.connect() as c:
        done = {r[0] for r in c.execute(text("select distinct ticker from fundamentals"))}
    todo = [t for t in all_t if t not in done]
    print(f"[1/3] fundamentals: {len(all_t)} isim, {len(done)} dolu, {len(todo)} kalan cekiliyor...", flush=True)
    if todo:
        with SessionLocal() as s:
            populate_fundamentals(s, todo)

    # 2) Valuation (PE/PB) — resume-safe (dolu olani atlar)
    print("[2/3] valuation (PE/PB) cekiliyor...", flush=True)
    with SessionLocal() as s:
        populate_valuation(s, all_t)

    # 3) Yeniden skorla (fundamentals faktorleri artik gercek deger)
    print("[3/3] yeniden skorlaniyor...", flush=True)
    try:
        from app.pipeline import refresh_scores
        with SessionLocal() as s:
            refresh_scores(s)
    except Exception as exc:  # noqa: BLE001 — veri zaten yazildi; skor ayrica kosulabilir
        print(f"[warn] yeniden skorlama hatasi (veri yazildi, refresh.bat ile skorla): {exc}", flush=True)

    with engine.connect() as c:
        f = c.execute(text("select count(*) from fundamentals")).scalar()
        pe = c.execute(text("select count(*) from fundamentals where pe is not null")).scalar()
        fok = c.execute(text("select count(*) from fundamentals where piotroski_f is not null")).scalar()
    print(f"[done] fundamentals {f} satir (F gecerli {fok}), PE/PB dolu {pe}. Yeniden skorlandi.", flush=True)
    if f < len(all_t) or pe < len(all_t):
        print("[not] eksik kaldiysa bu script'i tekrar calistir — kaldigi yerden devam eder.", flush=True)


if __name__ == "__main__":
    main()
