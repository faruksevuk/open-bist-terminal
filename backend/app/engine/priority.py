"""İşlem-öncelik katmanı (v0.1) — "bugün hangi sinyal, girmeye değer mi, ne bekleyebilirim?"

NEDEN: /api/setups sıralaması verdict+strength idi. Oysa elimizde ÖLÇÜLMÜŞ üç bilgi var
ve karara girmiyordu: (1) event-study NET işlem beklentisi (trade-sim mean_R_net),
(2) canlı OOS sonuçları (setup_outcomes), (3) bağlam (rejim/sektör tilt'i — sector_macro'da
hesaplanıp HİÇBİR yerde kullanılmıyordu). Bu katman üçünü tek şeffaf sayıda (priority)
birleştirir, işlem-planı ekonomisini (maliyet sonrası hedef R, başabaş isabet) çıkarır ve
Türkçe tavsiye etiketi üretir: "al-adayı" / "izle" / "girme".

DÜRÜSTLÜK: bu katman edge ÜRETMEZ, ölçülenleri harmanlar. Formül ÖN-KAYITLIDIR (parametre
araması yok, §9.5):

  expected_r_net (E) = shrinkage harmanı:
      E = (n_oos·oos_r + k·study_r) / (n_oos + k)
      study_r = event-study trade-sim mean_R_net (yoksa gross mean_R, o da yoksa
                setup-başına config prior'ı; pead_drift 0.05R research-prior, diğerleri 0).
      k = prior_weight_k (varsayılan 20) — canlı sonuç biriktikçe study'nin yerini alır.

  plan ekonomisi (sinyal-başına, maliyet DAHİL):
      risk_frac  = (entry−stop)/entry
      cost_r     = round_trip / risk_frac          (maliyetin R cinsinden bedeli)
      net_target_r = rr_plan − cost_r              (hedef vurulursa net kazanç)
      breakeven_hit = (1+cost_r)/(rr_plan+1)       (başabaş isabet oranı)
      risk_frac ≤ round_trip → İŞLEM GİRİLEMEZ (simulate_trade_detail guard'ıyla birebir).

  priority (0-100) = taban(E) × güç karışımı × bağlam çarpanı × haber çarpanı
      taban(E)   = 100·clip(E, 0, e_cap_r)/e_cap_r   (E≤0 → 0: negatif edge sıralamada dibe)
      güç        = (1−w_strength) + w_strength·strength/100
      bağlam     = (base+span·sektör/100)(base+span·rejim/100)   [sector_macro tilt çarpanı]
      haber      = 1 + clip(news_pos,0,∞)/news_div   (poz. KAP katalisti hafif öne alır)

Tavsiye: girilemezse "girme"; E>0 ise "al-adayı"; aksi "izle" (sebep metniyle).
"""

from __future__ import annotations

from typing import Any

# Varsayılanlar (config 'priority' yoksa) — POLICY/PRIOR, arama yapılmadı.
_DEF_PRIORITY: dict = {
    "prior_weight_k": 20.0,   # study/prior sözde-n (shrinkage); canlı n büyüdükçe önemi düşer
    # study'siz setup net-R prior'ı (research). htf = kanıtlı squeeze analojisi (analoji primi
    # 0.05); pead earnings-drift research-prior; kalan v2 prior-only dedektörler 0 (gölgeden
    # başlar, canlı OOS/event-study net R>0 kanıtlayınca yükselir — E≤0 → "izle").
    "prior_r": {"pead_drift": 0.05, "htf_squeeze_breakout": 0.05, "_default": 0.0},
    "e_cap_r": 0.30,          # E→taban ölçek tavanı (0.30R ve üstü = 100 taban)
    "w_strength": 0.40,       # sinyal gücünün taban üzerindeki payı
    "news_div": 100.0,        # haber çarpanı bölücüsü (pos_cap=12 → en çok ×1.12)
}

ADVICE_RANK = {"al-adayı": 2, "izle": 1, "girme": 0}


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def expected_net_r(setup: str, evidence_setups: dict, oos_per_setup: dict,
                   cfg: dict | None = None) -> tuple[float, str]:
    """Setup'ın beklenen NET R'si (işlem başına) + kaynak açıklaması.

    Öncelik: event-study mean_R_net → (yoksa) gross mean_R → (o da yoksa) config prior.
    Canlı OOS varsa shrinkage ile harmanlanır: (n·oos + k·study)/(n+k).
    """
    p = cfg or _DEF_PRIORITY
    k = float(p.get("prior_weight_k", _DEF_PRIORITY["prior_weight_k"]))
    priors = p.get("prior_r", _DEF_PRIORITY["prior_r"])

    ev = (evidence_setups or {}).get(setup) or {}
    ts = ev.get("trade_sim") or {}
    study_r = _f(ev.get("mean_R_net"))
    if study_r is None:
        study_r = _f(ts.get("mean_R_net"))
    src_study = "event-study net"
    if study_r is None:
        study_r = _f(ts.get("mean_R"))
        src_study = "event-study BRÜT (net yok)"
    if study_r is None:
        study_r = float(priors.get(setup, priors.get("_default", 0.0)))
        src_study = "prior (event-study yok)"
    n_study = int(ev.get("n_events") or 0)

    oos = (oos_per_setup or {}).get(setup) or {}
    n_oos = int(oos.get("n_closed") or 0)
    oos_r = _f(oos.get("ort_r_net"))
    if oos_r is None:
        oos_r = _f(oos.get("ort_r"))

    if n_oos > 0 and oos_r is not None:
        e = (n_oos * oos_r + k * study_r) / (n_oos + k)
        src = f"{src_study}{f' n={n_study}' if n_study else ''} + canlı n={n_oos} (k={k:g})"
    else:
        e = study_r
        src = f"{src_study}{f' n={n_study}' if n_study else ''}"
    return float(e), src


