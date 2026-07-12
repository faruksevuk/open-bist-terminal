"""Rank-IC backtest (M5 kapısı). Kullanım: python scripts/run_backtest.py"""

from __future__ import annotations

import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.runner import run_ic_backtest  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402


def main() -> None:
    with SessionLocal() as session:
        res = run_ic_backtest(session)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
