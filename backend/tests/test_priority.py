"""İşlem-öncelik katmanı (priority.py) birim testleri — saf fonksiyonlar, DB/ağ yok.

Sabitlenen ilkeler:
- E (beklenen net R) shrinkage harmanı: canlı n büyüdükçe study'nin önemi düşer.
- Study yoksa prior (pead_drift 0.05, diğerleri 0) — uydurma sayı yok.
- Plan ekonomisi simulate_trade_detail maliyet matematiğiyle TUTARLI (aynı eşik).
- E≤0 → priority 0 + "izle"; girilemez plan → "girme" + priority 0.
"""

from __future__ import annotations

import math

from app.engine.priority import (
    ADVICE_RANK,
    advise,
    assemble,
    context_mult,
    expected_net_r,
    news_mult,
    plan_economics,
    priority_score,
)

# event-study blob'unu taklit eden kanıt fixture'ı (gerçek koşum şekliyle birebir)
_EV = {
    "squeeze_breakout": {"n_events": 1181, "mean_R_net": 0.192,
                         "trade_sim": {"mean_R": 0.232, "mean_R_net": 0.192}},
    "snapback": {"n_events": 470, "mean_R_net": -0.007,
                 "trade_sim": {"mean_R": 0.02, "mean_R_net": -0.007}},
    "gross_only": {"n_events": 100, "trade_sim": {"mean_R": 0.10}},  # net yok → brüt fallback
}


# --- expected_net_r -------------------------------------------------------

def test_expected_r_study_only():
    e, src = expected_net_r("squeeze_breakout", _EV, {})
    assert math.isclose(e, 0.192, abs_tol=1e-9)
    assert "event-study net" in src and "n=1181" in src


def test_expected_r_blend_shrinkage():
    # canlı 5 işlem ort -0.5R; k=20 → E = (5·(-0.5) + 20·0.192)/25 = 0.0536
    oos = {"squeeze_breakout": {"n_closed": 5, "ort_r_net": -0.5}}
    e, src = expected_net_r("squeeze_breakout", _EV, oos)
    assert math.isclose(e, (5 * -0.5 + 20 * 0.192) / 25, abs_tol=1e-9)
    assert "canlı n=5" in src


def test_expected_r_oos_dominates_when_n_large():
    # canlı n=200 → study neredeyse etkisiz: E ≈ oos yönünde
    oos = {"squeeze_breakout": {"n_closed": 200, "ort_r_net": -0.2}}
    e, _ = expected_net_r("squeeze_breakout", _EV, oos)
    assert e < 0  # 200·(-0.2)+20·0.192 = -36.16 → negatif


def test_expected_r_prior_fallback_pead():
    e, src = expected_net_r("pead_drift", {}, {})
    assert math.isclose(e, 0.05, abs_tol=1e-9)
    assert "prior" in src


def test_expected_r_prior_default_zero():
    e, _ = expected_net_r("bilinmeyen_setup", {}, {})
    assert e == 0.0


def test_expected_r_gross_fallback_labeled():
    e, src = expected_net_r("gross_only", _EV, {})
    assert math.isclose(e, 0.10, abs_tol=1e-9)
    assert "BRÜT" in src  # dürüstlük: net yoksa brüt olduğu görünür


# --- plan_economics -------------------------------------------------------

def test_plan_economics_basic():
    # entry=100, stop=95, target=110, cost=0.0028 (simulate_trade_detail testiyle aynı senaryo)
    p = plan_economics(100.0, 95.0, 110.0, 0.0028)
    assert p is not None and p["feasible"] is True
    assert math.isclose(p["risk_frac"], 0.05, abs_tol=1e-9)
    assert math.isclose(p["rr"], 2.0, abs_tol=1e-9)
    assert math.isclose(p["cost_r"], 0.056, abs_tol=1e-9)
    assert math.isclose(p["net_target_r"], 1.944, abs_tol=1e-9)     # sim testiyle birebir
    assert math.isclose(p["net_stop_r"], -1.056, abs_tol=1e-9)
    # başabaş isabet: (1+0.056)/(2+1) = 0.352
    assert math.isclose(p["breakeven_hit"], 0.352, abs_tol=1e-3)


def test_plan_economics_infeasible_tight_stop():
    # risk_frac=0.001 <= 0.0028 → girilemez (sim guard'ıyla aynı eşik)
    p = plan_economics(100.0, 99.9, 110.0, 0.0028)
    assert p is not None and p["feasible"] is False


