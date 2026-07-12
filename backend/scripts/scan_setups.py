"""Bağımsız setup taraması — setup_signals tablosunu tazeler (SETUPS v0.1).

Kullanım:
    python scripts/scan_setups.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.engine.setup_scan import scan_universe  # noqa: E402
from app.engine.setups import SETUP_LABELS  # noqa: E402


def main() -> None:
    with SessionLocal() as session:
        sig = scan_universe(session)
        if sig.empty:
            print("[info] Bugün aktif setup sinyali yok (dürüst boş durum).")
            return
        print(f"[ok] {len(sig)} aktif setup sinyali:")
        for _, r in sig.iterrows():
            label = SETUP_LABELS.get(r["setup"], r["setup"])
            print(f"  {r['ticker']:8s} {label:22s} güç={r['strength']:5.1f} "
                  f"giriş={r['entry_ref']:.4f} stop={r['stop']:.4f} hedef={r['target']:.4f}")


if __name__ == "__main__":
    main()
