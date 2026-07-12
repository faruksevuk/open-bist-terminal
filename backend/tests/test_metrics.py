"""Backtest metrik testleri — Spearman IC, Newey-West, bootstrap (offline)."""

from __future__ import annotations

import numpy as np

from app.backtest.metrics import (
    bootstrap_ci,
    ic_summary,
    newey_west_tstat,
    quintile_spread,
    spearman_ic,
)


def test_spearman_monotonic():
    assert abs(spearman_ic([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) - 1.0) < 1e-9
    assert abs(spearman_ic([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) + 1.0) < 1e-9


def test_newey_west_positive_mean():
    x = np.full(50, 0.05) + np.random.default_rng(1).normal(0, 0.01, 50)
    t = newey_west_tstat(x, lags=5)
    assert t > 3  # güçlü pozitif ortalama


def test_newey_west_zero_mean_small_t():
    x = np.random.default_rng(2).normal(0, 1, 200)
    assert abs(newey_west_tstat(x, lags=5)) < 2.5  # sıfır ortalama → küçük t


def test_bootstrap_ci_brackets_mean():
    x = np.random.default_rng(3).normal(0.5, 0.1, 100)
    lo, hi = bootstrap_ci(x, n_boot=1000)
    assert lo < 0.5 < hi


def test_ic_summary_fields():
    ic = list(np.random.default_rng(4).normal(0.03, 0.1, 80))
    s = ic_summary(ic)
    for k in ["mean_ic", "t_newey_west", "ci95_low", "ci95_high", "hit_rate", "ci_excludes_zero"]:
        assert k in s


def test_quintile_spread_monotonic_signal():
    # skor = forward ile mükemmel hizalı → pozitif spread, monotonik
    pairs = [(float(i), float(i) + np.random.default_rng(i).normal(0, 0.5)) for i in range(50)]
    q = quintile_spread(pairs, q=5)
    assert q["spread"] > 0
