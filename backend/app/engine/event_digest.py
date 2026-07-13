"""Gunluk "dikkat cekenler" ozeti — Trader-Brain'in girdisi (deterministik, AI'siz).

Bir trader'in sabah ekrana bakip "sunda haber var, su kirdi, su tavan olmus" demesinin
otomatik hali. Dagink sinyalleri (skor + setup + KAP olayi + fiyat/hacim hareketi) hisse
basina TEK "olay demeti"nde toplar ve **materiality** (onem) ile siralar. Ust siradakiler
gunluk AI butcesi (config ai_budget.daily_cap) dahilinde beyne gonderilir.

Katman haritasi (v0.2):
  - kap_events   : kurumsal olay (bedelli/geri-alim/finansal), LLM-yorumlu           [VAR]
  - setup_signals: olay-tetikli teknik setup (squeeze breakout, snapback...)         [VAR]
  - scores       : firsat skoru + sinyal + gate durumu (radar alaka duzeyi)          [VAR]
  - daily_bars   : bugunku getiri + hacim-z (setup'a girmeyen "dikkat ceken hareket") [ince, burada]

Bu modul HICBIR LLM cagrisi yapmaz — sadece neyin AI'a deger oldugunu deterministik secer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DailyBar, KapEvent, Score, SetupSignal

# Olculmus-kenari olan setup'lar (SETUPS v0.1: en iyi net PF): ekstra materiality agirligi.
_EDGE_SETUPS = {"squeeze_breakout", "htf_squeeze_breakout"}

# "Dikkat ceken hareket" esikleri (setup'a girmese de trader'in gozune carpan).
_MOVE_RET = 0.04      # |gunluk getiri| >= %4
_MOVE_VOLZ = 2.0      # hacim >= trailing-20 ort + 2 sigma


@dataclass
class EventBundle:
    """Bir hissenin bir gunku tum dikkat-ceken olaylari + onem skoru."""

    ticker: str
    as_of: date
    materiality: float = 0.0
    reasons: list[str] = field(default_factory=list)
    # ham parcalar (beyin prompt'u + dashboard bunlari kullanir)
    corporate: list[dict] = field(default_factory=list)   # KAP olaylari
    technical: list[dict] = field(default_factory=list)    # setup sinyalleri
    move: dict | None = None                               # {ret, vol_z, close}
    stance: dict | None = None                             # {score, signal, gates, meets}


def _latest_bar_date(session: Session) -> date | None:
    return session.execute(select(func.max(DailyBar.date))).scalar()


def _moves(session: Session, as_of: date) -> dict[str, dict]:
    """Her ticker icin bugunku getiri + hacim-z (trailing 20g). daily_bars'tan tek gecis."""
    since = as_of - timedelta(days=40)  # ~27 islem gunu; 20g pencere + bugun icin yeterli
    rows = session.execute(
        select(DailyBar.ticker, DailyBar.date, DailyBar.close, DailyBar.volume)
        .where(DailyBar.date >= since)
        .order_by(DailyBar.ticker, DailyBar.date)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["ticker", "date", "close", "volume"])
    out: dict[str, dict] = {}
    for tkr, g in df.groupby("ticker", sort=False):
        g = g.dropna(subset=["close"])
        if len(g) < 2:
            continue
        last = g.iloc[-1]
        if last["date"] != as_of:
            continue  # bu ismin bugun bari yok (KOZAL gibi bayat) -> hareket olayi uretme
        prev_close = g.iloc[-2]["close"]
        ret = (last["close"] / prev_close - 1.0) if prev_close else 0.0
        vol_hist = g["volume"].iloc[:-1].tail(20)
        vz = 0.0
        if len(vol_hist) >= 5 and vol_hist.std(ddof=0) > 0:
            vz = float((last["volume"] - vol_hist.mean()) / vol_hist.std(ddof=0))
        out[tkr] = {"ret": float(ret), "vol_z": vz, "close": float(last["close"])}
    return out


def _active_setups(session: Session, as_of: date) -> dict[str, list[dict]]:
    """Bugun hala gecerli (valid_until >= as_of) aktif setup sinyalleri, ticker -> [setup...]."""
    rows = session.execute(
        select(SetupSignal).where(
            SetupSignal.active.is_(True),
            SetupSignal.valid_until >= as_of,
        )
    ).scalars().all()
    out: dict[str, list[dict]] = {}
    for s in rows:
        out.setdefault(s.ticker, []).append({
            "setup": s.setup, "strength": s.strength or 0.0,
            "triggered_at": s.triggered_at, "entry_ref": s.entry_ref,
            "stop": s.stop, "target": s.target, "time_exit_days": s.time_exit_days,
        })
    return out


def _active_kap(session: Session, now: datetime) -> dict[str, list[dict]]:
    """Etkisi suren, yorumlanmis KAP olaylari, ticker -> [olay...] (cok-hisse patlatilir)."""
    events = session.execute(
        select(KapEvent).where(
            KapEvent.interpreted.is_(True),
            KapEvent.effective_until > now,
        )
    ).scalars().all()
    out: dict[str, list[dict]] = {}
    for e in events:
        payload = {
            "type": e.type.value if e.type else "diger",
            "direction": e.direction or 0.0, "magnitude": e.magnitude or 0.0,
            "confidence": e.confidence or 0.0, "title": e.title,
            "published_at": e.published_at, "mechanism": e.mechanism,
        }
        for t in (e.tickers or []):
            out.setdefault(t, []).append(payload)
    return out


