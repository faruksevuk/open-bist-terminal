"""Çıkış-politikası karşılaştırması (exit study) — SETUPS çıkış yönetimi doğrulaması.

NEDEN: retail kısa-vade edge'inin çoğu kötü ÇIKIŞTA kaybolur. Setup GİRİŞ sinyalleri sabitken
(squeeze ailesi), çıkış politikasını değiştirip AYNI girişlerde net PF/ort-R karşılaştırır —
farkı yalnız çıkış yaratır. §9.5 disiplini: politika parametreleri ÖN-KAYITLI (config 'exits'),
tek koşum, hepsi raporlanır; "en iyisini seç + kanıt de" YAPILMAZ (snooping).

Adil karşılaştırma: bir tetik ancak 3 politikada da KAPANMIŞ (net R var) ise dahil edilir →
politika başına n BİREBİR aynı (apples-to-apples). Maliyet: fixed/trail 1× round-trip,
partial_be 1.5× (2 satış) — simulate_trade_detail içinde.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np

from app.backtest.event_study import (
    _MAX_H,
    _WARMUP,
    _build_panels,
    _cross_roc3,
    _cross_rs15,
    _market_series,
    round_trip_cost_pct,
    simulate_trade_detail,
)
from app.config_store import get_config, set_config
from app.engine.setups import _DEF_SETUPS, EVENT_STUDY_DETECTORS, MarketContext, SETUP_LABELS

log = logging.getLogger(__name__)

_DEF_POLICIES = {
    "fixed": {},
    "trail": {"trail_mult": 2.0},
    "partial_be": {"first_r": 1.0, "scale_frac": 0.5},
}


def _agg(rs: list[float], rs_net: list[float], days: list[int]) -> dict:
    if not rs_net:
        return {"n": 0}
    arr = np.array(rs_net, dtype=float)
    wins, losses = arr[arr > 0], arr[arr <= 0]
    pf = (float(wins.sum()) / abs(float(losses.sum()))) if len(losses) and losses.sum() != 0 else None
    return {
        "n": int(len(arr)),
        "mean_R": round(float(np.mean(rs)), 3),
        "mean_R_net": round(float(arr.mean()), 3),
        "pf_net": round(pf, 3) if pf is not None else None,
        "hit_net": round(float((arr > 0).mean()), 3),
        "avg_win": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else None,
        "avg_days": round(float(np.mean(days)), 1) if days else None,
    }


def run_exit_study(session, setups: tuple[str, ...] = ("squeeze_breakout", "htf_squeeze_breakout"),
                   policies: dict | None = None, write: bool = True) -> dict:
    """Squeeze ailesi girişlerinde çıkış politikalarını karşılaştır. write→config 'exit_study'."""
    setups_cfg = get_config(session, "setups") or _DEF_SETUPS
    exits_cfg = get_config(session, "exits") or {}
    pols = policies or {
        "fixed": {},
        "trail": exits_cfg.get("trail", _DEF_POLICIES["trail"]),
        "partial_be": exits_cfg.get("partial_be", _DEF_POLICIES["partial_be"]),
    }
    cost_pct = round_trip_cost_pct(session)
    panels = _build_panels(session)
    if not panels:
        return {"note": "panel yok (daily_bars boş?)", "n_tickers": 0}

    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    mkt = _market_series(panels, common)
    roc3x = _cross_roc3(panels, common, setups_cfg.get("snapback", {}).get("roc3_decile", 0.10))
    rs_pctile = setups_cfg.get("rs_shield", {}).get("rs_pctile_min", 90) / 100.0
    rs15x = _cross_rs15(panels, common, mkt["ret15"], rs_pctile)
    mkt_ret_series = mkt["ret"]

    m_ret5 = mkt["ret5"].to_numpy(dtype=float)
    m_ret15 = mkt["ret15"].to_numpy(dtype=float)
    m_day = mkt["ret"].to_numpy(dtype=float)
    m_above = mkt["above_ema50"].to_numpy()
    m_breadth = mkt["breadth"].to_numpy(dtype=float)
    x_p10 = roc3x["roc3_p10"].to_numpy(dtype=float)
    x_min = roc3x["roc3_min"].to_numpy(dtype=float)
    x_rs15 = rs15x.to_numpy(dtype=float)

    detectors = {name: EVENT_STUDY_DETECTORS[name] for name in setups if name in EVENT_STUDY_DETECTORS}
    # setup → policy → (gross_r, net_r, days) listeleri
    data: dict[str, dict[str, list]] = {name: {p: [] for p in pols} for name in detectors}
    n = len(common)

    for _ticker, panel in panels.items():
        pan = panel.reindex(common)
        o, h, low, c = pan["open"], pan["high"], pan["low"], pan["close"]
        stock_ret = pan["close"].pct_change()
        last_trig = {name: -10**9 for name in detectors}
        valid_close = pan["close"].notna().to_numpy()
        for i in range(_WARMUP, n - _MAX_H - 1):
            if not valid_close[i]:
                continue
            ctx = MarketContext(
                mkt_ret_5d=float(m_ret5[i]) if not np.isnan(m_ret5[i]) else 0.0,
                mkt_ret_15d=float(m_ret15[i]) if not np.isnan(m_ret15[i]) else 0.0,
                mkt_day_ret=float(m_day[i]) if not np.isnan(m_day[i]) else 0.0,
                mkt_above_ema50=bool(m_above[i]),
                breadth=float(m_breadth[i]) if not np.isnan(m_breadth[i]) else 0.5,
                cross={
                    "roc3_p10": float(x_p10[i]) if not np.isnan(x_p10[i]) else None,
                    "roc3_min": float(x_min[i]) if not np.isnan(x_min[i]) else None,
                    "rs15_p90": float(x_rs15[i]) if not np.isnan(x_rs15[i]) else None,
                    "mkt_ret_series": mkt_ret_series, "stock_ret": stock_ret,
                },
            )
            for name, detector in detectors.items():
                if i - last_trig[name] < _MAX_H:
                    continue
                res = detector(pan, i, ctx, setups_cfg)
                if res is None:
                    continue
                entry_j = i + 1
                if entry_j >= n:
                    continue
                # 3 politikayı da simüle et; YALNIZ hepsi kapalıysa dahil et (adil n)
                sims = {}
                ok = True
                for pname, pcfg in pols.items():
                    d = simulate_trade_detail(o, h, low, c, entry_j, res["stop"], res["target"],
                                              res["time_exit_days"], round_trip_cost_pct=cost_pct,
                                              exit_policy=pname, exit_cfg=pcfg)
                    if d.r_multiple_net is None:  # pending / girilemez → tetiği tamamen atla
                        ok = False
                        break
                    sims[pname] = (d.r_multiple, d.r_multiple_net, d.days_held)
                if ok:
                    for pname, (gr, nr, dd) in sims.items():
                        data[name][pname].append((gr, nr, dd or 0))
                    last_trig[name] = i

    # toplama + karşılaştırma
    out: dict[str, dict] = {}
    for name, per_pol in data.items():
        pol_stats = {}
        for pname, rows in per_pol.items():
            rs = [r[0] for r in rows]
            rs_net = [r[1] for r in rows]
            days = [r[2] for r in rows]
            pol_stats[pname] = _agg(rs, rs_net, days)
        fixed = pol_stats.get("fixed", {})
        base_r = fixed.get("mean_R_net")
        # en iyi net ort-R (fixed'e göre delta)
        ranked = sorted(
            [(p, s) for p, s in pol_stats.items() if s.get("n")],
            key=lambda x: (x[1].get("mean_R_net") or -9), reverse=True)
        best = ranked[0][0] if ranked else "fixed"
        out[name] = {
            "label": SETUP_LABELS.get(name, name),
            "n": fixed.get("n", 0),
            "policies": pol_stats,
            "best_by_mean_R_net": best,
            "delta_vs_fixed": {
                p: round((s.get("mean_R_net") or 0) - (base_r or 0), 3)
                for p, s in pol_stats.items() if s.get("n")
            },
        }

    result = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "n_tickers": len(panels),
        "round_trip_cost_pct": round(cost_pct, 6),
        "policies_echo": pols,
        "setups": out,
        "methodology": ("Aynı giriş sinyalleri (squeeze ailesi), çıkış politikası değişkeni. "
                        "Bir tetik ancak 3 politikada da kapalıysa dahil (adil n). Net = maliyet "
                        "sonrası (fixed/trail 1×, partial_be 1.5× round-trip). ÖN-KAYITLI param, "
                        "tek koşum (§9.5) — 'en iyi'yi seçip kanıt DEME (snooping). Tek rejim caveat."),
    }
    if write:
        set_config(session, "exit_study", result)
    return result
