"""Piotroski F-Score logic testi — sentetik bilanço df (ağ gerektirmez)."""

from __future__ import annotations

import pandas as pd

from app.engine.fscore import ITEM, compute_fscore


def _fin_df(cur: dict, prev: dict, year_cur: int = 2025, year_prev: int = 2024) -> pd.DataFrame:
    """key→değer sözlüklerinden isyatirim-benzeri df kur (yıllık /12 kolonları)."""
    rows = []
    for key, code in ITEM.items():
        rows.append(
            {
                "FINANCIAL_ITEM_CODE": code,
                "FINANCIAL_ITEM_NAME_EN": key,
                f"{year_prev}/12": prev.get(key),
                f"{year_cur}/12": cur.get(key),
            }
        )
    return pd.DataFrame(rows)


def test_perfect_fscore_is_nine():
    # Her sinyali tetikleyecek mükemmel iyileşme
    prev = dict(net_income=50, total_assets=1000, current_assets=300, current_liab=200,
                long_term_liab=300, gross_profit=100, net_sales=500, cfo=60,
                share_capital=100, rights_issue=0)
    cur = dict(net_income=120, total_assets=1000, current_assets=400, current_liab=150,
               long_term_liab=200, gross_profit=200, net_sales=700, cfo=200,
               share_capital=100, rights_issue=0)
    r = compute_fscore("TEST", _fin_df(cur, prev))
    assert r["f_score"] == 9, r["signals"]


def test_weak_fscore_low():
    # Zarar, CFO negatif, kötüleşme, yeni hisse ihracı
    prev = dict(net_income=100, total_assets=1000, current_assets=400, current_liab=150,
                long_term_liab=100, gross_profit=200, net_sales=700, cfo=150,
                share_capital=100, rights_issue=0)
    cur = dict(net_income=-50, total_assets=1200, current_assets=300, current_liab=250,
               long_term_liab=300, gross_profit=80, net_sales=500, cfo=-20,
               share_capital=140, rights_issue=40)
    r = compute_fscore("TEST", _fin_df(cur, prev))
    assert r["f_score"] <= 2, r["signals"]
    assert r["signals"]["no_issue"] == 0  # hisse ihraç edildi


def test_bank_like_is_na():
    # Sanayi kalemleri (net_sales, current_assets) yoksa → N/A
    df = pd.DataFrame(
        [
            {"FINANCIAL_ITEM_CODE": "3L", "FINANCIAL_ITEM_NAME_EN": "ni",
             "2024/12": 100, "2025/12": 120},
            {"FINANCIAL_ITEM_CODE": "1BL", "FINANCIAL_ITEM_NAME_EN": "ta",
             "2024/12": 1000, "2025/12": 1100},
        ]
    )
    r = compute_fscore("BANKLIKE", df)
    assert r["f_score"] is None
