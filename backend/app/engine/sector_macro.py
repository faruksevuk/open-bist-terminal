"""Sektör & Makro bağlam katmanı — üst-aşağı (top-down) skorlama (v0.1).

NE İŞE YARAR: setups (aşağı-yukarı, olay-tetikli) + faktör skoru DÜRÜST ama BAĞLAMSIZ —
"hangi sektör lider, piyasa risk-on mu risk-off mu" bilgisini taşımaz. Bu katman eldeki
veriden (DIŞ BAĞIMLILIK YOK) sektör görece-gücü + makro rejim derler ve bir **bağlam
tilt'i** üretir. AI anlatı (on-demand) bu derlenmiş sayıları yorumlar — HABER UYDURMAZ.

DÜRÜSTLÜK: bu katman EDGE ÜRETMEZ. Rejim/sektör tilt'i literatür-temelli bir PRIOR'dır
(backtest edilmedi) — kararı odaklar, bağlamı görünür kılar, kanıt iddia etmez.

Tasarım: skorlama matematiği SAF fonksiyonlar (test edilebilir); compute_market_context
DB'den seriyi toplayıp bu fonksiyonları çağırır ve config['market_context']'e yazar.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.data.history import load_daily
from app.db.models import Security

log = logging.getLogger(__name__)

# --- varsayılan config (PRIOR — hiçbiri optimize edilmedi) ---------------
_DEF_CONTEXT: dict = {
    "regime": {
        "w_ema50": 15.0, "w_ema200": 10.0, "w_breadth": 40.0,
        "vol_ref": 0.02, "w_vol": 250.0,      # 20g volatilite vol_ref'in üstündeyse ceza
        "risk_on_thr": 60.0, "risk_off_thr": 40.0,
    },
    "sector": {
        "w_rel": 0.5, "w_mom": 0.3, "w_breadth": 0.2, "min_members": 3,
        "lider_pctile": 0.67, "geride_pctile": 0.33,
    },
    # bağlam tilt'i: context = strength × (base+span·sektör/100) × (base+span·rejim/100)
    # base=0.85, span=0.30 → çarpan [0.85,1.15]² ≈ [0.72, 1.32] (bounded, ±~%30).
    "tilt": {"base": 0.85, "span": 0.30},
}


# --- SAF skorlama fonksiyonları (DB'siz — test edilebilir) --------------

def regime_score(above_ema50: bool, above_ema200: bool, breadth_ema50: float,
                 vol_20d: float, cfg: dict | None = None) -> float:
    """Makro rejim skoru 0-100 (yüksek = risk-on). Şeffaf, prior-temelli heuristik.

    50 tabanı ± trend (EMA50/EMA200) ± genişlik (üyelerin EMA50 üstü oranı) − volatilite cezası.
    """
    r = (cfg or _DEF_CONTEXT)["regime"]
    s = 50.0
    s += r["w_ema50"] if above_ema50 else -r["w_ema50"]
    s += r["w_ema200"] if above_ema200 else -r["w_ema200"]
    s += (float(breadth_ema50) - 0.5) * r["w_breadth"]
    s -= max(0.0, float(vol_20d) - r["vol_ref"]) * r["w_vol"]
    return float(np.clip(s, 0.0, 100.0))


def regime_label(score: float, cfg: dict | None = None) -> str:
    r = (cfg or _DEF_CONTEXT)["regime"]
    if score >= r["risk_on_thr"]:
        return "risk_on"
    if score <= r["risk_off_thr"]:
        return "risk_off"
    return "neutral"


def score_sectors(sector_rows: list[dict], cfg: dict | None = None) -> list[dict]:
    """Sektör istatistiklerini (rel_strength_20d, mom_20d, above_ema50) 0-100'e sıralar.

    Her sektör için ağırlıklı ham blend → kesitsel RANK-PERSENTİL (robust). trend etiketi
    persentil eşiğiyle (lider/nötr/geride). score desc sıralı, rank atanmış liste döner.
    """
    sw = (cfg or _DEF_CONTEXT)["sector"]
    rows = [dict(r) for r in sector_rows]
    if not rows:
        return []
    for r in rows:
        r["_raw"] = (sw["w_rel"] * float(r.get("rel_strength_20d") or 0.0)
                     + sw["w_mom"] * float(r.get("mom_20d") or 0.0)
                     + sw["w_breadth"] * (float(r.get("above_ema50") or 0.5) - 0.5))
    raws = [r["_raw"] for r in rows]
    n = len(rows)
    for r in rows:
        # mid-rank persentil (simetrik: min≈0, max≈1) — az sektörde bile eşikler temiz oturur
        less = sum(1 for x in raws if x < r["_raw"])
        equal = sum(1 for x in raws if x == r["_raw"])
        pct = (less + 0.5 * equal) / n
        r["score"] = round(pct * 100.0, 1)
        r["trend"] = ("lider" if pct >= sw["lider_pctile"]
                      else "geride" if pct <= sw["geride_pctile"] else "nötr")
    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r.pop("_raw", None)
    return rows


def context_tilt(strength: float, sector_score: float | None,
                 regime_score_: float | None, cfg: dict | None = None) -> float:
    """Bağlam-ayarlı skor: alt-yön güç × sektör tilt × makro rejim tilt (bounded).

    sector_score/regime yoksa nötr (100'e eşdeğer → çarpan base+span=1.15? hayır: 50 kabul
    edilir → çarpan base+0.5·span). Sonuç 0-100 clamp. PRIOR — kanıt değil.
    """
    t = (cfg or _DEF_CONTEXT)["tilt"]
    base, span = float(t["base"]), float(t["span"])
    sec = 50.0 if sector_score is None else float(sector_score)
    reg = 50.0 if regime_score_ is None else float(regime_score_)
    factor = (base + span * sec / 100.0) * (base + span * reg / 100.0)
    return float(np.clip(float(strength) * factor, 0.0, 100.0))


# --- DB derlemesi -------------------------------------------------------

def _roc(ac: pd.Series, n: int) -> float:
    if len(ac) <= n:
        return float("nan")
    a, b = ac.iloc[-(n + 1)], ac.iloc[-1]
    if pd.isna(a) or pd.isna(b) or a == 0:
        return float("nan")
    return float(b / a - 1.0)


def compute_market_context(session: Session, min_bars: int = 120) -> dict | None:
    """Evrenin adjusted kapanışlarından makro rejim + sektör görece-gücü derle.

    Eş-ağırlık evren = piyasa vekili (XU100 yerine, tutarlı ve PIT). USDTRY trendi
    bilgilendirici (BIST TL-nominal olduğundan rejim skoruna hafif etki — yön belirsiz).
    """
    cfg = get_config(session, "context") or _DEF_CONTEXT
    secs = session.execute(select(Security.ticker, Security.sector)).all()

    closes: dict[str, pd.Series] = {}
    sector_of: dict[str, str] = {}
    for t, sector in secs:
        d = load_daily(session, t)
        if d.empty or len(d) < min_bars:
            continue
        ac = d["adj_close"].astype(float)
        if ac.notna().sum() < min_bars:
            continue
        closes[t] = ac
        sector_of[t] = sector or "Diğer"
    if not closes:
        log.warning("market context: yeterli seri yok")
        return None

    px = pd.DataFrame(closes)
    rets = px.pct_change()
    last_date = str(px.index[-1])

    # --- makro: eş-ağırlık piyasa endeksi ---
    market_ret = rets.mean(axis=1)
    mkt_index = (1.0 + market_ret.fillna(0.0)).cumprod()
    mkt_ema50 = mkt_index.ewm(span=50, adjust=False).mean()
    mkt_ema200 = mkt_index.ewm(span=200, adjust=False).mean()
    above50 = bool(mkt_index.iloc[-1] > mkt_ema50.iloc[-1])
    above200 = bool(mkt_index.iloc[-1] > mkt_ema200.iloc[-1])
    mkt_roc20 = float(np.expm1(np.log1p(market_ret).rolling(20).sum()).iloc[-1])
    mkt_roc5 = float(np.expm1(np.log1p(market_ret).rolling(5).sum()).iloc[-1])
    vol20 = float(market_ret.tail(20).std()) if market_ret.notna().sum() > 5 else 0.0

    # üye-bazlı: kendi EMA50 üstü (genişlik) + 20g getiri
    above_own: dict[str, bool] = {}
    roc20_t: dict[str, float] = {}
    roc60_t: dict[str, float] = {}
    for t, ac in closes.items():
        e50 = ac.ewm(span=50, adjust=False).mean()
        above_own[t] = bool(ac.iloc[-1] > e50.iloc[-1])
        roc20_t[t] = _roc(ac, 20)
        roc60_t[t] = _roc(ac, 60)
    breadth_ema50 = float(np.mean([1.0 if v else 0.0 for v in above_own.values()]))
    last_ret = rets.iloc[-1]
    breadth_today = float((last_ret > 0).sum() / max(1, int(last_ret.notna().sum())))

    reg_score = regime_score(above50, above200, breadth_ema50, vol20, cfg)
    reg_label = regime_label(reg_score, cfg)

    # --- USDTRY trendi (bilgilendirici) ---
    from app.data.fx_macro import usdtry_trend
    fx = usdtry_trend()

    # --- sektör istatistikleri ---
    min_members = int((cfg.get("sector") or _DEF_CONTEXT["sector"]).get("min_members", 3))
    members: dict[str, list[str]] = {}
    for t in closes:
        members.setdefault(sector_of[t], []).append(t)
    sec_stats: list[dict] = []
    for sec, mem in members.items():
        if len(mem) < min_members:
            continue  # tek/iki-üyeli "sektör" = tek hissenin gürültüsü, sektör sinyali değil
        r20 = [roc20_t[t] for t in mem if not np.isnan(roc20_t[t])]
        r60 = [roc60_t[t] for t in mem if not np.isnan(roc60_t[t])]
        if not r20:
            continue
        mom20 = float(np.mean(r20))
        sec_stats.append({
            "sector": sec,
            "n": len(mem),
            "mom_20d": round(mom20, 4),
            "mom_60d": round(float(np.mean(r60)), 4) if r60 else None,
            "rel_strength_20d": round(mom20 - mkt_roc20, 4),
            "above_ema50": round(float(np.mean([1.0 if above_own[t] else 0.0 for t in mem])), 3),
        })
    sectors = score_sectors(sec_stats, cfg)
    sector_score_map = {r["sector"]: r["score"] for r in sectors}

    notes = _regime_notes(above50, above200, breadth_ema50, vol20, mkt_roc20, fx, sectors)

    return {
        "as_of": last_date,
        "macro": {
            "regime": reg_label,
            "regime_score": round(reg_score, 1),
            "above_ema50": above50,
            "above_ema200": above200,
            "market_ret_5d": round(mkt_roc5, 4),
            "market_ret_20d": round(mkt_roc20, 4),
            "breadth_today": round(breadth_today, 3),
            "breadth_ema50": round(breadth_ema50, 3),
            "vol_20d": round(vol20, 4),
            "usdtry": fx,
            "n_names": len(closes),
            "notes": notes,
        },
        "sectors": sectors,
        "sector_score": sector_score_map,
        "tilt_cfg": cfg.get("tilt", _DEF_CONTEXT["tilt"]),
    }


def _regime_notes(above50: bool, above200: bool, breadth: float, vol: float,
                  mkt_roc20: float, fx: dict, sectors: list[dict]) -> list[str]:
    """AI anlatı + UI için insan-okur sinyal notları (yorum DEĞİL, ham gözlem)."""
    out: list[str] = []
    out.append(f"Piyasa (eş-ağırlık) EMA50 {'üstünde' if above50 else 'altında'}, "
               f"EMA200 {'üstünde' if above200 else 'altında'}.")
    out.append(f"20g piyasa getirisi %{mkt_roc20 * 100:.1f}.")
    out.append(f"Genişlik: isimlerin %{breadth * 100:.0f}'i kendi EMA50'sinin üstünde.")
    out.append(f"20g günlük volatilite %{vol * 100:.1f}.")
    if fx and fx.get("ret_20d") is not None:
        out.append(f"USDTRY 20g %{fx['ret_20d'] * 100:+.1f} "
                   f"({'lira zayıflıyor' if fx['ret_20d'] > 0 else 'lira güçleniyor'}).")
    if sectors:
        lead = [s["sector"] for s in sectors if s["trend"] == "lider"][:3]
        lag = [s["sector"] for s in sectors if s["trend"] == "geride"][-3:]
        if lead:
            out.append("Lider sektörler: " + ", ".join(lead) + ".")
        if lag:
            out.append("Geride kalan sektörler: " + ", ".join(lag) + ".")
    return out


def store_market_context(session: Session) -> dict | None:
    """compute_market_context → config['market_context']. Döner: hesaplanan dict (veya None)."""
    ctx = compute_market_context(session)
    if ctx is None:
        return None
    set_config(session, "market_context", ctx)
    return ctx
