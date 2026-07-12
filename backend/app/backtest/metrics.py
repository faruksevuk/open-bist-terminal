"""Backtest istatistik metrikleri — SCORING v0.2 §9.4.

Rank IC (Spearman), Newey-West düzeltilmiş t (overlapping/autocorr için),
block-bootstrap güven aralığı. Naif t YANILTICIDIR (kesitsel korelasyon + overlap).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def spearman_ic(score: pd.Series | list, fwd: pd.Series | list) -> float:
    """Spearman IC = rank'lerin Pearson korelasyonu (scipy gerektirmez)."""
    df = pd.DataFrame({"s": list(score), "f": list(fwd)}).apply(pd.to_numeric, errors="coerce").dropna()
    if len(df) < 3 or df["s"].nunique() < 2 or df["f"].nunique() < 2:
        return float("nan")
    return float(df["s"].rank().corr(df["f"].rank()))  # pearson-of-ranks = Spearman


def newey_west_tstat(x: np.ndarray, lags: int = 5) -> float:
    """Bir zaman serisinin ortalamasının Newey-West (HAC) t-istatistiği."""
    x = np.asarray([v for v in x if not np.isnan(v)], dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    mu = x.mean()
    e = x - mu
    gamma0 = float(np.mean(e * e))
    s = gamma0
    for lag in range(1, min(lags, n - 1) + 1):
        w = 1.0 - lag / (lags + 1.0)  # Bartlett
        cov = float(np.mean(e[lag:] * e[:-lag]))
        s += 2.0 * w * cov
    var_mean = s / n
    if var_mean <= 0:
        return float("nan")
    return float(mu / np.sqrt(var_mean))


def bootstrap_ci(x: np.ndarray, n_boot: int = 2000, alpha: float = 0.05, seed: int = 7):
    """Ortalamanın bootstrap güven aralığı (yüzdelik yöntemi)."""
    x = np.asarray([v for v in x if not np.isnan(v)], dtype=float)
    if len(x) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(x, size=len(x), replace=True).mean() for _ in range(n_boot)])
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (lo, hi)


def ic_summary(ic_series: list[float], nw_lags: int = 5) -> dict:
    arr = np.asarray([v for v in ic_series if not np.isnan(v)], dtype=float)
    n = len(arr)
    if n < 3:
        return {"n": n, "mean_ic": None, "note": "yetersiz gözlem"}
    mean_ic = float(arr.mean())
    std_ic = float(arr.std(ddof=1))
    t_plain = float(mean_ic / (std_ic / np.sqrt(n))) if std_ic > 0 else float("nan")
    t_nw = newey_west_tstat(arr, lags=nw_lags)
    lo, hi = bootstrap_ci(arr)
    return {
        "n": n,
        "mean_ic": round(mean_ic, 4),
        "std_ic": round(std_ic, 4),
        "t_plain": round(t_plain, 2),
        "t_newey_west": round(t_nw, 2),
        "ci95_low": round(lo, 4),
        "ci95_high": round(hi, 4),
        "hit_rate": round(float((arr > 0).mean()), 3),  # IC>0 oranı
        "ci_excludes_zero": bool(lo > 0 or hi < 0),
    }


def quintile_spread(pairs: list[tuple[float, float]], q: int = 5) -> dict:
    """(score, fwd_ret) çiftlerinden üst-alt çeyreklik ileri-getiri farkı."""
    if len(pairs) < q * 3:
        return {"note": "yetersiz çift", "n": len(pairs)}
    df = pd.DataFrame(pairs, columns=["score", "fwd"]).dropna()
    df["bucket"] = pd.qcut(df["score"].rank(method="first"), q, labels=False)
    means = df.groupby("bucket")["fwd"].mean()
    top, bot = float(means.iloc[-1]), float(means.iloc[0])
    return {
        "n": len(df),
        "top_bucket_fwd": round(top, 4),
        "bottom_bucket_fwd": round(bot, 4),
        "spread": round(top - bot, 4),
        "monotonic": bool(means.is_monotonic_increasing),
    }
