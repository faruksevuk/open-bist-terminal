"""Setup sinyal API'si — kısa-vade işlem katmanı (SETUPS v0.1 + öncelik katmanı v0.1).

GET /api/setups           → aktif & süresi geçmemiş sinyaller + piyasa bağlamı + kanıt
                            + İŞLEM-ÖNCELİK alanları (expected_r_net / plan / advice).
GET /api/setups/evidence  → tam setup_evidence config blob'u (UI kanıt paneli).

Yalnız active & unexpired & verdict != "devre dışı" (?include_all=true ile hepsi).
Sıralama: advice rütbesi (al-adayı > izle > girme), sonra priority, sonra strength.
Öncelik formülü ÖN-KAYITLI (app/engine/priority.py) — ölçülen net beklenti (event-study
+ canlı OOS shrinkage) × bağlam tilt'i × haber çarpanı; edge üretmez, ölçüleni harmanlar.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.event_study import round_trip_cost_pct
from app.config_store import get_config
from app.db.base import get_session
from app.db.models import Horizon, Score, Security, SetupOutcome, SetupSignal
from app.engine import priority as prio
from app.engine.setup_outcomes import outcome_summary
from app.engine.setups import SETUP_LABELS
from app.news.events import news_map

router = APIRouter(prefix="/api", tags=["setups"])

# verdict sıralama rütbesi (yüksek = önce)
_VERDICT_RANK = {"kanıtlı": 3, "zayıf": 2, "deneysel": 1, "devre dışı": 0}
# event-study edilemeyen setuplar için sabit kanıt statüsü
_FIXED_VERDICT = {
    "pead_drift": "deneysel",
    "quiet_accumulation": "deneysel",  # başlangıç statüsü (event-study üzerine yazabilir)
}


def _evidence_for(setup: str, ev_cfg: dict) -> dict:
    """setup_evidence config'ten bir setup'ın kanıt özeti (yoksa sabit/deneysel)."""
    setups = (ev_cfg or {}).get("setups", {})
    s = setups.get(setup)
    if s and s.get("n_events", 0) > 0:
        e5 = (s.get("excess") or {}).get("5", {})
        return {
            "verdict": s.get("verdict", "deneysel"),
            "n_events": s.get("n_events"),
            "hit_rate": s.get("hit_rate_5d"),
            "mean_excess_5d": s.get("mean_excess_5d"),
            "t": s.get("t_newey_west_5d"),
            "profit_factor": s.get("profit_factor"),
            "ci95_5d": [e5.get("ci95_low"), e5.get("ci95_high")],
        }
    # pead_drift özel blok
    if setup == "pead_drift" and (ev_cfg or {}).get("pead_drift"):
        pd_ev = ev_cfg["pead_drift"]
        return {"verdict": pd_ev.get("verdict", "deneysel"), "n_events": None,
                "hit_rate": None, "mean_excess_5d": None, "t": None,
                "profit_factor": None, "status": pd_ev.get("status")}
    return {"verdict": _FIXED_VERDICT.get(setup, "deneysel"), "n_events": None,
            "hit_rate": None, "mean_excess_5d": None, "t": None, "profit_factor": None}


def _latest_swing_scores(session: Session) -> dict[str, float]:
    """En güncel swing Score.score (ticker → score) — sinyale bağlam olarak eklenir."""
    as_of = session.execute(
        select(Score.as_of).where(Score.horizon == Horizon.swing)
        .order_by(Score.as_of.desc()).limit(1)
    ).scalar()
    if as_of is None:
        return {}
    rows = session.execute(
        select(Score.ticker, Score.score).where(
            Score.horizon == Horizon.swing, Score.as_of == as_of)
    ).all()
    return {t: sc for t, sc in rows}


@router.get("/setups")
def setups(include_all: bool = False, session: Session = Depends(get_session)) -> dict:
    """Aktif setup sinyalleri + piyasa bağlamı + kanıt + işlem-öncelik alanları."""
    ev_cfg = get_config(session, "setup_evidence") or {}
    mkt_cfg = get_config(session, "setup_market") or {}
    ctx_cfg = get_config(session, "market_context") or {}
    macro = ctx_cfg.get("macro") or {}
    market = {
        "mkt_ret_5d": mkt_cfg.get("mkt_ret_5d"),
        "mkt_above_ema50": mkt_cfg.get("mkt_above_ema50"),
        "breadth": mkt_cfg.get("breadth"),
        # üst-aşağı rejim (sector_macro derlemesi) — Today paneli tek çağrıyla kurulsun
        "regime": macro.get("regime"),
        "regime_score": macro.get("regime_score"),
    }

    # --- öncelik girdileri (bir kez) ---
    prio_cfg = get_config(session, "priority")
    rtc = round_trip_cost_pct(session)
    oos_per_setup = outcome_summary(session)["per_setup"]
    evidence_setups = ev_cfg.get("setups", {}) or {}
    sector_score_map = ctx_cfg.get("sector_score") or {}
    regime_score = macro.get("regime_score")
    tilt_cfg = ctx_cfg.get("tilt_cfg")
    nm = news_map(session)

    today = date.today()
    stmt = (
        select(SetupSignal, Security.sector)
        .join(Security, Security.ticker == SetupSignal.ticker)
        .where(SetupSignal.active.is_(True))
    )
    rows = session.execute(stmt).all()
    scores = _latest_swing_scores(session)

    items: list[dict] = []
    as_of = None
    for sig, sector in rows:
        # süresi geçmiş sinyalleri gizle (deaktive edilmemiş olabilir)
        if sig.valid_until is not None and sig.valid_until < today:
            continue
        ev = _evidence_for(sig.setup, ev_cfg)
        if ev["verdict"] == "devre dışı" and not include_all:
            continue
        if as_of is None or (sig.triggered_at and sig.triggered_at > as_of):
            as_of = sig.triggered_at
        r_multiple = None
        if sig.entry_ref and sig.stop and (sig.entry_ref - sig.stop) > 0:
            r_multiple = round((sig.target - sig.entry_ref) / (sig.entry_ref - sig.stop), 2)
        pr = prio.assemble(
            setup=sig.setup, strength=sig.strength, entry=sig.entry_ref, stop=sig.stop,
            target=sig.target, sector=sector, news_pos=nm.get(sig.ticker, (0.0, 0.0))[0],
            evidence_setups=evidence_setups, oos_per_setup=oos_per_setup,
            sector_score_map=sector_score_map, regime_score=regime_score, tilt_cfg=tilt_cfg,
            round_trip_cost_pct=rtc, cfg=prio_cfg,
        )
        items.append({
            "ticker": sig.ticker,
            "sector": sector,
            "setup": sig.setup,
            "setup_label": SETUP_LABELS.get(sig.setup, sig.setup),
            "strength": sig.strength,
            "entry_ref": sig.entry_ref,
            "stop": sig.stop,
            "target": sig.target,
            "r_multiple": r_multiple,
            "time_exit_days": sig.time_exit_days,
            "triggered_at": sig.triggered_at.isoformat() if sig.triggered_at else None,
            "valid_until": sig.valid_until.isoformat() if sig.valid_until else None,
            "evidence": ev,
            "score": scores.get(sig.ticker),
            "context": sig.context,
            **pr,
        })

    # işlem-öncelik sırası: tavsiye rütbesi → priority → strength (verdict artık
    # evidence içinde görünür bilgi; sıralamayı ölçülen beklenti yönetir)
    items.sort(key=lambda x: (
        prio.ADVICE_RANK.get(x["advice"], 0), x["priority"] or 0.0, x["strength"] or 0.0),
        reverse=True)

    return {
        "as_of": as_of.isoformat() if as_of else None,
        "market": market,
        "round_trip_cost_pct": round(rtc, 6),
        "count": len(items),
        "setups": items,
    }


@router.get("/setups/evidence")
def setups_evidence(session: Session = Depends(get_session)) -> dict:
    """Tam setup_evidence config blob'u (UI kanıt paneli)."""
    return get_config(session, "setup_evidence") or {"note": "henüz event-study koşulmadı"}


