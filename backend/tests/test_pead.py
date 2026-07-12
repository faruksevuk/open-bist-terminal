"""PEAD SUE testi — kümülatif→diskret ayrıştırma + seasonal-naive (offline)."""

from __future__ import annotations

import pandas as pd

from app.engine.pead import _discrete_quarterly, compute_sue


def _cumulative_df(discrete: dict[tuple[int, int], float], code: str = "3L") -> pd.DataFrame:
    """Diskret çeyrekliklerden isyatirim-benzeri KÜMÜLATİF df kur."""
    cols = {}
    for (y, q), _ in discrete.items():
        m = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        cum = sum(discrete[(y, qq)] for qq in range(1, q + 1) if (y, qq) in discrete)
        cols[f"{y}/{m}"] = cum
    row = {"FINANCIAL_ITEM_CODE": code, "FINANCIAL_ITEM_NAME_EN": "net income", **cols}
    return pd.DataFrame([row])


def test_decumulation():
    disc = {(2023, 1): 10, (2023, 2): 12, (2023, 3): 11, (2023, 4): 15}
    df = _cumulative_df(disc)
    s = _discrete_quarterly(df, "3L")
    assert abs(s[(2023, 1)] - 10) < 1e-9
    assert abs(s[(2023, 2)] - 12) < 1e-9  # H1(22) - Q1(10) = 12
    assert abs(s[(2023, 4)] - 15) < 1e-9


# dalgalı taban (geçmiş tahmin hataları std>0 olsun) + son çeyrek sürprizi
_BASE = {
    (2023, 1): 10.0, (2023, 2): 12.0, (2023, 3): 9.0, (2023, 4): 11.0,
    (2024, 1): 12.0, (2024, 2): 13.0, (2024, 3): 11.0, (2024, 4): 10.0,
    (2025, 1): 13.0, (2025, 2): 15.0, (2025, 3): 10.0,
}


def test_positive_surprise_sign():
    disc = {**_BASE, (2025, 4): 45.0}  # büyük pozitif sürpriz
    r = compute_sue("TEST", _cumulative_df(disc))
    assert r["sign"] == 1, r
    assert r["sue"] > 0


def test_negative_surprise_sign():
    disc = {**_BASE, (2025, 4): -30.0}  # çöküş
    r = compute_sue("TEST", _cumulative_df(disc))
    assert r["sign"] == -1, r


def test_insufficient_data_na():
    disc = {(2024, 1): 10, (2024, 2): 11}
    r = compute_sue("TEST", _cumulative_df(disc))
    assert r["sue"] is None
