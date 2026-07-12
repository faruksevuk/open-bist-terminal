"""SCORING v0.2 seed config bütünlük testleri (DB gerektirmez).

Değişmez ilkeleri ve kritik karar invariant'larını sabitler — biri kazara
bozulursa test kırılır.
"""

from __future__ import annotations

import math

from app.seed_config import SEED_CONFIG


def test_factor_weights_sum_to_one():
    # Çok-faktör havuzu prior'u normalize (kalibrasyon sonradan öğrenir/üzerine yazar)
    fw = SEED_CONFIG["factor_weights"]
    total = sum(fw.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9), f"factor_weights toplamı {total} ≠ 1.0"
    # kanıtlı edge (low_vol) prior'da mevcut
    assert "low_vol" in fw


def test_quality_subweights_sum_to_one():
    q = SEED_CONFIG["weights"]["quality"]
    total = q["wf"] + q["wa"] + q["wv"]
    assert math.isclose(total, 1.0, abs_tol=1e-9), f"quality w toplamı {total} ≠ 1.0"


def test_no_leverage_above_one():
    # v0.2 §6: kaldıraç >1 AÇILMAZ.
    assert SEED_CONFIG["risk"]["max_leverage"] <= 1.0


def test_edge_factor_cap_below_one():
    # v0.2 §6: edge yalnızca base_r'yi kısabilir (cap<1), asla büyütemez.
    assert SEED_CONFIG["risk"]["edge_factor_cap"] < 1.0
    assert SEED_CONFIG["risk"]["edge_factor_floor"] < SEED_CONFIG["risk"]["edge_factor_cap"]


def test_edge_scaling_off_by_default():
    # Edge ölçülene dek saf ATR sizing.
    assert SEED_CONFIG["risk"]["edge_scaling_enabled"] is False
    assert SEED_CONFIG["risk"]["edge_min_live_trades"] >= 100


def test_news_asymmetry():
    # v0.2 §3.5: negatif tam (-20), pozitif düşük tavan (+12), anchored-değil daha da düşük.
    n = SEED_CONFIG["news"]
    assert n["neg_cap"] == -20
    assert n["pos_cap"] == 12
    assert n["pos_cap_unanchored"] < n["pos_cap"]


def test_min_move_floor_is_policy_knob_present():
    # M = evren tabanı (ödül değil) — eşik config'te var ve makul.
    assert 0.0 < SEED_CONFIG["thresholds"]["min_move_atr_pct"] < 0.10


def test_pead_decay_slower_than_default():
    # v0.2 §8: PEAD penceresi global hızlı sönümle boğulmasın.
    d = SEED_CONFIG["decay_halflife_days"]
    assert d["finansal_tablo"] > d["diger"]


def test_required_config_keys_present():
    required = {
        "weights",
        "thresholds",
        "risk",
        "risk_valve",
        "news",
        "event_durations",
        "decay_halflife_days",
        "cadence",
        "calibration",
        "prompts",
    }
    assert required.issubset(SEED_CONFIG.keys())
