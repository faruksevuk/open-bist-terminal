"""Skorla + tara + bağlam + sonuç-takibi (refresh.bat bunu çağırır).

Gerçek iş app/pipeline.py'de (scheduler ile AYNI fonksiyonlar — çatal yok); bu script
yalnız çalıştırır ve özetleri yazdırır.

Kullanım:
    python scripts/run_scoring.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.pipeline import refresh_scores  # noqa: E402


def main() -> None:
    with SessionLocal() as session:
        out = refresh_scores(session)

    sc = out.get("scores", {})
    if not sc.get("written"):
        print(f"[warn] {sc.get('note', 'skor yazılamadı')}")
        return
    print(f"[ok] {sc['written']} skor yazıldı | kapı geçen: {sc['gated']} | "
          f"eşik geçen: {sc['meets']} | eşik(eff): {sc['threshold_eff']}")
    if sc["meets"] == 0:
        print("[info] Bugün setup yok — nakitte bekle (dürüst boş durum).")

    st = out.get("setups", {})
    if "error" in st:
        print(f"[warn] setup taraması hata: {st['error']}")
    else:
        print(f"[ok] setup taraması: {st.get('signals', 0)} aktif sinyal.")
        if st.get("by_setup"):
            print(f"[info] setup dağılımı: {st['by_setup']}")

    cx = out.get("context", {})
    if "error" in cx:
        print(f"[warn] bağlam derleme hata: {cx['error']}")
    elif "regime" in cx:
        print(f"[ok] bağlam: rejim={cx['regime']} ({cx['regime_score']}) | "
              f"genişlik %{cx['breadth_ema50'] * 100:.0f}")
    else:
        print(f"[warn] bağlam: {cx.get('note', 'derlenemedi')}")

    oc = out.get("outcomes", {})
    if "error" in oc:
        print(f"[warn] sonuç-takibi hata: {oc['error']}")
    else:
        print(f"[ok] sonuç-takibi: {oc.get('evaluated', 0)} sinyal değerlendirildi | "
              f"kapalı: {oc.get('closed', 0)} | beklemede: {oc.get('pending', 0)} | "
              f"giriş-yok: {oc.get('no_entry', 0)}")


if __name__ == "__main__":
    main()
