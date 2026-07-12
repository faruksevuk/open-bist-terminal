"""Sektör & Makro bağlam — saf skorlama + derleme (DB/ağ patch'lenir) testleri."""

from __future__ import annotations

import numpy as np
import pandas as pd

import app.engine.sector_macro as sm
from app.engine.sector_macro import (
    compute_market_context,
    context_tilt,
    regime_label,
    regime_score,
    score_sectors,
)


def test_regime_score_monotonic():
    # tüm sinyaller olumlu → yüksek; olumsuz → düşük
    good = regime_score(above_ema50=True, above_ema200=True, breadth_ema50=0.8, vol_20d=0.01)
    bad = regime_score(above_ema50=False, above_ema200=False, breadth_ema50=0.2, vol_20d=0.05)
    assert good > bad
    assert 0.0 <= good <= 100.0 and 0.0 <= bad <= 100.0
    assert good >= 60.0  # açık risk-on
    assert bad <= 40.0   # açık risk-off


def test_regime_score_bounded():
    # aşırı girdilerde bile 0-100 clamp
    assert regime_score(True, True, 1.0, 0.0) <= 100.0
    assert regime_score(False, False, 0.0, 1.0) >= 0.0


def test_regime_label_thresholds():
    assert regime_label(75.0) == "risk_on"
    assert regime_label(25.0) == "risk_off"
    assert regime_label(50.0) == "neutral"


def test_score_sectors_ranks_and_labels():
    rows = [
        {"sector": "Lider", "rel_strength_20d": 0.08, "mom_20d": 0.10, "above_ema50": 0.9},
        {"sector": "Orta", "rel_strength_20d": 0.0, "mom_20d": 0.02, "above_ema50": 0.5},
        {"sector": "Geride", "rel_strength_20d": -0.06, "mom_20d": -0.04, "above_ema50": 0.2},
    ]
    scored = score_sectors(rows)
    by = {r["sector"]: r for r in scored}
    # en güçlü sektör en yüksek skor + rank 1 + 'lider'
    assert by["Lider"]["score"] > by["Orta"]["score"] > by["Geride"]["score"]
    assert by["Lider"]["rank"] == 1
    assert by["Lider"]["trend"] == "lider"
    assert by["Geride"]["trend"] == "geride"
    assert all(0.0 <= r["score"] <= 100.0 for r in scored)


def test_score_sectors_empty():
    assert score_sectors([]) == []


def test_context_tilt_bounded_and_monotonic():
    base = 60.0
    hi = context_tilt(base, sector_score=100.0, regime_score_=100.0)  # lider + risk-on
    lo = context_tilt(base, sector_score=0.0, regime_score_=0.0)      # geride + risk-off
    mid = context_tilt(base, sector_score=50.0, regime_score_=50.0)
    assert hi > mid > lo
    assert 0.0 <= lo and hi <= 100.0
    # tilt bounded: ~±%30 (base 0.85, span 0.30 → çarpan [0.72, 1.32])
    assert hi <= base * 1.33
    assert lo >= base * 0.71


def test_context_tilt_none_is_neutral():
    # sector/regime None → 50 kabul (nötr), çökme yok
    n = context_tilt(50.0, None, None)
    assert n > 0.0


# --- derleme (compute_market_context) — load_daily + Security + fx patch'lenir ---

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """compute_market_context yalnız session.execute(select(Security...)).all() kullanır."""
    def __init__(self, secs):
        self._secs = secs

    def execute(self, _stmt):
        return _FakeResult(self._secs)


def _synth_series(n: int, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.02, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"adj_close": close, "close": close}, index=idx)


def test_compute_market_context_assembly(monkeypatch):
    # Sektör A = güçlü yükseliş, Sektör B = düşüş → A, B'nin üstünde sıralanmalı.
    secs = [(f"A{i}", "SektorA") for i in range(4)] + [(f"B{i}", "SektorB") for i in range(4)]
    panels = {t: _synth_series(250, 0.004 if s == "SektorA" else -0.002, seed=hash(t) % 9973)
              for t, s in secs}

    monkeypatch.setattr(sm, "get_config", lambda _s, _k: None)          # in-code default
    monkeypatch.setattr(sm, "load_daily", lambda _s, t: panels[t])
    monkeypatch.setattr("app.data.fx_macro.usdtry_trend",
                        lambda days=20: {"rate": None, "ret_20d": None, "trend": None, "stale": True})

    res = compute_market_context(_FakeSession(secs), min_bars=120)
    assert res is not None
    m = res["macro"]
    assert m["regime"] in ("risk_on", "neutral", "risk_off")
    assert 0.0 <= m["regime_score"] <= 100.0
    assert 0.0 <= m["breadth_ema50"] <= 1.0
    assert res["sectors"] and all("score" in s and "rank" in s and "trend" in s for s in res["sectors"])
    smap = res["sector_score"]
    assert set(smap) == {"SektorA", "SektorB"}
    assert smap["SektorA"] > smap["SektorB"]  # güçlü sektör önde
    assert res["sectors"][0]["rank"] == 1
