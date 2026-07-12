"""Setup başına DÜRÜST olay-çalışması (event study) doğrulaması — SETUPS v0.1.

Her setup'ı KENDİ verimizde point-in-time bir olay-çalışmasıyla bağımsız doğrular.
Bu bir DOĞRULAMA'dır, optimizasyon DEĞİL: parametreler prior'dır (setups._DEF_SETUPS),
tam olarak BİR kez koşulur. HİÇBİR parametre araması yapılmaz (§9.5 deneme disiplini —
docs/BIST-SCORING-v0.2.md).

Metodoloji:
- PIT walk: 220+ barı olan tüm tickerlarda, ısınma 210 sonrası her bar t'de dedektörü
  YALNIZ t'ye kadarki veriyle koştur.
- Tetikte: giriş = t+1 barının OPEN'ı (adjusted). h=1,3,5,10 barlık FORWARD FAZLA getiri:
  excess = hissenin (giriş open → t+1+h close) getirisi EKSİ evrenin aynı penceredeki
  MEDYAN forward getirisi (kesitsel demeaning rejim sürüklenmesini öldürür).
- Basit işlem simülasyonu: setup'ın stop/target/time-exit'i günlük barlarda; muhafazakâr —
  bir bar low<=stop AND high>=target ise ÖNCE STOP sayılır. R-multiple üretir.
- İstatistiksel dürüstlük: olaylar zamanda kümelenir → önce AYNI GÜNÜN olayları günlük
  ortalamaya toplanır; t = DAILY ortalama seri üzerinde newey_west_tstat (lags=horizon);
  ayrıca bootstrap_ci. Ticker başına AYNI setup'ın örtüşen tetikleri atlanır (önceki olaydan
  horizon(10) bar geçmeden yeni olay yok).

Verdict (setup başına, 5g excess'e göre):
- "kanıtlı"    : n_events >= 30 AND mean_excess(5g) > 0 AND NW t >= 1.5
- "zayıf"      : n >= 30, pozitif ama t < 1.5
- "deneysel"   : n < 30
- "devre dışı" : mean_excess(5g) <= 0 (negatif-edge; API'de gizli, include_all ile taranabilir)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.metrics import bootstrap_ci, newey_west_tstat
from app.config_store import get_config, set_config
from app.data.history import load_daily
from app.db.models import Security
from app.engine.indicators import compute_indicators
from app.engine.setups import EVENT_STUDY_DETECTORS, MarketContext, _DEF_SETUPS

log = logging.getLogger(__name__)

_WARMUP = 210
_MIN_BARS = 220
_HORIZONS = [1, 3, 5, 10]
_MAX_H = max(_HORIZONS)

# İşlem maliyeti varsayılanı (config 'costs' yoksa fallback — seed_config ile birebir).
_DEF_COSTS = {"commission_pct_per_side": 0.0004, "spread_slippage_pct_per_side": 0.0010}


def round_trip_cost_pct(session: Session) -> float:
    """Config 'costs' → round-trip maliyet fraksiyonu = 2×(komisyon+slippage).

    get_config fallback deseni (yoksa muhafazakâr varsayılan). Fiyat-getiri uzayında;
    simulate_trade_detail'e doğrudan geçirilir.
    """
    c = get_config(session, "costs") or _DEF_COSTS
    commission = float(c.get("commission_pct_per_side", _DEF_COSTS["commission_pct_per_side"]))
    slippage = float(c.get("spread_slippage_pct_per_side", _DEF_COSTS["spread_slippage_pct_per_side"]))
    return 2.0 * (commission + slippage)


def _build_panels(session: Session, min_bars: int = _MIN_BARS,
                  tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Ticker → indikatör paneli (bir kez hesaplanır — runtime patlamasın)."""
    if tickers is None:
        tickers = list(session.execute(select(Security.ticker)).scalars().all())
    panels: dict[str, pd.DataFrame] = {}
    for t in tickers:
        d = load_daily(session, t)
        if d.empty or len(d) < min_bars:
            continue
        ind = compute_indicators(d)
        if not ind.empty and len(ind) >= min_bars:
            panels[t] = ind
    return panels


