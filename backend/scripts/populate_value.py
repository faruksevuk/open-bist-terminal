"""PE/PB doldur (value faktörü) — RESUME-SAFE. fast_info kırılgan; tekrar çalıştır=kaldığı yerden.

Kullanım: python scripts/populate_value.py   (ya da kökten value.bat)
Her ticker'da commit eder; süreç ölse bile ilerleme kalır, tekrar çalıştırınca devam eder.
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


def main() -> None:
    with engine.connect() as c:
        ticks = [r[0] for r in c.execute(text("select distinct ticker from daily_bars order by ticker"))]
        before = c.execute(text("select count(*) from fundamentals where pe is not null")).scalar()
    print(f"[..] {len(ticks)} isim, mevcut PE/PB dolu {before} → kalanlar çekiliyor (yavaş)…")
    with SessionLocal() as s:
        n = populate_valuation(s, ticks)
    with engine.connect() as c:
        after = c.execute(text("select count(*) from fundamentals where pe is not null")).scalar()
    print(f"[done] bu çalıştırmada +{n} | toplam PE/PB dolu: {after}/{len(ticks)}")
    if after < len(ticks):
        print("[not] eksik kaldıysa value.bat'ı tekrar çalıştır — kaldığı yerden devam eder.")
    print("Sonra: refresh.bat (skorları value ile güncelle) veya kalibrasyon.")


if __name__ == "__main__":
    main()
