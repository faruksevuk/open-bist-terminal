"""Özellik motoru — tüm evren için kesitsel (cross-sectional) özellik çerçevesi.

Skorlama (scoring.py) bunu kullanır: her ticker'ın en güncel indikatör snapshot'ı +
F-Score/accrual (fundamentals'tan) + piyasa ko-hareket (oversold/stab percentile ve
sub_cause için). Oversold & stabilizasyon EVREN-İÇİ göreli olduğundan tüm isimler
birlikte gerekir (v0.2 §3.2/§3.4).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.history import load_daily
from app.db.models import Fundamental, Security
from app.engine.indicators import compute_indicators

log = logging.getLogger(__name__)


def _fundamentals_map(session: Session) -> dict[str, dict]:
    """ticker → en güncel {f, accrual, is_na} (fundamentals tablosundan)."""
    rows = session.execute(
        select(
            Fundamental.ticker, Fundamental.as_of, Fundamental.piotroski_f,
            Fundamental.accrual_ratio, Fundamental.raw, Fundamental.pe, Fundamental.pb,
        )
    ).all()
    latest: dict[str, tuple] = {}
    for ticker, as_of, f, accr, raw, pe, pb in rows:
        if ticker not in latest or as_of > latest[ticker][0]:
            latest[ticker] = (as_of, f, accr, raw, pe, pb)
    return {
        t: {"f": v[1], "accrual": v[2], "sue": (v[3] or {}).get("sue"), "pe": v[4], "pb": v[5]}
        for t, v in latest.items()
    }


def build_features(session: Session, min_bars: int = 60) -> pd.DataFrame:
    """Evren için kesitsel özellik çerçevesi (index=ticker)."""
    secs = session.execute(select(Security.ticker, Security.excluded, Security.sector)).all()
    funda = _fundamentals_map(session)

    snaps: dict[str, dict] = {}
    ret_series: dict[str, pd.Series] = {}

    for ticker, excluded, sector in secs:
        d = load_daily(session, ticker)
        if d.empty or len(d) < min_bars:
            continue
        ind = compute_indicators(d)
        last = ind.iloc[-1]
        rets = ind["close"].pct_change()
        ret_series[ticker] = rets
        f_info = funda.get(ticker, {})
        snaps[ticker] = {
            "excluded": bool(excluded),
            "sector": sector,
            "bars": int(len(ind)),
            "close": _f(last.get("close")),
            "rsi14": _f(last.get("rsi14")),
            "atr_pct": _f(last.get("atr_pct")),
            "adx14": _f(last.get("adx14")),
            "dist_ema50": _f(last.get("dist_ema50")),
            "dist_52w_high": _f(last.get("dist_52w_high")),
            "drawdown_20d": _f(last.get("drawdown_20d")),
            "macd_hist": _f(last.get("macd_hist")),
            "roc20": _f(last.get("roc20")),  # momentum faktörü
            "roc5": _f(last.get("roc5")),  # kısa-vade dönüş (rev5 = -roc5, ölçülü edge)
            "avg_tl_vol_20": _f(last.get("avg_tl_vol_20")),
            "max_1m": _f(last.get("max_1m")),
            "last_ret": _f(rets.iloc[-1]) if len(rets) else None,
            "ret_20d": _f(ind["close"].pct_change(20).iloc[-1]) if len(ind) > 20 else None,
            # günlük oynaklık (60g getiri std) — skor satırındaki 5g hedef bandı için
            # (target_bands._LOOKBACK ile aynı pencere; ekstra I/O yok, seri zaten elde)
            "sigma20": _f(rets.tail(60).std(ddof=0)) if len(rets) >= 20 else None,
            # stabilizasyon ham bileşenleri
            "rsi_delta": _f(ind["rsi14"].diff().iloc[-1]) if len(ind) > 1 else None,
            "vol_dryup": _vol_dryup(ind),
            "bullish_bar": _bullish(ind),
            "higher_low": _higher_low(ind),
            "f_score": f_info.get("f"),
            "accrual": f_info.get("accrual"),
            "sue": f_info.get("sue"),  # PEAD kazanç-sürprizi (katalist)
            "pe": f_info.get("pe"),  # value faktörü
            "pb": f_info.get("pb"),
        }

    if not snaps:
        return pd.DataFrame()

    df = pd.DataFrame.from_dict(snaps, orient="index")

    # --- piyasa ko-hareket (sub_cause için): eşit-ağırlık piyasa getirisi ---
    rets_df = pd.DataFrame(ret_series)
    market_ret = rets_df.mean(axis=1)
    win = 20
    mkt_ret_20d_scalar = float((1 + market_ret.tail(win)).prod() - 1)
    df["market_ret_20d"] = mkt_ret_20d_scalar
    
    # --- sektör relatif güç (RS) persentili ---
    if "sector" in df and "ret_20d" in df:
        sector_rets = df.groupby("sector")["ret_20d"].mean()
        sector_rs = sector_rets - mkt_ret_20d_scalar
        sector_rs_pct = sector_rs.rank(pct=True) * 100.0
        df["sector_rs_percentile"] = df["sector"].map(sector_rs_pct).fillna(50.0)
    else:
        df["sector_rs_percentile"] = 50.0

    # 40-gün piyasa korelasyonu — vektörize (eski hisse-başına concat/corr döngüsü kaldırıldı)
    corr = rets_df.tail(40).corrwith(market_ret.tail(40))
    df["corr_market"] = corr.reindex(df.index)
    # breadth (bugün yükselen oranı): her hissenin KENDİ son getirisi — en güncel barı
    # eksik isimler artık yanlışlıkla "düşüyor" sayılmaz (eşiği şişirmez).
    last_ret = df["last_ret"].dropna()
    df.attrs["breadth"] = float((last_ret > 0).mean()) if len(last_ret) else 0.5
    df.attrs["market_vol_20d"] = float(market_ret.tail(20).std()) if len(market_ret) > 5 else 0.0
    return df


def _f(v) -> float | None:
    if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vol_dryup(ind: pd.DataFrame) -> float | None:
    """Son hacim / 20g ort hacim — düşüşte hacim kuruması (düşük = kuruyor)."""
    if "vol_tl" not in ind or len(ind) < 20:
        return None
    recent = ind["vol_tl"].iloc[-1]
    avg = ind["vol_tl"].tail(20).mean()
    return _f(recent / avg) if avg else None


def _bullish(ind: pd.DataFrame) -> float | None:
    if len(ind) < 1:
        return None
    last = ind.iloc[-1]
    return 1.0 if last["close"] > last["open"] else 0.0


def _higher_low(ind: pd.DataFrame, n: int = 5) -> float | None:
    """Son dip, ondan önceki n-günlük dipten yüksek mi (dönüş başlangıcı)."""
    if len(ind) < n + 2:
        return None
    low = ind["low"]
    return 1.0 if low.iloc[-1] > low.iloc[-(n + 1):-1].min() else 0.0