def _market_series(panels: dict[str, pd.DataFrame], common: list) -> pd.DataFrame:
    """Ortak takvimde eş-ağırlık piyasa yardımcı serileri (PIT: her bar yalnız o güne dek).

    Döner: DataFrame [ret, ret5, above_ema50, breadth] index=common.
    Bunlar zaten yalnız geçmiş+bugünü kullanır (rolling/pct_change) → t'de PIT değeri
    doğrudan .iat[i] ile alınabilir.
    """
    ret_df = pd.DataFrame(
        {t: p["close"].reindex(common).pct_change() for t, p in panels.items()}
    )
    market_ret = ret_df.mean(axis=1)
    # eş-ağırlık endeks seviyesi → kendi EMA50'siyle karşılaştır
    eq_index = (1.0 + market_ret.fillna(0.0)).cumprod()
    eq_ema50 = eq_index.ewm(span=50, adjust=False).mean()
    above = eq_index > eq_ema50
    breadth = (ret_df > 0).sum(axis=1) / ret_df.notna().sum(axis=1).replace(0, np.nan)
    ret5 = np.expm1(np.log1p(market_ret).rolling(5).sum())
    ret15 = np.expm1(np.log1p(market_ret).rolling(15).sum())  # rs_shield piyasa 15g
    out = pd.DataFrame({
        "ret": market_ret, "ret5": ret5, "ret15": ret15,
        "above_ema50": above, "breadth": breadth,
    }, index=common)
    return out


def _cross_rs15(panels: dict[str, pd.DataFrame], common: list, mkt_ret15: pd.Series,
                pctile: float) -> pd.Series:
    """Her bar için evren rs15 = (hisse 15g − piyasa 15g) p-persentil eşiği (rs_shield; PIT)."""
    ret15_df = pd.DataFrame(
        {t: p["close"].reindex(common).pct_change(15) for t, p in panels.items()}
    )
    rs15_df = ret15_df.sub(mkt_ret15, axis=0)
    return rs15_df.quantile(pctile, axis=1)


def _cross_roc3(panels: dict[str, pd.DataFrame], common: list, decile: float) -> pd.DataFrame:
    """Her bar için evren roc3 alt-desil eşiği + evren min roc3 (snapback strength için).

    Döner: DataFrame [roc3_p10, roc3_min] index=common. Kesitsel; PIT (o günün roc3'ü).
    """
    roc3_df = pd.DataFrame(
        {t: p["close"].reindex(common).pct_change(3) for t, p in panels.items()}
    )
    p10 = roc3_df.quantile(decile, axis=1)
    rmin = roc3_df.min(axis=1)
    return pd.DataFrame({"roc3_p10": p10, "roc3_min": rmin}, index=common)


def _fwd_return(panel: pd.DataFrame, common: list, i: int, h: int,
                entry_open: float) -> float | None:
    """Giriş t+1 OPEN → t+1+h CLOSE getirisi (adjusted). None = veri yok."""
    j = i + 1 + h
    if j >= len(common):
        return None
    exit_close = panel["close"].reindex(common).iat[j]
    if pd.isna(exit_close) or entry_open == 0 or pd.isna(entry_open):
        return None
    return float(exit_close / entry_open - 1.0)


@dataclass
class TradeResult:
    """Bir setup işleminin tam simülasyon sonucu (kapandıysa) veya kısmi durumu.

    status: 'target' | 'stop' | 'time_exit' | 'pending' | 'no_entry'.
    'pending' = giriş oldu ama zaman-çıkışına kadarki barlar HENÜZ tam gelmedi ve stop/target
    vurulmadı → yeniden değerlendirilecek. 'no_entry' = giriş barı hiç oluşmadı.
    R, exit_price, exit_index, days_held yalnız KAPANDIĞINDA doludur.

    İşlem maliyeti (komisyon+spread) hesaba katılırsa `r_multiple`/`realized_pct` alanları
    GROSS kalır (eski tüketiciler anlamını sessizce değiştirmesin); NET karşılıkları ayrı
    alanlarda döner. `round_trip_cost_pct == 0` ise net == gross.
    """

    status: str
    entry_index: int | None = None
    entry_price: float | None = None
    exit_index: int | None = None
    exit_price: float | None = None
    r_multiple: float | None = None          # GROSS R (maliyet öncesi)
    days_held: int | None = None
    r_multiple_net: float | None = None      # NET R (round-trip maliyet düşülmüş)
    pct: float | None = None                 # GROSS getiri fraksiyonu (exit/entry - 1)
    pct_net: float | None = None             # NET getiri fraksiyonu (gross_pct - round_trip)