def _latest_scores(session: Session) -> dict[str, dict]:
    """Her ticker'in en guncel skor satiri, ticker -> {score, signal, gates, meets}."""
    mx = (
        select(Score.ticker, func.max(Score.as_of).label("m"))
        .group_by(Score.ticker)
        .subquery()
    )
    rows = session.execute(
        select(Score.ticker, Score.score, Score.signal,
               Score.passed_gates, Score.meets_absolute_threshold)
        .join(mx, (Score.ticker == mx.c.ticker) & (Score.as_of == mx.c.m))
    ).all()
    out: dict[str, dict] = {}
    for t, sc, sig, pg, meets in rows:
        out[t] = {
            "score": float(sc) if sc is not None else None,
            "signal": sig.value if hasattr(sig, "value") else sig,
            "gates": bool(pg), "meets": bool(meets),
        }
    return out


def _materiality(b: EventBundle) -> None:
    """Olay demetine 0-100 onem skoru + insan-okur gerekce yaz (deterministik)."""
    m = 0.0
    reasons: list[str] = []

    # 1) Kurumsal olay (KAP) — en yuksek sinyal. |yon*buyukluk*guven| -> 0..40
    if b.corporate:
        ce = max(b.corporate, key=lambda e: abs(e["direction"] * e["magnitude"] * e["confidence"]))
        contrib = abs(ce["direction"] * ce["magnitude"] * ce["confidence"]) * 40.0
        if contrib > 0.5:
            m += contrib
            yon = "pozitif" if ce["direction"] > 0 else ("negatif" if ce["direction"] < 0 else "notr")
            reasons.append(f"KAP: {ce['type']} ({yon})")

    # 2) Teknik setup — GUNCEL durumu yansit: bayat/basarisiz kirilim manseti sismasin.
    #    Sinyal gunler once tetiklenmis olabilir; fiyat girisin/stop'un altina dustuyse
    #    kirilim OLDU ve basarisiz -> tetik-anindaki gucuyle one cikmasin (Faruk'un yakaladigi bug).
    if b.technical:
        s = max(b.technical, key=lambda x: x["strength"])
        edge = 1.3 if s["setup"] in _EDGE_SETUPS else 1.0
        contrib = (s["strength"] / 100.0) * 25.0 * edge
        cur = b.move["close"] if b.move else None
        entry, stop = s.get("entry_ref"), s.get("stop")
        state = ""
        if cur and entry:
            chg = cur / entry - 1.0
            if stop and cur < stop:                # stop yendi -> kirilim basarisiz
                contrib *= 0.15
                state = f" (stop yedi {chg * 100:+.0f}%)"
            elif chg <= -0.02:                     # girisin altina dondu
                contrib *= 0.45
                state = f" (geri dondu {chg * 100:+.0f}%)"
            elif chg >= 0.02:                      # kirilim tutuyor
                state = f" (giris ustu +{chg * 100:.0f}%)"
        m += contrib
        reasons.append(f"Setup: {s['setup']} (guc {s['strength']:.0f}){state}")

    # 3) Dikkat ceken hareket — setup'a girmeyen fiyat/hacim. 0..20
    if b.move:
        ret, vz = b.move["ret"], b.move["vol_z"]
        if abs(ret) >= _MOVE_RET or vz >= _MOVE_VOLZ:
            contrib = min(20.0, abs(ret) * 100.0 * 1.2 + max(0.0, vz) * 3.0)
            m += contrib
            reasons.append(f"Hareket: {ret:+.1%}, hacim {vz:+.1f}s")

    # 4) Radar alaka — zaten dikkatte olan isme trader daha cok kafa yorar. 0..~20
    if b.stance and b.stance["score"] is not None:
        if b.stance["meets"]:
            m += 12.0
            reasons.append("radar: esik-ustu")
        elif b.stance["gates"]:
            m += 7.0
            reasons.append("radar: gate-gecti")
        m += max(-8.0, min(8.0, (b.stance["score"] - 50.0) / 50.0 * 8.0))

    b.materiality = round(min(100.0, m), 1)
    b.reasons = reasons


def daily_event_digest(
    session: Session, as_of: date | None = None, top_n: int | None = None,
    min_materiality: float = 8.0,
) -> list[EventBundle]:
    """Bugunku tum dikkat-ceken olaylari hisse basina topla + onem'e gore sirala.

    Sadece BIR olay-kaynagi (KAP / setup / dikkat-ceken hareket) olan isimler dahil edilir;
    salt yuksek-skor "olay" degildir (radar sadece agirlik verir). min_materiality alti elenir.
    top_n verilirse ilk N doner (AI butcesi = ai_budget.daily_cap ile eslesir).
    """
    as_of = as_of or _latest_bar_date(session)
    if as_of is None:
        return []
    now = datetime.now(timezone.utc)

    moves = _moves(session, as_of)
    setups = _active_setups(session, as_of)
    kap = _active_kap(session, now)
    scores = _latest_scores(session)

    # Olay-kaynagi olan tum ticker'lar (skor tek basina olay saymaz).
    move_tickers = {t for t, mv in moves.items()
                    if abs(mv["ret"]) >= _MOVE_RET or mv["vol_z"] >= _MOVE_VOLZ}
    candidates = set(kap) | set(setups) | move_tickers

    bundles: list[EventBundle] = []
    for t in candidates:
        b = EventBundle(ticker=t, as_of=as_of)
        b.corporate = kap.get(t, [])
        b.technical = setups.get(t, [])
        b.move = moves.get(t)
        b.stance = scores.get(t)
        _materiality(b)
        if b.materiality >= min_materiality:
            bundles.append(b)

    bundles.sort(key=lambda x: x.materiality, reverse=True)
    return bundles[:top_n] if top_n else bundles
