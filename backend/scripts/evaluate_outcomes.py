"""Canlı sinyal sonuç-takibi (OOS) — bağımsız değerlendirme + Türkçe özet tablo.

Her SetupSignal'ın tetik-sonrası ne olduğunu (target/stop/time_exit/no_entry) event-study
ile AYNI stop-önce konvansiyonuyla ölçer; SetupOutcome tablosuna upsert eder ve dürüst
beklenti bloğunu basar.

Kullanım:
    python scripts/evaluate_outcomes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.engine.setup_outcomes import evaluate_outcomes, outcome_summary  # noqa: E402


def main() -> None:
    with SessionLocal() as session:
        oc = evaluate_outcomes(session)
        print(f"[ok] {oc['evaluated']} sinyal değerlendirildi | kapalı: {oc['closed']} | "
              f"beklemede: {oc['pending']} | giriş oldu: {oc['filled']} | "
              f"giriş-yok: {oc['no_entry']}")

        summ = outcome_summary(session)
        per = summ["per_setup"]
        if not per:
            print("[info] Henüz hiç sinyal sonucu yok — takip biriktikçe dolacak.")
            return

        print("\n=== CANLI SONUÇ TAKİBİ (OOS) — setup başına ===")
        hdr = (f"{'setup':22s} {'kapalı':>6s} {'bekle':>6s} {'giriş-yok':>9s} "
               f"{'isabet':>7s} {'ort R':>7s} {'top R':>7s} {'ort gün':>7s}")
        print(hdr)
        print("-" * len(hdr))
        for _, s in per.items():
            isabet = f"{s['isabet']*100:.0f}%" if s["isabet"] is not None else "—"
            ort_r = f"{s['ort_r']:+.2f}" if s["ort_r"] is not None else "—"
            top_r = f"{s['toplam_r']:+.2f}" if s["toplam_r"] is not None else "—"
            ort_g = f"{s['ort_gun']:.1f}" if s["ort_gun"] is not None else "—"
            print(f"{s['setup_label']:22s} {s['n_closed']:>6d} {s['n_pending']:>6d} "
                  f"{s['n_no_entry']:>9d} {isabet:>7s} {ort_r:>7s} {top_r:>7s} {ort_g:>7s}")

        ov = summ["overall"]
        print("-" * len(hdr))
        ov_isabet = f"{ov['isabet']*100:.0f}%" if ov["isabet"] is not None else "—"
        ov_ort = f"{ov['ort_r']:+.2f}" if ov["ort_r"] is not None else "—"
        ov_top = f"{ov['toplam_r']:+.2f}" if ov["toplam_r"] is not None else "—"
        print(f"{'GENEL':22s} {ov['n_closed']:>6d} {ov['n_pending']:>6d} "
              f"{ov['n_no_entry']:>9d} {ov_isabet:>7s} {ov_ort:>7s} {ov_top:>7s}")

        e = summ["expectancy"]
        print("\n=== DÜRÜST BEKLENTİ ===")
        print(f"  risk/işlem (base_r): {e['risk_per_trade']}")
        print(f"  ölçülen: {e['measured_r_per_week']}R/hafta → ~%{e['expected_weekly_pct']:.2f}/hafta")
        print(f"  hedef %{e['target_weekly_pct']:.0f}/hafta için gereken: {e['needed_r_per_week']:.0f}R/hafta")
        print(f"  açık (gap): {e['gap']:.1f}R/hafta")
        print(f"  → {e['gap_note']}")


if __name__ == "__main__":
    main()
