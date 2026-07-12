"""Setup olay-çalışması (event study) — her setup'ı KENDİ verimizde doğrular.

Bu bir DOĞRULAMA'dır, optimizasyon DEĞİL. Prior parametreler TEK koşumla ölçülür
(§9.5 deneme disiplini). setup_evidence config'e yazılır; API kanıt paneli okur.

Kullanım:
    python scripts/run_event_study.py            # koştur + config'e yaz + tablo bas
    python scripts/run_event_study.py --dry-run  # config'e yazma
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.event_study import run_event_study  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.engine.setups import SETUP_LABELS  # noqa: E402


def _fmt(v, nd=4):
    return "  n/a" if v is None else f"{v:+.{nd}f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup event study (PIT doğrulama)")
    ap.add_argument("--dry-run", action="store_true", help="config'e yazma (setup_evidence)")
    args = ap.parse_args()

    t0 = time.time()
    with SessionLocal() as session:
        res = run_event_study(session, write=not args.dry_run)
    dt = time.time() - t0

    if "setups" not in res:
        print(f"[warn] {res.get('note', 'sonuç yok')}")
        return

    print(f"\n=== SETUP EVENT STUDY (PIT doğrulama) — {res['n_tickers']} ticker, "
          f"{dt:.1f}s ===")
    rtc = res.get("round_trip_cost_pct")
    if rtc is not None:
        print(f"İşlem maliyeti (round-trip): %{rtc * 100:.3f} — trade-sim NET buna göre.")
    print("PARAMETRE ARAMASI YOK — prior tek koşum (§9.5).\n")
    hdr = (f"{'setup':22s} {'n_ev':>5s} {'n_gün':>5s} {'hit5':>6s} "
           f"{'exc1':>8s} {'exc3':>8s} {'exc5':>8s} {'exc10':>8s} "
           f"{'t5(NW)':>7s} {'PF':>6s}  verdict")
    print(hdr)
    print("-" * len(hdr))
    for name, s in res["setups"].items():
        label = SETUP_LABELS.get(name, name)
        if s.get("n_events", 0) == 0:
            print(f"{label:22s} {'0':>5s} {'-':>5s} {'-':>6s} "
                  f"{'-':>8s} {'-':>8s} {'-':>8s} {'-':>8s} {'-':>7s} {'-':>6s}  {s['verdict']}")
            continue
        ex = s["excess"]
        hit5 = s.get("hit_rate_5d")
        pf = s.get("profit_factor")
        t5 = s.get("t_newey_west_5d")
        print(f"{label:22s} {s['n_events']:>5d} {s['n_days']:>5d} "
              f"{(hit5 if hit5 is not None else 0):>6.2f} "
              f"{_fmt(ex.get('1', {}).get('mean_excess'))} "
              f"{_fmt(ex.get('3', {}).get('mean_excess'))} "
              f"{_fmt(ex.get('5', {}).get('mean_excess'))} "
              f"{_fmt(ex.get('10', {}).get('mean_excess'))} "
              f"{(t5 if t5 is not None else 0):>7.2f} "
              f"{(pf if pf is not None else 0):>6.2f}  {s['verdict']}")

    # PEAD notu
    pead = res.get("pead_drift", {})
    print(f"\n{SETUP_LABELS.get('pead_drift', 'pead_drift'):22s} "
          f"verdict={pead.get('verdict')} — {pead.get('note', '')}")

    # bootstrap CI (5g) detayı + trade-sim GROSS vs NET (friction realizmi)
    print("\n--- 5g excess bootstrap %95 CI + trade-sim GROSS/NET ---")
    for name, s in res["setups"].items():
        if s.get("n_events", 0) == 0:
            continue
        e5 = s["excess"].get("5", {})
        ts = s["trade_sim"]
        print(f"  {SETUP_LABELS.get(name, name):22s} "
              f"CI[{_fmt(e5.get('ci95_low'))}, {_fmt(e5.get('ci95_high'))}]  "
              f"n={ts['n_trades']} "
              f"mean_R={ts['mean_R']}→net {ts['mean_R_net']}  "
              f"PF={ts['profit_factor']}→net {ts['profit_factor_net']}")

    if args.dry_run:
        print("\n[dry-run] setup_evidence config'e YAZILMADI.")
    else:
        print("\n[ok] setup_evidence config'e yazıldı.")


if __name__ == "__main__":
    main()