def plan_economics(entry: float | None, stop: float | None, target: float | None,
                   round_trip_cost_pct: float) -> dict | None:
    """Sinyalin işlem-planı ekonomisi (maliyet dahil). Geçersiz plan → None.

    feasible=False → risk_frac ≤ maliyet: 1R risk bütçesini komisyon+spread yer
    (simulate_trade_detail'in maliyet-fizibilite guard'ıyla AYNI eşik, tuning yok).
    """
    e, s, t = _f(entry), _f(stop), _f(target)
    if e is None or s is None or t is None or e <= 0:
        return None
    risk = e - s
    if risk <= 0 or t <= e:
        return None
    risk_frac = risk / e
    rr = (t - e) / risk
    cost = max(0.0, float(round_trip_cost_pct))
    feasible = cost <= 0.0 or risk_frac > cost
    cost_r = (cost / risk_frac) if risk_frac > 0 else float("inf")
    return {
        "risk_frac": round(risk_frac, 4),
        "rr": round(rr, 2),
        "cost_r": round(cost_r, 3),
        "net_target_r": round(rr - cost_r, 3),
        "net_stop_r": round(-1.0 - cost_r, 3),
        # başabaş isabet: h·(rr−c) + (1−h)·(−1−c) = 0 → h* = (1+c)/(rr+1)
        "breakeven_hit": round((1.0 + cost_r) / (rr + 1.0), 3),
        "feasible": bool(feasible),
    }


def context_mult(sector_score: float | None, regime_score: float | None,
                 tilt_cfg: dict | None = None) -> float:
    """sector_macro.context_tilt'in ÇARPAN kısmı (strength'e uygulanmadan) — tek kaynak
    formül: (base+span·sektör/100)(base+span·rejim/100), bounded ~[0.72, 1.32]."""
    t = tilt_cfg or {"base": 0.85, "span": 0.30}
    base, span = float(t.get("base", 0.85)), float(t.get("span", 0.30))
    sec = 50.0 if sector_score is None else float(sector_score)
    reg = 50.0 if regime_score is None else float(regime_score)
    return (base + span * sec / 100.0) * (base + span * reg / 100.0)


def news_mult(news_pos: float | None, cfg: dict | None = None) -> float:
    p = cfg or _DEF_PRIORITY
    div = float(p.get("news_div", _DEF_PRIORITY["news_div"]))
    np_ = max(0.0, _f(news_pos) or 0.0)
    return 1.0 + np_ / div


def priority_score(e_net: float, strength: float | None, ctx_mult: float,
                   nws_mult: float, cfg: dict | None = None) -> float:
    """0-100 öncelik. E≤0 → 0 (negatif/sıfır edge sıralamada dibe; 'izle')."""
    p = cfg or _DEF_PRIORITY
    e_cap = float(p.get("e_cap_r", _DEF_PRIORITY["e_cap_r"]))
    w = float(p.get("w_strength", _DEF_PRIORITY["w_strength"]))
    base = 100.0 * min(max(e_net, 0.0), e_cap) / e_cap if e_cap > 0 else 0.0
    s = 0.0 if strength is None else min(max(float(strength), 0.0), 100.0)
    mix = (1.0 - w) + w * s / 100.0
    return float(min(max(base * mix * ctx_mult * nws_mult, 0.0), 100.0))


def advise(e_net: float, e_src: str, plan: dict | None) -> tuple[str, str]:
    """(advice, reason) — Türkçe, tek cümle. Sıra: girilemez > al-adayı > izle."""
    if plan is not None and not plan.get("feasible", True):
        return ("girme", "Stop çok dar: round-trip maliyet 1R risk bütçesini yiyor — "
                         "bu plan ekonomik olarak girilemez.")
    if plan is None:
        return ("izle", "Plan (giriş/stop/hedef) eksik ya da geçersiz.")
    if e_net > 0:
        return ("al-adayı", f"Ölçülen net beklenti +{e_net:.2f}R/işlem ({e_src}).")
    if "prior" in e_src:
        return ("izle", "Ölçülmüş edge yok (deneysel/prior) — kanıt birikene dek küçük kal ya da izle.")
    return ("izle", f"Ölçülen net beklenti ≤ 0 ({e_net:.2f}R, {e_src}) — işlem olarak ölü, izle.")


def assemble(*, setup: str, strength: float | None, entry: float | None, stop: float | None,
             target: float | None, sector: str | None, news_pos: float | None,
             evidence_setups: dict, oos_per_setup: dict, sector_score_map: dict,
             regime_score: float | None, tilt_cfg: dict | None,
             round_trip_cost_pct: float, cfg: dict | None = None) -> dict:
    """Bir sinyalin tüm öncelik alanlarını üret (API item'ına merge edilir)."""
    e_net, e_src = expected_net_r(setup, evidence_setups, oos_per_setup, cfg)
    plan = plan_economics(entry, stop, target, round_trip_cost_pct)
    sec_score = (sector_score_map or {}).get(sector) if sector else None
    cm = context_mult(sec_score, regime_score, tilt_cfg)
    nm = news_mult(news_pos, cfg)
    pr = priority_score(e_net, strength, cm, nm, cfg)
    adv, reason = advise(e_net, e_src, plan)
    if adv == "girme":
        pr = 0.0
    return {
        "expected_r_net": round(e_net, 3),
        "expected_r_src": e_src,
        "plan": plan,
        "context_mult": round(cm, 3),
        "news_mult": round(nm, 3),
        "news_pos": round(_f(news_pos) or 0.0, 1),
        "priority": round(pr, 1),
        "advice": adv,
        "advice_reason": reason,
    }