def simulate_trade_detail(
    o: pd.Series, h: pd.Series, low: pd.Series, c: pd.Series,
    entry_j: int, stop: float, target: float, time_exit_days: int,
    round_trip_cost_pct: float = 0.0,
    exit_policy: str = "fixed", exit_cfg: dict | None = None,
) -> TradeResult:
    """Stop/target/time-exit günlük bar yürüyüşü (TEK doğruluk kaynağı — hem event-study
    hem canlı sonuç-takibi bunu kullanır; mantık ÇATALLANMAZ).

    ÇIKIŞ POLİTİKALARI (exit_policy — ön-kayıtlı, §9.5; canlı takip DAİMA 'fixed'):
    - 'fixed'      (VARSAYILAN, DAVRANIŞ BİREBİR KORUNUR): stop/target/time-exit, stop-önce.
    - 'trail'      : sabit hedef YOK; iz-süren stop = max(stop, HWM − trail_mult×risk). Kazananı
                     koştur. Çıkış = trail-stop ya da time. HWM bu barın high'ıyla güncellenip
                     SONRAKİ barın stop'unu yükseltir (look-ahead yok). Tek round-trip maliyeti.
    - 'partial_be' : 1R'de (first_r) scale_frac kadar sat + stop→başabaş, kalanı orijinal hedefe.
                     Blended R. 2 satış → 1.5× round-trip maliyeti (dürüst friction).
    exit_cfg = politika parametreleri (yoksa config 'exits' varsayılanı çağıran tarafça verilir).

    Konvansiyonlar (event_study ile birebir):
    - Giriş = `entry_j` barının OPEN'ı (adjusted); çağıran, entry_j = sinyal barı + 1 verir.
    - Muhafazakâr stop-önce: bir bar low<=stop AND high>=target ise ÖNCE STOP (worst-case).
    - Zaman-çıkışı: entry_j + time_exit_days barının CLOSE'u (o bara kadar stop/target yoksa).
    - R = (exit - entry) / (entry - stop). risk = entry - stop > 0 olmalı.

    İşlem maliyeti: `round_trip_cost_pct` = 2×(komisyon+slippage), FİYAT-getiri uzayında.
    net_pct = gross_pct − round_trip_cost_pct. R-uzayına çevrim: R denominatörü (entry−stop)
    fiyat mesafesi olduğundan, maliyet fraksiyonu entry ile çarpılıp risk'e bölünür:
        cost_R = round_trip_cost_pct × entry / (entry − stop)
        net_R  = gross_R − cost_R
    (Yani net_R = net_pct / risk_fraksiyonu; ikisi de aynı R matematiğinden tutarlı türer.)
    `round_trip_cost_pct == 0` (varsayılan) → net == gross, eski davranış değişmez.

    MALİYET-FİZİBİLİTE guard'ı: risk-fraksiyonu ((entry−stop)/entry) round-trip maliyetten
    KÜÇÜK/eşitse, işlem maliyeti tüm 1R risk bütçesini yer → ekonomik olarak GİRİLEMEZ. Böyle
    bir işlemin net_R'si anlamsızdır (risk→0'da R-normalizasyon patlar; float artefaktı) →
    `r_multiple_net=None` (net-R toplamasından düşer). GROSS ve pct_net dokunulmaz. Eşik
    maliyetin KENDİSİdir (ayar/tuning YOK). Canlı sizing'de bu isimler zaten ATR-tabanıyla
    (min_move_atr_pct) elenir; net-R'de de dürüstçe dışlanır.

    Barlar YETMEZSE (entry_j + time_exit_days henüz oluşmadı) ve stop/target vurulmadıysa
    → status='pending' (kısmi; kapanmamış). Giriş barı yoksa → 'no_entry'.
    """
    n = len(c)
    if entry_j >= n:
        return TradeResult(status="no_entry")
    entry = o.iat[entry_j]
    if pd.isna(entry) or entry <= stop:
        return TradeResult(status="no_entry")
    risk = entry - stop
    if risk <= 0:
        return TradeResult(status="no_entry")

    # maliyeti R-uzayına çevir (net_R = gross_R − cost_R); fiyat-getiri uzayında da düş.
    cost_pct = float(round_trip_cost_pct)
    risk_frac = risk / float(entry)                    # (entry−stop)/entry
    cost_r = cost_pct / risk_frac if risk_frac > 0 else float("inf")
    # maliyet-fizibilite: risk-fraksiyonu maliyeti aşmıyorsa net_R anlamsız (girilemez işlem)
    cost_feasible = cost_pct <= 0.0 or risk_frac > cost_pct

    def _closed(status: str, exit_index: int, exit_price: float,
                cost_mult: float = 1.0) -> TradeResult:
        gross_r = (exit_price - entry) / risk
        gross_pct = exit_price / entry - 1.0
        net_r = float(gross_r - cost_r * cost_mult) if cost_feasible else None
        return TradeResult(
            status=status, entry_index=entry_j, entry_price=float(entry),
            exit_index=exit_index, exit_price=float(exit_price),
            r_multiple=float(gross_r), r_multiple_net=net_r,
            pct=float(gross_pct), pct_net=float(gross_pct - cost_pct * cost_mult),
            days_held=exit_index - entry_j,
        )

    def _blended(status: str, exit_index: int, gross_r: float, gross_pct: float,
                 cost_mult: float) -> TradeResult:
        """Kısmi çıkış (partial): tek fiyat yok — harmanlanmış R/pct. exit_price None."""
        net_r = float(gross_r - cost_r * cost_mult) if cost_feasible else None
        return TradeResult(
            status=status, entry_index=entry_j, entry_price=float(entry),
            exit_index=exit_index, exit_price=None,
            r_multiple=float(gross_r), r_multiple_net=net_r,
            pct=float(gross_pct), pct_net=float(gross_pct - cost_pct * cost_mult),
            days_held=exit_index - entry_j if exit_index is not None else None,
        )

    def _pending() -> TradeResult:
        return TradeResult(status="pending", entry_index=entry_j, entry_price=float(entry))

    exit_bar = entry_j + int(time_exit_days)  # zaman-çıkışı barı (dahil)
    last_available = min(exit_bar, n - 1)
    cfg = exit_cfg or {}

    # === FIXED (VARSAYILAN — DAVRANIŞ BİREBİR KORUNUR) ===
    if exit_policy == "fixed":
        for k in range(entry_j, last_available + 1):
            bl, bh = low.iat[k], h.iat[k]
            if pd.isna(bl) or pd.isna(bh):
                continue
            if bl <= stop:  # muhafazakâr: stop AND target aynı barda → stop
                # GAP-THROUGH (taban kilidi): bar stop'un ALTINDA açıldıysa fill = açılış
                # (stop koruması iş görmez — gap-down/limit worst-case). Değilse stop'ta fill.
                bo = o.iat[k]
                fill = float(bo) if (not pd.isna(bo) and bo < stop) else stop
                return _closed("stop", k, fill)
            if bh >= target:
                bo = o.iat[k]  # simetrik: bar target üstünde açıldıysa fill = açılış (daha iyi)
                fill = float(bo) if (not pd.isna(bo) and bo > target) else target
                return _closed("target", k, fill)
        if exit_bar > n - 1:
            return _pending()  # zaman-çıkışı barı henüz oluşmadı
        exit_c = c.iat[exit_bar]
        if pd.isna(exit_c):
            return _pending()
        return _closed("time_exit", exit_bar, exit_c)

    # === TRAIL: iz-süren stop (HWM − trail_mult×risk); sabit hedef YOK (kazananı koştur) ===
    if exit_policy == "trail":
        tmult = float(cfg.get("trail_mult", 2.0))
        cur_stop = stop
        h0 = h.iat[entry_j]
        hwm = float(h0) if not pd.isna(h0) else float(entry)
        for k in range(entry_j, last_available + 1):
            bl, bh = low.iat[k], h.iat[k]
            if pd.isna(bl) or pd.isna(bh):
                continue
            if bl <= cur_stop:  # önceki barlarca belirlenmiş trail'e değdi (look-ahead yok)
                bo = o.iat[k]
                fill = float(bo) if (not pd.isna(bo) and bo < cur_stop) else cur_stop
                return _closed("trail_stop", k, fill)
            if bh > hwm:        # bu barın high'ı → SONRAKİ bar için stop'u yükselt
                hwm = float(bh)
                cur_stop = max(cur_stop, hwm - tmult * risk)
        if exit_bar > n - 1:
            return _pending()
        exit_c = c.iat[exit_bar]
        if pd.isna(exit_c):
            return _pending()
        return _closed("time_exit", exit_bar, exit_c)

    # === PARTIAL_BE: first_r'de scale_frac sat + stop→başabaş; kalan orijinal hedefe (blended) ===
    if exit_policy == "partial_be":
        first_r = float(cfg.get("first_r", 1.0))
        frac = float(cfg.get("scale_frac", 0.5))
        first_target = entry + first_r * risk
        cur_stop = stop
        scaled = False
        remaining = 1.0
        r_acc = 0.0
        pct_acc = 0.0
        for k in range(entry_j, last_available + 1):
            bl, bh = low.iat[k], h.iat[k]
            if pd.isna(bl) or pd.isna(bh):
                continue
            bo = o.iat[k]
            if bl <= cur_stop:  # stop-önce (ölçek öncesi tam stop; sonrası başabaş)
                fill = float(bo) if (not pd.isna(bo) and bo < cur_stop) else cur_stop
                r_acc += remaining * (fill - entry) / risk
                pct_acc += remaining * (fill / entry - 1.0)
                return _blended("partial_stop" if scaled else "stop", k, r_acc, pct_acc,
                                1.5 if scaled else 1.0)
            if not scaled and bh >= first_target:  # ilk kez 1R → frac sat, stop→başabaş
                fill1 = float(bo) if (not pd.isna(bo) and bo > first_target) else first_target
                r_acc += frac * (fill1 - entry) / risk
                pct_acc += frac * (fill1 / entry - 1.0)
                remaining -= frac
                cur_stop = entry
                scaled = True
                if bh >= target:  # aynı barda hedefe de ulaştıysa kalanı kapat
                    fill2 = float(bo) if (not pd.isna(bo) and bo > target) else target
                    r_acc += remaining * (fill2 - entry) / risk
                    pct_acc += remaining * (fill2 / entry - 1.0)
                    return _blended("partial_target", k, r_acc, pct_acc, 1.5)
                continue
            if scaled and bh >= target:  # kalan yarı hedefe
                fill2 = float(bo) if (not pd.isna(bo) and bo > target) else target
                r_acc += remaining * (fill2 - entry) / risk
                pct_acc += remaining * (fill2 / entry - 1.0)
                return _blended("partial_target", k, r_acc, pct_acc, 1.5)
        if exit_bar > n - 1:
            return _pending()
        exit_c = c.iat[exit_bar]
        if pd.isna(exit_c):
            return _pending()
        r_acc += remaining * (exit_c - entry) / risk
        pct_acc += remaining * (exit_c / entry - 1.0)
        return _blended("partial_time" if scaled else "time_exit", exit_bar, r_acc, pct_acc,
                        1.5 if scaled else 1.0)

    raise ValueError(f"bilinmeyen exit_policy: {exit_policy}")