@router.get("/strategies")
def strategies(session: Session = Depends(get_session)) -> dict:
    """Strateji karnesi — her stratejinin kanıt (event-study) × canlı (OOS) × harman durumu.

    Tek çağrıda 'hangi strateji işliyor, hangisi ölü' sorusunun sunucu-hesaplı cevabı
    (§14.3: hesap frontend'e taşınmaz). expected_r_net = priority ile AYNI shrinkage
    harmanı → dashboard sıralamasıyla çelişmez.
    """
    ev_cfg = get_config(session, "setup_evidence") or {}
    evidence_setups = ev_cfg.get("setups", {}) or {}
    oos = outcome_summary(session)["per_setup"]
    prio_cfg = get_config(session, "priority")

    keys = sorted(set(SETUP_LABELS) | set(evidence_setups) | set(oos))
    rows: list[dict] = []
    for key in keys:
        ev = _evidence_for(key, ev_cfg)
        s = evidence_setups.get(key) or {}
        trade = s.get("trade_sim") or {}
        live = oos.get(key) or {}
        e_net, e_src = prio.expected_net_r(key, evidence_setups, oos, prio_cfg)
        if ev["verdict"] == "devre dışı":
            status = "devre dışı"
        elif e_net > 0:
            status = "işlemde"
        else:
            status = "izle"
        rows.append({
            "setup": key,
            "label": SETUP_LABELS.get(key, key),
            "verdict": ev["verdict"],
            "status": status,
            "expected_r_net": round(e_net, 3),
            "expected_r_src": e_src,
            "study": {
                "n": s.get("n_events"),
                "n_days": s.get("n_days"),   # bağımsız gün sayısı — kümelenme/kırılganlık işareti
                "mean_r_net": s.get("mean_R_net") or trade.get("mean_R_net"),
                "pf_net": s.get("profit_factor_net") or trade.get("profit_factor_net"),
                "hit_rate": trade.get("hit_rate_R"),
                "by_regime": s.get("by_regime"),
            },
            "live": {
                "n_closed": live.get("n_closed", 0),
                "n_pending": live.get("n_pending", 0),
                "mean_r_net": live.get("ort_r_net"),
                "hit_rate": live.get("isabet_net") if live.get("isabet_net") is not None else live.get("isabet"),
            },
        })

    # işlemde olanlar önce, sonra beklentiye göre
    _SRANK = {"işlemde": 2, "izle": 1, "devre dışı": 0}
    rows.sort(key=lambda r: (_SRANK.get(r["status"], 0), r["expected_r_net"]), reverse=True)
    return {
        "as_of": ev_cfg.get("as_of"),
        "round_trip_cost_pct": ev_cfg.get("round_trip_cost_pct"),
        "count": len(rows),
        "strategies": rows,
        "note": ("expected_r_net = event-study net R ile canlı OOS'un shrinkage harmanı "
                 "(öncelik katmanıyla aynı formül). 'işlemde' = ölçülen net beklenti > 0."),
    }


