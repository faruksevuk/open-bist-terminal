"""Evreni tohumla + yfinance geçmişini çek (M2 adım 1).

Kullanım:
    python scripts/fetch_history.py                 # tüm securities, 2y
    python scripts/fetch_history.py --period 6mo    # daha kısa
    python scripts/fetch_history.py THYAO GARAN      # sadece bu tickerlar
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.history import fetch_history  # noqa: E402
from app.data.universe_seed import seed_universe  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402


def main() -> None:
    argv = sys.argv[1:]
    period = "2y"
    tickers: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--period" and i + 1 < len(argv):
            period = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        tickers.append(a)
        i += 1
    tickers_arg = tickers or None

    with SessionLocal() as session:
        seeded = seed_universe(session)
        print(f"[ok] evren tohumlandı: {seeded} isim")
        print(f"[..] geçmiş çekiliyor (period={period}, ticker={tickers_arg or 'TÜMÜ'}) …")
        res = fetch_history(session, tickers=tickers_arg, period=period)

    total = sum(res.values())
    ok = sum(1 for v in res.values() if v > 0)
    print(f"[done] {ok}/{len(res)} ticker, toplam {total} bar yazıldı.")
    empties = [t for t, v in res.items() if v == 0]
    if empties:
        print(f"[warn] boş dönenler: {empties}")


if __name__ == "__main__":
    main()