def _simulate_trade(panel: pd.DataFrame, common: list, i: int, res: dict,
                    cost_pct: float = 0.0) -> tuple[float | None, float | None]:
    """Stop/target/time-exit günlük bar simülasyonu → (GROSS R, NET R). Muhafazakâr: bir bar
    low<=stop AND high>=target ise ÖNCE STOP (worst-case). Giriş = t+1 open.
    R = (exit - entry_open) / (entry_open - stop). (None, None) = simüle edilemedi.

    `cost_pct` = round-trip maliyet fraksiyonu (2×komisyon+slippage). 0 → net == gross.

    NOT: gerçek yürüyüş `simulate_trade_detail`'e delege edilir — canlı sonuç-takibiyle
    (setup_outcomes.py) mantık PAYLAŞILIR, çatallanmaz. Event-study bağlamında zaman-çıkışı
    barı daima mevcuttur (i, n - _MAX_H - 1'e kadar gezilir) → 'pending' dönmez.
    """
    entry_j = i + 1
    if entry_j >= len(common):
        return None, None
    o = panel["open"].reindex(common)
    h = panel["high"].reindex(common)
    low = panel["low"].reindex(common)
    c = panel["close"].reindex(common)
    detail = simulate_trade_detail(
        o, h, low, c, entry_j, res["stop"], res["target"], res["time_exit_days"],
        round_trip_cost_pct=cost_pct,
    )
    return detail.r_multiple, detail.r_multiple_net