@router.get("/setups/outcomes")
def setups_outcomes(session: Session = Depends(get_session)) -> dict:
    """Canlı sinyal sonuç-takibi (OOS) — setup başına + genel + dürüst beklenti + son 50 kapalı.

    outcome_summary'yi çağırır (SALT-OKUR; değerlendirme run_scoring/evaluate_outcomes'da yapılır).
    """
    summ = outcome_summary(session)

    # son 50 KAPALI sonuç (exit_date desc; None'lar en sona)
    closed_stmt = (
        select(SetupOutcome)
        .where(SetupOutcome.status.in_(("target", "stop", "time_exit")))
        .order_by(SetupOutcome.exit_date.desc().nullslast(), SetupOutcome.id.desc())
        .limit(50)
    )
    closed = session.execute(closed_stmt).scalars().all()
    outcomes = [
        {
            "ticker": o.ticker,
            "setup": o.setup,
            "setup_label": SETUP_LABELS.get(o.setup, o.setup),
            "status": o.status,
            "realized_r": round(o.realized_r, 3) if o.realized_r is not None else None,
            "realized_pct": round(o.realized_pct, 4) if o.realized_pct is not None else None,
            "days_held": o.days_held,
            "triggered_at": o.triggered_at.isoformat() if o.triggered_at else None,
            "entry_date": o.entry_date.isoformat() if o.entry_date else None,
            "exit_date": o.exit_date.isoformat() if o.exit_date else None,
        }
        for o in closed
    ]

    e = summ["expectancy"]
    return {
        "as_of": summ["as_of"],
        "cost_note": summ.get("cost_note"),
        "round_trip_cost_pct": summ.get("round_trip_cost_pct"),
        "per_setup": summ["per_setup"],
        "overall": summ["overall"],
        "expectancy": {
            "risk_per_trade": e["risk_per_trade"],
            "measured_r_per_week": e["measured_r_per_week"],
            "measured_r_per_week_net": e["measured_r_per_week_net"],
            "expected_weekly_pct": e["expected_weekly_pct"],
            "expected_weekly_pct_net": e["expected_weekly_pct_net"],
            "target_weekly_pct": e["target_weekly_pct"],
            "needed_r_per_week": e["needed_r_per_week"],
            "needed_ignores_cost": e["needed_ignores_cost"],
            "gap_note": e["gap_note"],
        },
        "outcomes": outcomes,
    }
