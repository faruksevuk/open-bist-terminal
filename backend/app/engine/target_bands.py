"""5g/30g hedef fiyat BANDI — volatilite konisi (deterministik, AI'siz).

DURUST cerceve: bu bir yon tahmini DEGIL. "Bu hisse son donemde ne kadar oynadiysa, N gun
sonra ~su aralikta olur" der — belirsizlik aralatigi. Merkez = bugunku fiyat (drift KASITLI
sifir; momentum'dan yon eklemek gizli-tahmin olur, projenin dogruluk ilkesine ters).

Matematik (rasgele-yuruyus, log-normal):
  sigma_daily = gunluk log-getiri std'si (adj_close, son `lookback` gun)
  sigma_N     = sigma_daily * sqrt(N)                       # oynaklik sqrt(zaman) ile olceklenir
  bant        = spot * exp(±z * sigma_N)                    # log-uzay (fiyat asla negatif olmaz)
  z=1 => ~%68 (1 sigma), z=2 => ~%95 (2 sigma)

Uyari (bilincli kabul): getiriler tam normal/duragan degil (sisman kuyruk, vol kumelenmesi);
BIST'te ±%10 fiyat limiti var — ama sigma zaten limit-kisitli fiyatlardan olculdugu icin bant
ampirik olarak limit-farkinda. Bu yuzden ekstra kirpma yok. Kisa gecmis (IPO) => bant None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.data.history import load_daily

_DEFAULT_HORIZONS = (5, 30)   # islem gunu (takvim degil) — Faruk: "5 gunluk / 30 gunluk"
_LOOKBACK = 60                # ~3 ay realized vol (recency<->stabilite dengesi)
_MIN_OBS = 20                 # bu kadar getiri yoksa bant guvenilmez -> None


def cone(spot: float, sigma_daily: float, horizon_days: int, z: float) -> tuple[float, float]:
    """Tek bant: (alt, ust) = spot * exp(-/+ z*sigma_daily*sqrt(N)). Saf; test edilebilir."""
    sig_h = sigma_daily * math.sqrt(horizon_days)
    return spot * math.exp(-z * sig_h), spot * math.exp(z * sig_h)


def sigma_daily_from_closes(adj_closes: list[float] | np.ndarray, lookback: int = _LOOKBACK) -> float | None:
    """Gunluk log-getiri std'si (son `lookback`). Yeterli veri yoksa None. Saf."""
    a = np.asarray([c for c in adj_closes if c and c > 0], dtype=float)
    if a.size < _MIN_OBS + 1:
        return None
    logret = np.diff(np.log(a))
    logret = logret[-lookback:]
    if logret.size < _MIN_OBS:
        return None
    s = float(np.std(logret, ddof=1))
    # ~0 vol = dejenere/sayisal artefakt (sabit fiyat serisi kayan-noktada 1e-16 verir),
    # gercek hisse degil -> bant anlamsiz -> None. 1e-6 gunluk vol zaten gerceklik-disi dusuk.
    return s if s > 1e-6 else None


@dataclass
class TargetBands:
    ticker: str
    as_of: date
    spot: float
    sigma_daily: float          # gunluk oynaklik (0.028 = %2.8)
    bands: dict                  # {horizon: {low1, high1, low2, high2, sigma_h, pct1, pct2}}


def _band_row(spot: float, sigma_daily: float, h: int) -> dict:
    low1, high1 = cone(spot, sigma_daily, h, 1.0)
    low2, high2 = cone(spot, sigma_daily, h, 2.0)
    sig_h = sigma_daily * math.sqrt(h)
    return {
        "low1": round(low1, 2), "high1": round(high1, 2),   # ~%68 (1 sigma)
        "low2": round(low2, 2), "high2": round(high2, 2),   # ~%95 (2 sigma)
        "sigma_h": round(sig_h, 4),
        "pct1": round((math.exp(sig_h) - 1) * 100, 1),       # 1σ beklenen hareket (+%)
        "pct2": round((math.exp(2 * sig_h) - 1) * 100, 1),   # 2σ beklenen hareket (+%)
    }


def compute_bands(
    session: Session, ticker: str, horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    lookback: int = _LOOKBACK,
) -> TargetBands | None:
    """Bir hissenin 5g/30g volatilite-konisi bandi. Yetersiz gecmis/veri => None."""
    df = load_daily(session, ticker)
    if df.empty or "adj_close" not in df.columns:
        return None
    closes = df["adj_close"].to_numpy(dtype=float)
    sigma = sigma_daily_from_closes(closes, lookback=lookback)
    if sigma is None:
        return None
    spot = float(closes[-1])
    if not spot or spot <= 0:
        return None
    as_of = df.index[-1]
    as_of = as_of if isinstance(as_of, date) else date.today()
    bands = {h: _band_row(spot, sigma, h) for h in horizons}
    return TargetBands(ticker=ticker, as_of=as_of, spot=round(spot, 2),
                       sigma_daily=round(sigma, 4), bands=bands)
