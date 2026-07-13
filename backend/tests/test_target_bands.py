"""target_bands volatilite konisi — pür fonksiyon testleri (DB'siz, deterministik)."""

import math

import numpy as np

from app.engine.target_bands import cone, sigma_daily_from_closes


def test_cone_log_symmetry():
    """Log-uzay simetri: alt*ust == spot^2 (exp(-x)*exp(+x)=1)."""
    low, high = cone(100.0, 0.02, 5, 1.0)
    assert low < 100.0 < high
    assert math.isclose(low * high, 100.0 ** 2, rel_tol=1e-9)


def test_cone_wider_with_horizon():
    """Daha uzun vade -> daha geniş bant."""
    _, h5 = cone(100.0, 0.02, 5, 1.0)
    _, h30 = cone(100.0, 0.02, 30, 1.0)
    assert h30 > h5


def test_cone_sqrt_time_scaling():
    """Oynaklık sqrt(zaman) ile ölçeklenir: log-genişlik oranı = sqrt(30/5)."""
    _, h5 = cone(100.0, 0.02, 5, 1.0)
    _, h30 = cone(100.0, 0.02, 30, 1.0)
    ratio = math.log(h30 / 100.0) / math.log(h5 / 100.0)
    assert math.isclose(ratio, math.sqrt(30 / 5), rel_tol=1e-9)


def test_cone_z_scaling():
    """2 sigma log-genişliği 1 sigma'nın tam 2 katı."""
    _, h1 = cone(100.0, 0.02, 5, 1.0)
    _, h2 = cone(100.0, 0.02, 5, 2.0)
    assert math.isclose(math.log(h2 / 100.0), 2 * math.log(h1 / 100.0), rel_tol=1e-9)


def test_sigma_short_history_none():
    """<20 getiri -> güvenilmez -> None."""
    assert sigma_daily_from_closes([100, 101, 102, 103]) is None


def test_sigma_constant_growth_none():
    """Sabit-oranlı seri: log-getiri sabit -> std=0 -> dejenere -> None."""
    closes = [100 * (1.01 ** i) for i in range(40)]
    assert sigma_daily_from_closes(closes) is None


def test_sigma_recovers_known_vol():
    """Bilinen ~%2 oynaklıklı seriden ~0.02 sigma geri gelmeli."""
    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.02, 120)))
    s = sigma_daily_from_closes(closes)
    assert s is not None and 0.015 < s < 0.025


def test_sigma_ignores_nonpositive():
    """0/negatif kapanışlar (bozuk veri) atlanır, çökmez."""
    rng = np.random.default_rng(1)
    good = list(100 * np.exp(np.cumsum(rng.normal(0.0, 0.02, 60))))
    s = sigma_daily_from_closes(good + [0.0, -5.0])
    assert s is not None and s > 0
