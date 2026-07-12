"""Skorlama saf-fonksiyon testleri (DB/ağ gerektirmez) — v0.2 §4-5 invariant'ları."""

from __future__ import annotations

import pandas as pd

from app.engine.scoring import _pct, _signal_after_gate, _z


def test_band_after_gate_cannot_buy_without_gate():
    # Kapı geçilmemişse yüksek skor bile Al/Güçlü Al olamaz (v0.2 §5.3)
    assert _signal_after_gate(80, meets=False) == "hold"
    assert _signal_after_gate(65, meets=False) == "hold"
    assert _signal_after_gate(40, meets=False) == "reduce"
    assert _signal_after_gate(20, meets=False) == "sell"


def test_band_after_gate_buy_side_only_when_meets():
    assert _signal_after_gate(80, meets=True) == "strong_buy"
    assert _signal_after_gate(60, meets=True) == "buy"
    assert _signal_after_gate(74.9, meets=True) == "buy"


def test_strong_buy_ceiling_with_one_risk_flag():
    # core<=100, news_pos<=12, rg=0.6 → max (100+12)*0.6 = 67.2 < 75 → Güçlü Al imkansız
    core, news_pos, rg = 100, 12, 0.6
    score = (core + news_pos) * rg
    assert round(score, 1) == 67.2
    assert _signal_after_gate(score, meets=True) == "buy"  # strong_buy değil


def test_zscore_constant_series_is_zero():
    z = _z(pd.Series([5.0, 5.0, 5.0]))
    assert (z == 0).all()


def test_percentile_monotonic():
    p = _pct(pd.Series([1.0, 2.0, 3.0, 4.0]))
    assert p.iloc[0] < p.iloc[-1]
    assert 0 <= p.min() and p.max() <= 100
