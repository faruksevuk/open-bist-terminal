"""OTONOM SINAV — kâğıt portföy: sistem kendi sinyallerini SANAL defterde kendisi işler.

AMAÇ (kullanıcı hedefi 2026-07-17: "%7/hafta + ileride tam otonom trading"): tam otonomiye
giden yolun kanıt katmanı. Sistem her "al-adayı" sinyalini kendi kurallarıyla (sinyal-stop
sizing, heat tavanı, devre kesici, stop-önce çıkış) sanal parayla işler; haftalık gerçekleşen
% hedefle ve ölçülen kapasiteyle DÜRÜSTÇE kıyaslanır. Gerçek para/emir YOK — bu bir sınav
defteri: gerçek otonomi kararı bu karneden çıkar, histen değil.

KONVANSİYONLAR (event_study/simulate_trade_detail ile hizalı):
- Sinyal bugün kuyruğa girer, SONRAKİ barın OPEN'ında dolar (bakış-ileri yok).
- Fiyat uzayı ADJUSTED (temettü/bedelli hayalet stop üretmez); stop/target sinyalden.
- Stop-önce: bir barda low<=stop VE high>=target ise stop sayılır; open<stop → open'dan
  (gap-through) fill. Zaman çıkışı: giriş barı dahil time_exit_days barın CLOSE'u.
- Boyut: position_size(plan_stop=sinyal stopu) — base_r gerçekten işlem-başı risk olur.
- Devre kesici setups katmanında uygulanır (al-adayı → girme) → burada kendiliğinden etkir.

Durum config 'paper_state'te (JSON): tek-kullanıcı, gün-başına idempotent (last_step_day).
Backend kapalı geçen günlerde dolum ilk mevcut barın open'ına kayar (dürüst not: canlıda
böyle gün atlanmaz; kâğıtta yaklaşımdır).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.config_store import get_config, set_config
from app.data.history import load_daily
from app.risk.sizing import position_size

log = logging.getLogger(__name__)

_CLOSED_KEEP = 300
_EQ_KEEP = 600


def _adj_bars(session: Session, ticker: str) -> pd.DataFrame:
    """Adjusted OHLC (setup_outcomes ile aynı uzay). index=date."""
    df = load_daily(session, ticker)
    if df.empty:
        return df
    if "adj_close" in df.columns:
        f = (df["adj_close"] / df["close"]).astype(float).fillna(1.0)
        out = df.copy()
        for col in ("open", "high", "low", "close"):
            out[col] = df[col] * f
        return out
    return df


def _exit_check(entry: float, stop: float, target: float, bars_held: int,
                time_exit_days: int, o: float, h: float, low: float, c: float,
                ) -> tuple[str, float] | None:
    """Bir günün barında çıkış var mı? (status, fill) ya da None. SAF — test edilebilir.

    Kurallar simulate_trade_detail ile hizalı: gap-through open fill; stop-önce; zaman
    çıkışı bars_held (giriş barı=1) >= time_exit_days ise close'tan.
    """
    if o <= stop:
        return ("stop", o)          # açılış zaten stopun altında → open fill
    if low <= stop:
        return ("stop", stop)       # gün içinde stop (stop-önce kuralı: target'a bakılmaz)
    if h >= target:
        return ("target", target)
    if bars_held >= time_exit_days:
        return ("time_exit", c)
    return None


def _init_state(start_cash: float) -> dict:
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "start_cash": float(start_cash),
        "cash": float(start_cash),
        "last_step_day": None,
        "positions": [], "pending": [], "closed": [],
        "equity_hist": [],
    }


def _equity(state: dict, closes: dict[str, float]) -> float:
    eq = float(state["cash"])
    for p in state["positions"]:
        px = closes.get(p["ticker"], p["entry"])
        eq += p["qty"] * px
    return eq


def paper_step(session: Session) -> dict:
    """Bir adım: (yeni bar günüyse) dolum+çıkış+mark, her çağrıda yeni sinyal kuyruğu."""
    cfg = get_config(session, "auto_paper") or {}
    if not cfg.get("enabled", False):
        return {"enabled": False}
    start_cash = float(cfg.get("start_cash", 100_000.0))
    state = get_config(session, "paper_state") or _init_state(start_cash)

    from app.engine.event_digest import _latest_bar_date
    as_of = _latest_bar_date(session)
    if as_of is None:
        return {"enabled": True, "note": "bar yok"}
    day = as_of.isoformat()

    risk_cfg = get_config(session, "risk") or {}
    filled = exited = 0

    # ilgili barlar (pozisyon + bekleyen)
    tickers = {p["ticker"] for p in state["positions"]} | {p["ticker"] for p in state["pending"]}
    bars: dict[str, pd.DataFrame] = {t: _adj_bars(session, t) for t in tickers}

    def _row(t: str, d) -> pd.Series | None:
        df = bars.get(t)
        if df is None or df.empty:
            return None
        try:
            return df.loc[d] if d in df.index else None
        except (KeyError, TypeError):
            return None

    if state.get("last_step_day") != day:
        closes: dict[str, float] = {}
        # 1) BEKLEYEN DOLUMLAR — queued_day'den SONRAKİ ilk mevcut bar bugünse open'dan
        still_pending: list[dict] = []
        eq_now = _equity(state, {t: float(df["close"].iloc[-1]) for t, df in bars.items() if not df.empty})
        open_risk = sum(p["qty"] * (p["entry"] - p["stop"]) for p in state["positions"])
        for pen in state["pending"]:
            if pen["queued_day"] >= day:
                still_pending.append(pen)  # bugün kuyruklandı → yarın dolar
                continue
            row = _row(pen["ticker"], as_of)
            if row is None:
                still_pending.append(pen)  # bugün barı yok → bekle (birkaç gün sonra düşer)
                if (pd.Timestamp(day) - pd.Timestamp(pen["queued_day"])).days > 7:
                    still_pending.pop()  # bayat kuyruk — sessizce düşür
                continue
            fill = float(row["open"])
            if fill <= pen["stop"]:
                continue  # açılış stopun altında → girilmez (no_entry)
            sz = position_size(eq_now, fill, 0.0, risk_cfg,
                               open_heat_pct=(open_risk / eq_now if eq_now else 0.0),
                               plan_stop=pen["stop"])
            qty = int(sz.get("qty") or 0)
            cost = qty * fill
            if qty <= 0 or cost > state["cash"]:
                continue  # heat/nakit izin vermiyor → sinyal atlanır (kâğıtta da disiplin)
            state["cash"] -= cost
            open_risk += qty * (fill - pen["stop"])
            state["positions"].append({
                "ticker": pen["ticker"], "setup": pen["setup"], "key": pen["key"],
                "qty": qty, "entry": round(fill, 4), "stop": pen["stop"],
                "target": pen["target"], "time_exit_days": pen["time_exit_days"],
                "entry_day": day, "bars_held": 0,
            })
            filled += 1
        state["pending"] = still_pending

        # 2) ÇIKIŞLAR (bugünün barı; yeni dolanlar dahil — giriş barında stop/target olabilir)
        remaining: list[dict] = []
        for p in state["positions"]:
            row = _row(p["ticker"], as_of)
            if row is None:
                remaining.append(p)
                continue
            p["bars_held"] += 1
            res = _exit_check(p["entry"], p["stop"], p["target"], p["bars_held"],
                              p["time_exit_days"], float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]))
            closes[p["ticker"]] = float(row["close"])
            if res is None:
                remaining.append(p)
                continue
            status, fill = res
            state["cash"] += p["qty"] * fill
            risk = p["entry"] - p["stop"]
            r = (fill - p["entry"]) / risk if risk > 0 else 0.0
            state["closed"].append({
                "ticker": p["ticker"], "setup": p["setup"], "qty": p["qty"],
                "entry": p["entry"], "exit": round(fill, 4), "status": status,
                "r": round(r, 3), "pct": round(fill / p["entry"] - 1.0, 4),
                "entry_day": p["entry_day"], "exit_day": day,
            })
            exited += 1
        state["positions"] = remaining
        state["closed"] = state["closed"][-_CLOSED_KEEP:]

        # 3) MARK — bugünkü kapanışlarla sanal özsermaye
        for p in state["positions"]:
            if p["ticker"] not in closes:
                row = _row(p["ticker"], as_of)
                if row is not None:
                    closes[p["ticker"]] = float(row["close"])
        eq = _equity(state, closes)
        state["equity_hist"].append({"d": day, "eq": round(eq, 2)})
        state["equity_hist"] = state["equity_hist"][-_EQ_KEEP:]
        state["last_step_day"] = day

    # 4) YENİ SİNYALLER (her çağrıda; gün-içi rescore yakalansın) — al-adayı → kuyruk
    queued = 0
    try:
        from app.api.routes.setups import setups as setups_view
        data = setups_view(include_all=False, session=session)
        known = ({p.get("key") for p in state["positions"]}
                 | {p.get("key") for p in state["pending"]}
                 | {c.get("key") for c in state["closed"][-50:] if c.get("key")})
        held = {p["ticker"] for p in state["positions"]} | {p["ticker"] for p in state["pending"]}
        for it in data.get("setups", []):
            if it.get("advice") != "al-adayı":
                continue  # izle/girme (devre kesici dahil) kâğıtta da işlenmez
            if None in (it.get("entry_ref"), it.get("stop"), it.get("target")) or not it.get("time_exit_days"):
                continue
            key = f"{it['ticker']}:{it['setup']}:{it.get('triggered_at')}"
            if key in known or it["ticker"] in held:
                continue
            state["pending"].append({
                "ticker": it["ticker"], "setup": it["setup"], "key": key,
                "entry_ref": it["entry_ref"], "stop": it["stop"], "target": it["target"],
                "time_exit_days": int(it["time_exit_days"]), "queued_day": day,
            })
            queued += 1
    except Exception as exc:  # noqa: BLE001 — sinyal kuyruğu düşse adım kaybolmasın
        log.warning("paper sinyal kuyruğu hatası: %s", exc)

    set_config(session, "paper_state", state)
    session.commit()
    return {"enabled": True, "day": day, "filled": filled, "exited": exited,
            "queued": queued, "open": len(state["positions"]),
            "pending": len(state["pending"]), "cash": round(state["cash"], 2)}


def paper_stats(session: Session) -> dict:
    """Kâğıt karne: toplam/haftalık %, işlem istatistikleri, hedef & kapasite kıyası."""
    cfg = get_config(session, "auto_paper") or {}
    state = get_config(session, "paper_state")
    target = float((get_config(session, "goals") or {}).get("target_weekly_pct", 7.0))
    out: dict = {"enabled": bool(cfg.get("enabled", False)),
                 "start_cash": float(cfg.get("start_cash", 100_000.0)),
                 "target_weekly_pct": target}
    if not state:
        out["note"] = "Kâğıt portföy henüz adım atmadı — ilk skorlama/tarama koşumunda başlar."
        return out

    eq_hist = state.get("equity_hist") or []
    eq = eq_hist[-1]["eq"] if eq_hist else state.get("cash", out["start_cash"])
    start = float(state.get("start_cash") or out["start_cash"])
    closed = state.get("closed") or []
    wins = [c for c in closed if (c.get("r") or 0) > 0]
    rs = [c.get("r") or 0.0 for c in closed]

    weekly_pct = None
    if len(eq_hist) >= 2:
        d0 = pd.Timestamp(eq_hist[0]["d"])
        d1 = pd.Timestamp(eq_hist[-1]["d"])
        weeks = max((d1 - d0).days / 7.0, 1e-9)
        if weeks >= 0.9:  # ~1 haftadan kısa seride haftalık oran yanıltıcı olur
            weekly_pct = round(((eq / start) ** (1.0 / weeks) - 1.0) * 100.0, 3)

    max_dd = 0.0
    peak = -1e18
    for pt in eq_hist:
        peak = max(peak, pt["eq"])
        if peak > 0:
            max_dd = min(max_dd, pt["eq"] / peak - 1.0)

    out.update({
        "started_at": state.get("started_at"),
        "last_step_day": state.get("last_step_day"),
        "equity": round(eq, 2),
        "cash": round(state.get("cash", 0.0), 2),
        "total_pct": round((eq / start - 1.0) * 100.0, 2) if start else 0.0,
        "weekly_pct_realized": weekly_pct,   # None = seri henüz ~1 haftadan kısa
        "n_closed": len(closed),
        "hit_rate": round(len(wins) / len(closed), 3) if closed else None,
        "sum_r": round(sum(rs), 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "positions": state.get("positions") or [],
        "pending": state.get("pending") or [],
        "closed_tail": closed[-12:][::-1],
        "equity_hist": eq_hist[-120:],
        "note": ("SANAL defter — gerçek para/emir yok. Otonominin sınav karnesi: sistem her "
                 "al-adayını kendi kurallarıyla işler; hedef bu karneyle test edilir."),
    })
    return out
