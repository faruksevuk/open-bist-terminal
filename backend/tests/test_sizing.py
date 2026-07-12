"""Pozisyon boyutlandırma testleri (offline) — v0.2 §6 invariant'ları."""

from __future__ import annotations

from app.risk.sizing import position_size

CFG = {"base_r": 0.01, "k_atr": 2.0, "edge_factor_cap": 0.90,
       "max_name_pct": 0.30, "max_heat_pct": 0.06}


def test_worked_example():
    # equity 10k, fiyat 100, ATR 4 (=%4), r=1%, k=2 → per_share_risk=8, qty=floor(100/8)=12
    r = position_size(10_000, 100.0, 4.0, CFG)
    assert r["qty"] == 12
    assert r["notional"] == 1200.0
    assert abs(r["notional_pct"] - 0.12) < 1e-6
    assert r["capped_by"] is None
    assert r["fits_heat"] is True


def test_max_name_cap_binds_low_atr():
    # çok düşük ATR → büyük pozisyon → max_name (%30=3000) bağlar
    r = position_size(10_000, 100.0, 0.5, CFG)  # per_share_risk=1, ham qty=100, notional=10000
    assert r["capped_by"] == "max_name"
    assert r["notional"] <= 3000.0 + 1e-6


def test_edge_factor_cannot_overbet():
    # edge_factor>1 denemesi cap<1'e kırpılır → r_eff base_r'yi AŞAMAZ
    r = position_size(10_000, 100.0, 4.0, CFG, edge_factor=2.0)
    assert r["r_eff"] <= 0.01  # base_r
    assert abs(r["r_eff"] - 0.009) < 1e-9  # 0.01 * 0.90


def test_invalid_inputs():
    assert position_size(10_000, 0, 4.0, CFG)["valid"] is False
    assert position_size(10_000, 100, 0, CFG)["valid"] is False