def _verdict(n_events: int, mean_excess_5d: float | None, t5: float | None) -> str:
    if mean_excess_5d is None or n_events == 0:
        return "deneysel"
    if mean_excess_5d <= 0:
        return "devre dışı"
    if n_events < 30:
        return "deneysel"
    # n>=30 ve pozitif
    if t5 is not None and not np.isnan(t5) and t5 >= 1.5:
        return "kanıtlı"
    return "zayıf"


def run_event_study(session: Session, write: bool = True,
                    tickers: list[str] | None = None) -> dict:
    """Setup 1,2,3,5 için PIT olay-çalışması. pead_drift ATLANIR (PIT yok).

    write=True → set_config(session, 'setup_evidence', {...}). Döner: setup başına dict.
    """
    setups_cfg = get_config(session, "setups") or _DEF_SETUPS
    cost_pct = round_trip_cost_pct(session)  # koşum anındaki config maliyeti (net trade-sim)
    panels = _build_panels(session, tickers=tickers)
    if not panels:
        return {"note": "panel yok (daily_bars boş?)", "n_tickers": 0}

    common = sorted(set().union(*[set(p.index) for p in panels.values()]))
    mkt = _market_series(panels, common)
    roc3x = _cross_roc3(panels, common, setups_cfg.get("snapback", {}).get("roc3_decile", 0.10))
    rs_pctile = setups_cfg.get("rs_shield", {}).get("rs_pctile_min", 90) / 100.0
    rs15x = _cross_rs15(panels, common, mkt["ret15"], rs_pctile)  # rs_shield kesitsel eşiği
    mkt_ret_series = mkt["ret"]  # quiet_accumulation için indeks-hizalı

    # her setup için olay kayıtları
    events: dict[str, list[dict]] = {name: [] for name in EVENT_STUDY_DETECTORS}
    n = len(common)

    # ---- per-bar piyasa bağlamı ÖNCEDEN (ticker-bağımsız → bir kez) ----
    # Her (ticker, bar) için yeni MarketContext kurmak yerine, i'ye bağlı market/cross
    # değerlerini bir kez numpy'ye çevir; ticker döngüsünde yalnız stock_ret enjekte edilir.
    m_ret5 = mkt["ret5"].to_numpy(dtype=float)
    m_ret15 = mkt["ret15"].to_numpy(dtype=float)
    m_day = mkt["ret"].to_numpy(dtype=float)
    m_above = mkt["above_ema50"].to_numpy()
    m_breadth = mkt["breadth"].to_numpy(dtype=float)
    x_p10 = roc3x["roc3_p10"].to_numpy(dtype=float)
    x_min = roc3x["roc3_min"].to_numpy(dtype=float)
    x_rs15 = rs15x.to_numpy(dtype=float)

    # ---- PIT walk ----
    for ticker, panel in panels.items():
        # panelin ortak takvimdeki hizası (reindex → i common indeksiyle örtüşür)
        pan = panel.reindex(common)
        # bu ticker'ın stock_ret'ini bir kez hesapla (quiet_accumulation hot-path)
        stock_ret = pan["close"].pct_change()
        # ticker başına, AYNI setup'ın son tetik indeksi (örtüşme baskılama)
        last_trig: dict[str, int] = {name: -10**9 for name in EVENT_STUDY_DETECTORS}
        # bu ticker'ın kendi geçerli bar aralığı (NaN olmayan close)
        valid_close = pan["close"].notna().to_numpy()
        open_arr = pan["open"].to_numpy(dtype=float)
        for i in range(_WARMUP, n - _MAX_H - 1):
            if not valid_close[i]:
                continue
            # PIT market context (o bara dek) — hafif kurulum
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
                    "mkt_ret_series": mkt_ret_series,
                    "stock_ret": stock_ret,
                },
            )
            for name, detector in EVENT_STUDY_DETECTORS.items():
                if i - last_trig[name] < _MAX_H:  # örtüşme baskılama (aynı setup)
                    continue
                res = detector(pan, i, ctx, setups_cfg)
                if res is None:
                    continue
                # giriş t+1 open (adjusted)
                entry_j = i + 1
                if entry_j >= n:
                    continue
                entry_open = open_arr[entry_j]
                if np.isnan(entry_open) or entry_open == 0:
                    continue
                # forward getiriler + evren medyanı (aynı pencere) → excess
                # regime_up: tetik anında piyasa eş-ağırlık endeksi kendi EMA50 üstünde mi
                # (ÖN-KAYITLI dilim — raporlama için; verdict'e girmez).
                rec = {"ticker": ticker, "date": common[i], "strength": res["strength"],
                       "regime_up": bool(m_above[i])}
                ok = True
                for h in _HORIZONS:
                    stock_fwd = _fwd_return(pan, common, i, h, entry_open)
                    if stock_fwd is None:
                        ok = False
                        break
                    rec[f"stock_fwd_{h}"] = stock_fwd
                if not ok:
                    continue
                # R-multiple (işlem simülasyonu) — GROSS + NET (koşum-anı maliyeti)
                rec["R"], rec["R_net"] = _simulate_trade(pan, common, i, res, cost_pct)
                events[name].append(rec)
                last_trig[name] = i

    # ---- evren medyan forward getirisi (aynı i+1 open → i+1+h close mantığıyla) ----
    # Verimlilik: her bar-tarih için evren forward getiri medyanı önceden hesaplanır.
    univ_fwd = _universe_fwd_medians(panels, common)

    # ---- setup başına toplama + istatistik ----
    out: dict[str, dict] = {}
    for name, evs in events.items():
        out[name] = _aggregate(name, evs, univ_fwd, common, setups_cfg)

    result = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "n_tickers": len(panels),
        "warmup": _WARMUP,
        "horizons": _HORIZONS,
        "round_trip_cost_pct": round(cost_pct, 6),  # net trade-sim'de kullanılan maliyet
        "params_echo": {k: setups_cfg.get(k) for k in EVENT_STUDY_DETECTORS},
        "methodology": ("PIT walk; giriş t+1 open; excess = hisse fwd - evren MEDYAN fwd "
                        "(kesitsel demeaning); günlük kümeleme + Newey-West (lags=h); "
                        "örtüşme baskılama (horizon=10 bar); stop-önce simülasyon. "
                        "trade-sim GROSS+NET (net = round-trip komisyon+spread düşülmüş); "
                        "verdict demeaned excess'e dayanır (maliyet kesitsel kanıtı etkilemez). "
                        "by_regime = ÖN-KAYITLI dilim (tetikte piyasa EMA50 üstü/altı) — "
                        "yalnız raporlama, verdict'e girmez (koşullu kural = snooping). "
                        "PARAMETRE ARAMASI YOK — tek koşum, prior doğrulama (§9.5)."),
        "pead_drift": {
            "verdict": "deneysel",
            "status": "deneysel (prior — PIT yok)",
            "note": "SUE latest-snapshot; PIT açıklama tarihi yok → dürüst event-study edilemez. "
                    "Canlı-only setup; kanıt statüsü sabit 'deneysel'.",
        },
        "setups": out,
    }

    if write:
        set_config(session, "setup_evidence", result)
    return result


