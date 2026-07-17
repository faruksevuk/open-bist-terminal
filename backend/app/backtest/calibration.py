"""Faktör ağırlığı kalibrasyonu — tez-bağımsız, veri-öğrenir (kullanıcı kararı C).

Her faktörün backtest IC'sini ölçer, ağırlığı IC'ye orantılı yapar (edge yoksa ~0).
Edge YARILANIR (deflate, §9.7 McLean-Pontiff). PIT-backtest edilemeyen kalite için
research-temelli küçük prior. Sonuç config 'factor_weights'e yazılır; scoring okur.

composite_ic_live: kullanıcıya GÖSTERİLEN nihai skorun (canlı ağırlıklar + valf + haber
dahil, scores tablosundaki haliyle) gerçekleşen 5g rank-IC sicili — denetim bulgusu:
faktörler tek tek ölçülüyordu ama bileşik skorun canlı öngörü gücü hiç izlenmiyordu.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.runner import run_factor_diagnostic
from app.config_store import set_config

log = logging.getLogger(__name__)

# scoring faktörü → backtest teşhis faktörü eşlemesi
_MAP = {
    "low_vol": "low_atr",
    "momentum": "momentum(strength)",   # scoring momentum = bileşik güç (t=1.60 > düz roc20)
    "roc20": "roc20",                   # düz 20g momentum (ayrı faktör; t=0.89)
    "reversal": "oversold(reversal)",
    "stab": "stab",
    "rev5": "rev5",               # kısa-vade dönüş (pct(-roc5)); ölçülü edge (NW t=2.14)
}
# PIT-backtest edilemeyen faktörler → research-temelli prior (IC eşdeğeri)
QUALITY_PRIOR_IC = 0.010  # Piotroski (research §3)
PEAD_PRIOR_IC = 0.015     # kazanç-sürprizi drifti — literatürde güçlü/yerleşik (research §2 "altın")
VALUE_PRIOR_IC = 0.012    # value (ucuzluk) — yerleşik faktör (research §3)
CAUSE_PRIOR_IC = 0.0      # ko-hareket sebep proxy'si — edge göstermedi


def calibrate_factor_weights(session: Session, deflate: float = 0.5, write: bool = True) -> dict:
    diag = run_factor_diagnostic(session)
    ic = {f: (m.get("mean_ic") or 0.0) for f, m in diag["factors"].items()}

    raw: dict[str, float] = {}
    for factor, diag_name in _MAP.items():
        raw[factor] = max(0.0, ic.get(diag_name, 0.0) * deflate)  # negatif IC → 0
    # Prior'lar da AYNI deflate ölçeğinde — aksi halde normalize sonrası prior'lar
    # öğrenilen (yarılanmış) faktörlere göre sistematik olarak fazla ağırlanırdı.
    raw["quality"] = QUALITY_PRIOR_IC * deflate
    raw["pead"] = PEAD_PRIOR_IC * deflate
    raw["value"] = VALUE_PRIOR_IC * deflate
    raw["cause"] = CAUSE_PRIOR_IC * deflate

    total = sum(raw.values()) or 1.0
    weights = {f: round(v / total, 3) for f, v in raw.items()}

    if write:
        set_config(session, "factor_weights", weights)

    return {
        "weights": weights,
        "raw_deflated_ic": {f: round(v, 4) for f, v in raw.items()},
        "diagnostic_ic": {f: round(v, 4) for f, v in ic.items()},
        "deflate": deflate,
        "note": "Ağırlık ∝ max(0, IC×0.5). Negatif/sıfır-edge faktör → 0 ağırlık. "
                "quality PIT-backtest edilemediği için research-temelli prior.",
    }


def composite_ic_live(session: Session, horizon: int = 5, min_names: int = 30) -> dict:
    """Geçmiş her skor gününde rank-IC(gösterilen skor, +5g gerçekleşen adjusted getiri).

    Sentetik yeniden-hesap DEĞİL: scores tablosundaki gerçekten üretilmiş satırlar
    kullanılır (ağırlık değişimleri, valf, haber — hepsi dahil; "kullanıcı ne gördüyse o").
    Gün başına o günün SON koşumu alınır. Sonuç config 'composite_ic_live'a yazılır.
    Sistem gençken n küçük — UI bunu söyler; kanıt değil, izleme.
    """
    from app.db.models import DailyBar, Horizon, Score

    rows = session.execute(
        select(Score.ticker, Score.as_of, Score.score)
        .where(Score.horizon == Horizon.swing, Score.score.isnot(None))
    ).all()
    if not rows:
        out = {"as_of": datetime.now(timezone.utc).isoformat(), "points": [], "n_days": 0,
               "note": "skor geçmişi yok"}
        set_config(session, "composite_ic_live", out)
        return out
    sdf = pd.DataFrame(rows, columns=["ticker", "as_of", "score"])
    sdf["d"] = pd.to_datetime(sdf["as_of"]).dt.date
    # gün başına son koşum
    sdf = sdf.sort_values("as_of").groupby(["d", "ticker"], as_index=False).last()

    since = min(sdf["d"])
    bars = session.execute(
        select(DailyBar.ticker, DailyBar.date, DailyBar.adj_close, DailyBar.close)
        .where(DailyBar.date >= since)
    ).all()
    bdf = pd.DataFrame(bars, columns=["ticker", "date", "adj_close", "close"])
    if bdf.empty:
        out = {"as_of": datetime.now(timezone.utc).isoformat(), "points": [], "n_days": 0,
               "note": "bar yok"}
        set_config(session, "composite_ic_live", out)
        return out
    bdf["px"] = bdf["adj_close"].where(bdf["adj_close"].notna(), bdf["close"])
    wide = bdf.pivot_table(index="date", columns="ticker", values="px").sort_index()
    fwd = wide.shift(-horizon) / wide - 1.0   # t → t+h işlem barı getirisi

    points: list[dict] = []
    for d, g in sdf.groupby("d"):
        # skor gününe eşit ya da önceki son bar günü (skor akşam koşar → o günün barı)
        idx = wide.index[wide.index <= d]
        if len(idx) == 0:
            continue
        bar_day = idx[-1]
        f = fwd.loc[bar_day]
        merged = g.set_index("ticker")["score"].to_frame().join(f.rename("fwd")).dropna()
        if len(merged) < min_names:
            continue
        ic = float(merged["score"].rank().corr(merged["fwd"].rank()))
        if np.isnan(ic):
            continue
        points.append({"date": str(d), "n": int(len(merged)), "ic": round(ic, 4)})

    ics = [p["ic"] for p in points]
    mean_ic = float(np.mean(ics)) if ics else None
    t_stat = float(np.mean(ics) / (np.std(ics, ddof=1) / np.sqrt(len(ics)))) if len(ics) >= 3 and np.std(ics, ddof=1) > 0 else None
    out = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "horizon": horizon,
        "n_days": len(points),
        "mean_ic": round(mean_ic, 4) if mean_ic is not None else None,
        "t": round(t_stat, 2) if t_stat is not None else None,
        "points": points[-60:],  # son 60 gözlem (UI zaman serisi)
        "note": ("Gösterilen NİHAİ skorun canlı rank-IC'si (+{h}g, adjusted). Gözlem az iken "
                 "gürültülüdür — izleme amaçlı, kanıt değil.").format(h=horizon),
    }
    set_config(session, "composite_ic_live", out)
    return out


def store_factor_diagnostic(session: Session) -> dict:
    """run_factor_diagnostic'i koştur + scoring-faktör anahtarlarına eşle + config'e sakla.

    Dashboard 'Ayarlar' sekmesi bunu okur: her faktörün yanında ölçülen rank-IC/t/isabet
    gösterilir (kanıt-bilinçli ağırlık ayarı). quality/pead/value/cause fiyat-ölçülemez →
    research prior olarak işaretlenir. Tek rejim (2y) — kanıt değil, işaret.
    """
    diag = run_factor_diagnostic(session)
    dfac = diag.get("factors", {})
    measured: dict[str, dict] = {}
    for skey, dname in _MAP.items():           # scoring anahtarı → diagnostic adı
        m = dfac.get(dname) or {}
        if m.get("mean_ic") is not None:
            measured[skey] = {
                "ic": m.get("mean_ic"), "t": m.get("t_newey_west"),
                "hit": m.get("hit_rate"), "n": m.get("n"),
                "ci_excludes_zero": m.get("ci_excludes_zero"),
            }
    blob = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "params": diag.get("params"),
        "measured": measured,
        "priors": {   # fiyat-ölçülemeyen (fundamental/prior) faktörler — IC eşdeğeri
            "quality": QUALITY_PRIOR_IC, "pead": PEAD_PRIOR_IC,
            "value": VALUE_PRIOR_IC, "cause": CAUSE_PRIOR_IC,
        },
        "note": ("Fiyat-faktör rank-IC'si (5g ileri, evren-içi percentile). "
                 "quality/pead/value/cause fiyattan ölçülemez → research prior. "
                 "Tek rejim (2y disinflasyon) — kanıt değil, işaret; edge yarılanarak yorumlanmalı."),
    }
    set_config(session, "factor_diagnostic", blob)
    return blob