def test_plan_economics_invalid_none():
    assert plan_economics(100.0, 105.0, 110.0, 0.0028) is None   # stop > entry
    assert plan_economics(100.0, 95.0, 99.0, 0.0028) is None     # target < entry
    assert plan_economics(None, 95.0, 110.0, 0.0028) is None


# --- context / news çarpanları -------------------------------------------

def test_context_mult_neutral_is_one():
    assert math.isclose(context_mult(50.0, 50.0), 1.0, abs_tol=1e-9)
    assert math.isclose(context_mult(None, None), 1.0, abs_tol=1e-9)  # bilinmiyor → nötr


def test_context_mult_bounds():
    assert math.isclose(context_mult(100.0, 100.0), 1.15 ** 2, abs_tol=1e-9)
    assert math.isclose(context_mult(0.0, 0.0), 0.85 ** 2, abs_tol=1e-9)


def test_news_mult_positive_only():
    assert news_mult(None) == 1.0
    assert news_mult(-8.0) == 1.0            # negatif haber çarpana girmez (blok scan'de)
    assert math.isclose(news_mult(12.0), 1.12, abs_tol=1e-9)


# --- priority_score -------------------------------------------------------

def test_priority_zero_when_edge_nonpositive():
    assert priority_score(0.0, 90.0, 1.2, 1.1) == 0.0
    assert priority_score(-0.1, 90.0, 1.2, 1.1) == 0.0


def test_priority_full_at_cap():
    # E=cap, güç=100, çarpanlar nötr → 100
    assert math.isclose(priority_score(0.30, 100.0, 1.0, 1.0), 100.0, abs_tol=1e-9)


def test_priority_monotonic_in_strength_and_context():
    lo = priority_score(0.19, 30.0, 1.0, 1.0)
    hi = priority_score(0.19, 90.0, 1.0, 1.0)
    assert hi > lo
    assert priority_score(0.19, 90.0, 1.3, 1.0) > priority_score(0.19, 90.0, 0.75, 1.0)


# --- advise ----------------------------------------------------------------

def test_advise_infeasible_wins():
    plan = {"feasible": False}
    adv, reason = advise(0.5, "event-study net", plan)
    assert adv == "girme" and "maliyet" in reason


def test_advise_positive_edge():
    adv, reason = advise(0.19, "event-study net n=1181", {"feasible": True})
    assert adv == "al-adayı" and "+0.19R" in reason


def test_advise_prior_zero_watch():
    adv, _ = advise(0.0, "prior (event-study yok)", {"feasible": True})
    assert adv == "izle"


def test_advise_measured_negative_watch():
    adv, reason = advise(-0.007, "event-study net n=470", {"feasible": True})
    assert adv == "izle" and "ölü" in reason


def test_advice_rank_order():
    assert ADVICE_RANK["al-adayı"] > ADVICE_RANK["izle"] > ADVICE_RANK["girme"]


# --- assemble (uçtan uca alanlar) ------------------------------------------

def test_assemble_buy_candidate():
    out = assemble(
        setup="squeeze_breakout", strength=70.0, entry=100.0, stop=95.0, target=110.0,
        sector="Sınai", news_pos=5.0, evidence_setups=_EV, oos_per_setup={},
        sector_score_map={"Sınai": 80.0}, regime_score=65.0, tilt_cfg=None,
        round_trip_cost_pct=0.0028, cfg=None,
    )
    assert out["advice"] == "al-adayı"
    assert out["expected_r_net"] == 0.192
    assert out["plan"]["feasible"] is True
    assert out["priority"] > 0
    assert out["context_mult"] > 1.0  # lider sektör + risk-on → nötr üstü


def test_assemble_infeasible_forces_zero_priority():
    out = assemble(
        setup="squeeze_breakout", strength=95.0, entry=100.0, stop=99.9, target=110.0,
        sector=None, news_pos=None, evidence_setups=_EV, oos_per_setup={},
        sector_score_map={}, regime_score=None, tilt_cfg=None,
        round_trip_cost_pct=0.0028, cfg=None,
    )
    assert out["advice"] == "girme"
    assert out["priority"] == 0.0


def test_assemble_negative_edge_watch():
    out = assemble(
        setup="snapback", strength=88.0, entry=100.0, stop=95.0, target=110.0,
        sector=None, news_pos=None, evidence_setups=_EV, oos_per_setup={},
        sector_score_map={}, regime_score=None, tilt_cfg=None,
        round_trip_cost_pct=0.0028, cfg=None,
    )
    assert out["advice"] == "izle"     # ölçülen net beklenti ≤ 0 → işlem olarak ölü
    assert out["priority"] == 0.0      # E≤0 → taban 0