def _universe_fwd_medians(panels: dict[str, pd.DataFrame], common: list) -> dict[int, dict[int, float]]:
    """Her bar-indeksi i için evrenin (i+1 open → i+1+h close) forward getiri MEDYANI.

    Döner: {i: {h: median}}. Vektörize: ticker başına forward getiri matrisi → medyan.
    """
    n = len(common)
    # ticker × common close/open matrisleri
    close_m = pd.DataFrame({t: p["close"].reindex(common).values for t, p in panels.items()},
                           index=range(n))
    open_m = pd.DataFrame({t: p["open"].reindex(common).values for t, p in panels.items()},
                          index=range(n))
    med: dict[int, dict[int, float]] = {}
    for h in _HORIZONS:
        # entry open at i+1, exit close at i+1+h → fwd[i] = close[i+1+h]/open[i+1]-1
        entry = open_m.shift(-1)                 # open[i+1]
        exit_ = close_m.shift(-(1 + h))          # close[i+1+h]
        fwd = exit_ / entry - 1.0
        med_h = fwd.median(axis=1)               # her i için evren medyanı
        for i in range(n):
            v = med_h.iat[i]
            if not pd.isna(v):
                med.setdefault(i, {})[h] = float(v)
    return med


def _aggregate(name: str, evs: list[dict], univ_fwd: dict, common: list,
               setups_cfg: dict) -> dict:
    """Olay listesini setup verdict'ine indirger (günlük kümeleme + NW t + bootstrap)."""
    if not evs:
        return {"setup": name, "n_events": 0, "n_days": 0, "verdict": "deneysel",
                "note": "hiç tetik yok"}

    # i-indeksini tarih→i eşlemesiyle bul (excess için evren medyanı)
    date_to_i = {d: k for k, d in enumerate(common)}
    df = pd.DataFrame(evs)
    # her olayın her horizon için excess'i
    for h in _HORIZONS:
        excess = []
        for _, r in df.iterrows():
            i = date_to_i.get(r["date"])
            um = univ_fwd.get(i, {}).get(h) if i is not None else None
            sfwd = r.get(f"stock_fwd_{h}")
            excess.append(sfwd - um if (um is not None and sfwd is not None and not pd.isna(sfwd)) else np.nan)
        df[f"excess_{h}"] = excess

    n_events = len(df)
    n_days = int(df["date"].nunique())

    # günlük kümeleme: aynı günün olaylarını ortalamaya topla → NW t (autocorr için)
    daily = df.groupby("date").mean(numeric_only=True).sort_index()

    per_h: dict[str, dict] = {}
    for h in _HORIZONS:
        col = f"excess_{h}"
        series = df[col].dropna().values
        daily_series = daily[col].dropna().values
        if len(series) == 0:
            per_h[str(h)] = {"n": 0}
            continue
        t_nw = newey_west_tstat(daily_series, lags=h) if len(daily_series) >= 3 else float("nan")
        lo, hi = bootstrap_ci(daily_series) if len(daily_series) >= 3 else (float("nan"), float("nan"))
        per_h[str(h)] = {
            "mean_excess": round(float(np.nanmean(series)), 4),
            "median_excess": round(float(np.nanmedian(series)), 4),
            "hit_rate": round(float((series > 0).mean()), 3),
            "n": int(len(series)),
            "t_newey_west": round(float(t_nw), 2) if not np.isnan(t_nw) else None,
            "ci95_low": round(float(lo), 4) if not np.isnan(lo) else None,
            "ci95_high": round(float(hi), 4) if not np.isnan(hi) else None,
        }

    # işlem-sim istatistikleri (R) — GROSS + NET (net = round-trip maliyet düşülmüş)
    def _profit_factor(vals: np.ndarray) -> float | None:
        w, ls = vals[vals > 0], vals[vals <= 0]
        return (float(w.sum()) / abs(float(ls.sum()))) if len(ls) and ls.sum() != 0 else None

    r_vals = df["R"].dropna().values if "R" in df else np.array([])
    r_net_vals = df["R_net"].dropna().values if "R_net" in df else np.array([])
    wins = r_vals[r_vals > 0]
    losses = r_vals[r_vals <= 0]
    profit_factor = _profit_factor(r_vals)
    profit_factor_net = _profit_factor(r_net_vals)
    trade_stats = {
        "n_trades": int(len(r_vals)),
        "mean_R": round(float(r_vals.mean()), 3) if len(r_vals) else None,
        "avg_win": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "hit_rate_R": round(float((r_vals > 0).mean()), 3) if len(r_vals) else None,
        # NET (maliyet sonrası) — friction realizmi; verdict'i ETKİLEMEZ (excess'e dayanır)
        "mean_R_net": round(float(r_net_vals.mean()), 3) if len(r_net_vals) else None,
        "profit_factor_net": round(profit_factor_net, 3) if profit_factor_net is not None else None,
        "hit_rate_R_net": round(float((r_net_vals > 0).mean()), 3) if len(r_net_vals) else None,
    }

    # --- ÖN-KAYITLI rejim dilimi: tetik anında piyasa EMA50 üstü/altı -----------------
    # SADECE raporlama: verdict'e GİRMEZ (koşullu kural değiştirme = snooping olurdu).
    # Kullanım: kullanıcı "bu setup düşen piyasada da çalışıyor mu"yu görür; kural
    # değişikliği ancak canlı OOS teyidiyle yapılır.
    by_regime: dict[str, dict] = {}
    if "regime_up" in df.columns:
        for label, mask in (("up", df["regime_up"].astype(bool)),
                            ("down", ~df["regime_up"].astype(bool))):
            sub = df[mask]
            if not len(sub):
                by_regime[label] = {"n": 0}
                continue
            exc5 = sub["excess_5"].dropna().values
            rnet = sub["R_net"].dropna().values if "R_net" in sub else np.array([])
            pf_net_reg = _profit_factor(rnet) if len(rnet) else None
            by_regime[label] = {
                "n": int(len(sub)),
                "mean_excess_5d": round(float(np.nanmean(exc5)), 4) if len(exc5) else None,
                "hit_rate_5d": round(float((exc5 > 0).mean()), 3) if len(exc5) else None,
                "mean_R_net": round(float(rnet.mean()), 3) if len(rnet) else None,
                "profit_factor_net": round(pf_net_reg, 3) if pf_net_reg is not None else None,
            }

    mean_excess_5d = per_h.get("5", {}).get("mean_excess")
    t5 = per_h.get("5", {}).get("t_newey_west")
    verdict = _verdict(n_events, mean_excess_5d, t5)

    return {
        "setup": name,
        "n_events": n_events,
        "n_days": n_days,
        "hit_rate_5d": per_h.get("5", {}).get("hit_rate"),
        "excess": per_h,
        "mean_excess_5d": mean_excess_5d,
        "t_newey_west_5d": t5,
        "trade_sim": trade_stats,
        "profit_factor": trade_stats["profit_factor"],
        "profit_factor_net": trade_stats["profit_factor_net"],
        "mean_R_net": trade_stats["mean_R_net"],
        "by_regime": by_regime,  # ön-kayıtlı EMA50 dilimi — raporlama; verdict'e girmez
        "verdict": verdict,
        "params": setups_cfg.get(name),
    }
