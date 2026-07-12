"""Teknik indikatör motoru — SCORING v0.2 §3 / MASTER §7.

Girdi: daily_bars DataFrame'i (open, high, low, close, adj_close, volume).
Tüm fiyat-temelli indikatörler **ADJUSTED** seride hesaplanır (v0.2 §9.2):
adj faktörü = adj_close/close ile O/H/L düzeltilir. TL hacim ham fiyatla.
Wilder smoothing (RMA) = ewm(alpha=1/n, adjust=False).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --- temel yardımcılar --------------------------------------------------

def _rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder hareketli ortalama (RSI/ATR/ADX için)."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _rma(gain, n)
    avg_loss = _rma(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # Gerçek "sıfır kayıp" (avg_loss=0 ama kazanç var) → 100. Isınma barları (henüz
    # tanımsız) NaN KALIR — eskiden fillna(100) onları yapay "aşırı-alım=100" gösteriyordu.
    return out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return _rma(true_range(high, low, close), n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    tr_n = _rma(true_range(high, low, close), n)
    plus_di = 100 * _rma(plus_dm, n) / tr_n.replace(0, np.nan)
    minus_di = 100 * _rma(minus_dm, n) / tr_n.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _rma(dx.fillna(0), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def roc(close: pd.Series, n: int) -> pd.Series:
    return close.pct_change(n) * 100


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


# --- adjusted OHLC kurulumu --------------------------------------------

def adjusted_ohlc(daily: pd.DataFrame) -> pd.DataFrame:
    """daily_bars df → ADJUSTED open/high/low/close + raw close + volume + vol_tl."""
    df = daily.copy()
    factor = (df["adj_close"] / df["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out = pd.DataFrame(index=df.index)
    out["open"] = df["open"] * factor
    out["high"] = df["high"] * factor
    out["low"] = df["low"] * factor
    out["close"] = df["adj_close"]          # adjusted close
    out["raw_close"] = df["close"]          # TL hacim için ham fiyat
    out["volume"] = df["volume"]
    out["vol_tl"] = df["close"] * df["volume"]  # gerçek işlem hacmi (TL)
    return out


# --- ana hesap ----------------------------------------------------------

def compute_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    """daily_bars df → indikatör kolonları eklenmiş ADJUSTED DataFrame."""
    if daily.empty:
        return pd.DataFrame()
    df = adjusted_ohlc(daily)
    c, h, low_, v = df["close"], df["high"], df["low"], df["volume"]

    df["ema20"] = ema(c, 20)
    df["ema50"] = ema(c, 50)
    df["ema200"] = ema(c, 200)
    df["rsi14"] = rsi(c, 14)
    df["atr14"] = atr(h, low_, c, 14)
    df["atr_pct"] = df["atr14"] / c                      # M tabanı + risk valfi
    df["adx14"] = adx(h, low_, c, 14)
    macd_line, macd_sig, macd_hist = macd(c)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd_line, macd_sig, macd_hist
    df["roc5"] = roc(c, 5)
    df["roc20"] = roc(c, 20)
    df["roc125"] = roc(c, 125)
    bb_mid, bb_up, bb_low = bollinger(c, 20, 2.0)
    df["bb_mid"], df["bb_upper"], df["bb_lower"] = bb_mid, bb_up, bb_low

    df["high_52w"] = c.rolling(252, min_periods=20).max()
    df["low_52w"] = c.rolling(252, min_periods=20).min()
    df["dist_52w_high"] = (c - df["high_52w"]) / df["high_52w"]      # negatif = zirveden uzak
    df["dist_ema50"] = (c - df["ema50"]) / df["ema50"]              # oversold girdisi (ters)
    df["avg_tl_vol_20"] = df["vol_tl"].rolling(20, min_periods=5).mean()  # likidite kapısı

    daily_ret = c.pct_change()
    df["max_1m"] = daily_ret.rolling(21).max()                      # MAX/lotto (Bali 2011)
    df["ret_5d"] = c.pct_change(5)
    df["drawdown_20d"] = (c - c.rolling(20).max()) / c.rolling(20).max()  # son sert düşüş
    return df


def latest_snapshot(daily: pd.DataFrame) -> dict | None:
    """En güncel barın indikatör değerleri (skorlama bunu kullanır)."""
    ind = compute_indicators(daily)
    if ind.empty:
        return None
    last = ind.iloc[-1]
    cols = [
        "close", "ema20", "ema50", "ema200", "rsi14", "atr14", "atr_pct", "adx14",
        "macd", "macd_signal", "macd_hist", "roc5", "roc20", "roc125",
        "bb_upper", "bb_lower", "high_52w", "low_52w", "dist_52w_high", "dist_ema50",
        "avg_tl_vol_20", "max_1m", "ret_5d", "drawdown_20d",
    ]
    snap = {k: (float(last[k]) if pd.notna(last[k]) else None) for k in cols if k in last}
    snap["as_of"] = str(ind.index[-1])
    snap["bars"] = int(len(ind))
    return snap
