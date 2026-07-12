"""Evren kapıları (sert) — SCORING v0.2 §2. Geçemeyen skorlanır ama aday olamaz.

Not (M4): limit-lock kapısı canlı Midas snapshot gerektirir (ertelendi); şimdilik
atlanır ve 'limit_lock_unknown' notu düşülür. Diğer kapılar günlük barla çalışır.
"""

from __future__ import annotations

import pandas as pd


def apply_gates(df: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """df'e passed_gates (bool) + gate_reasons (list) ekler."""
    min_liq = thresholds.get("min_liq_tl", 50_000_000)
    min_move = thresholds.get("min_move_atr_pct", 0.005)  # seed ile aynı (0.025 eski; edge taşıyan düşük-vol'ü eliyordu)
    min_f = thresholds.get("min_fscore", 5)
    min_bars_full = 200

    reasons: list[list[str]] = []
    passed: list[bool] = []
    for _, r in df.iterrows():
        fail: list[str] = []
        if r.get("excluded"):
            fail.append("tedbir/işlem yasağı")
        if not r.get("avg_tl_vol_20") or r["avg_tl_vol_20"] < min_liq:
            fail.append("likidite<eşik")
        if not r.get("bars") or r["bars"] < min_bars_full:
            fail.append("yetersiz geçmiş (<200)")
        if r.get("atr_pct") is None or r["atr_pct"] < min_move:
            fail.append("hareket<taban (ölü-sakin)")  # M tabanı
        f = r.get("f_score")
        if f is not None and f < min_f:  # banka (None) kaliteden geçer, farklı muamele
            fail.append(f"F-Score<{min_f}")
        reasons.append(fail)
        passed.append(len(fail) == 0)

    out = df.copy()
    out["passed_gates"] = passed
    out["gate_reasons"] = reasons
    return out
