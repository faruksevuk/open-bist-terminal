"""Faktör ağırlığı kalibrasyonu — tez-bağımsız, veri-öğrenir (kullanıcı kararı C).

Her faktörün backtest IC'sini ölçer, ağırlığı IC'ye orantılı yapar (edge yoksa ~0).
Edge YARILANIR (deflate, §9.7 McLean-Pontiff). PIT-backtest edilemeyen kalite için
research-temelli küçük prior. Sonuç config 'factor_weights'e yazılır; scoring okur.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

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
