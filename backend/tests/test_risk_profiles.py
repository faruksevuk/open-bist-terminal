"""Risk profilleri birim testleri — saf matematik + profil sözleşmesi (DB yok).

Sabitlenen ilkeler:
- kayıp-serisi DP'si KESİN (elle sayılabilir küçük durumlarla doğrulanır),
- drawdown bileşik formülü doğru,
- profiller sıralı risk taşır (temkinli < dengeli < agresif) ve sizing'in
  okuduğu anahtarları eksiksiz tanımlar.
"""

from __future__ import annotations

import math

from app.risk.profiles import (
    PROFILES,
    _PROFILE_KEYS,
    profile_math,
    streak_drawdown,
    streak_probability,
)


# --- streak_probability (kesin DP) ---------------------------------------

def test_streak_exact_small_cases():
    # p=0.5, k=2, n=2: yalnız LL → 0.25
    assert math.isclose(streak_probability(0.5, 2, 2), 0.25, abs_tol=1e-12)
    # p=0.5, k=2, n=3: LLL, LLW, WLL → 3/8
    assert math.isclose(streak_probability(0.5, 2, 3), 0.375, abs_tol=1e-12)
    # k > n → imkânsız
    assert streak_probability(0.5, 5, 3) == 0.0
    # p=1 → n>=k ise kesin
    assert math.isclose(streak_probability(1.0, 4, 4), 1.0, abs_tol=1e-12)
    # p=0 → hiç kayıp yok
    assert streak_probability(0.0, 2, 50) == 0.0


def test_streak_monotonicity():
    # daha uzun pencere → olasılık artar; daha uzun seri şartı → azalır
    p1 = streak_probability(0.55, 6, 30)
    p2 = streak_probability(0.55, 6, 100)
    assert p2 > p1
    assert streak_probability(0.55, 8, 50) < streak_probability(0.55, 5, 50)
    # olasılık [0,1] bandında
    assert 0.0 <= p1 <= 1.0 and 0.0 <= p2 <= 1.0


def test_streak_realistic_magnitude():
    """%45 isabet (p_loss=0.55), 50 işlem: 6'lı seri OLDUKÇA muhtemel olmalı (>%30) —
    kullanıcının 'agresif' seçmeden görmesi gereken gerçek bu."""
    p = streak_probability(0.55, 6, 50)
    assert 0.30 < p < 0.85


# --- drawdown --------------------------------------------------------------

def test_streak_drawdown_compound():
    # %2 risk, 8 seri kayıp → 1 - 0.98^8 ≈ %14.9
    assert math.isclose(streak_drawdown(0.02, 8), 1 - 0.98 ** 8, abs_tol=1e-12)
    # %0.5 risk aynı seride çok daha sığ
    assert streak_drawdown(0.005, 8) < streak_drawdown(0.02, 8) / 3


# --- profil sözleşmesi ------------------------------------------------------

def test_profiles_ordered_and_complete():
    assert set(PROFILES) == {"temkinli", "dengeli", "agresif"}
    for p in PROFILES.values():
        for k in _PROFILE_KEYS:
            assert k in p
    assert (PROFILES["temkinli"]["base_r"] < PROFILES["dengeli"]["base_r"]
            < PROFILES["agresif"]["base_r"])
    assert (PROFILES["temkinli"]["max_heat_pct"] < PROFILES["dengeli"]["max_heat_pct"]
            < PROFILES["agresif"]["max_heat_pct"])
    # agresif bile tek işlemde %2'yi aşmaz (küçük hesapta toparlanamaz bölge >%2-3)
    assert PROFILES["agresif"]["base_r"] <= 0.02


def test_dengeli_matches_legacy_seed():
    """'dengeli' mevcut seed 'risk' değerleriyle birebir — profil sistemi davranışı
    değiştirmeden devreye girer (varsayılan aktif profil dengeli)."""
    assert PROFILES["dengeli"]["base_r"] == 0.01
    assert PROFILES["dengeli"]["max_heat_pct"] == 0.06
    assert PROFILES["dengeli"]["daily_stop_pct"] == 0.03
    assert PROFILES["dengeli"]["weekly_dd_pct"] == 0.10


def test_profile_math_shape():
    m = profile_math(0.02, hit_rate=0.45, n_trades=50)
    assert m["risk_per_trade"] == 0.02
    assert set(m["streaks"]) == {"4", "6", "8"}
    for s in m["streaks"].values():
        assert 0.0 <= s["p_streak"] <= 1.0
        assert 0.0 < s["drawdown"] < 1.0
