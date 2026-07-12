"""İndikatör motoru birim testleri — sentetik veri (ağ gerektirmez)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.indicators import compute_indicators, latest_snapshot, rsi


def _synthetic(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.001, 0.02, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "adj_close": close, "volume": vol},
        index=idx,
    )


def test_rsi_bounds():
    df = _synthetic()
    r = rsi(df["close"], 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_indicators_present_and_sane():
    ind = compute_indicators(_synthetic())
    for col in ["ema20", "ema50", "ema200", "rsi14", "atr14", "atr_pct",
                "adx14", "macd_hist", "roc5", "bb_upper", "bb_lower", "avg_tl_vol_20"]:
        assert col in ind.columns, f"eksik kolon: {col}"
    last = ind.iloc[-1]
    assert last["atr14"] > 0
    assert 0 <= last["atr_pct"] < 1
    assert last["bb_upper"] > last["bb_lower"]
    assert 0 <= last["adx14"] <= 100


def test_adjusted_series_used():
    # adj_close = close*2 → tüm fiyat indikatörleri 2x ölçeklenmeli (atr_pct değişmez)
    base = _synthetic()
    scaled = base.copy()
    scaled["adj_close"] = scaled["close"] * 2.0
    a = compute_indicators(base).iloc[-1]
    b = compute_indicators(scaled).iloc[-1]
    assert abs(b["close"] / a["close"] - 2.0) < 1e-6
    assert abs(b["atr_pct"] - a["atr_pct"]) < 1e-6  # oransal → ölçekten bağımsız


def test_latest_snapshot_shape():
    snap = latest_snapshot(_synthetic())
    assert snap is not None
    assert snap["bars"] == 300
    assert snap["close"] > 0
    assert snap["rsi14"] is not None
