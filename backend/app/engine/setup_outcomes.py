"""Canlı sinyal sonuç-takibi (OOS) — SETUPS v0.1 kanıt-biriktirme katmanı.

Her ateşlenen SetupSignal'a ne OLDUĞUNU (target/stop/time_exit/no_entry) izler; böylece
canlı (out-of-sample) DÜRÜST beklenti/isabet birikir. "zayıf/deneysel" verdict'lerinden
"kanıtlı"ya giden TEK yol budur — event-study prior'ı zaten koştu, gerçek edge ancak canlı
sonuçlarla teyit edilir.

Konvansiyon (event_study._simulate_trade ile BİREBİR — mantık çatallanmaz):
- Giriş = tetik (triggered_at) barından SONRAKİ ilk barın OPEN'ı (adjusted seri, load_daily).
- Muhafazakâr STOP-ÖNCE: bir bar low<=stop AND high>=target ise ÖNCE stop sayılır.
- Zaman-çıkışı = giriş barı + time_exit_days barının CLOSE'u.
- R = (exit - entry) / (entry - planlı_stop).
- Giriş barı henüz oluşmadıysa → 'pending' (kalır, sonraki koşumda tekrar denenir).
- Giriş oldu ama zaman-çıkışına kadarki barlar tam gelmedi ve stop/target yoksa → 'pending'.

evaluate_outcomes(session): final olmayan (satır yok VEYA status='pending') her sinyali
yeniden değerlendirir; SetupOutcome'a upsert eder.
outcome_summary(session): setup-başına + genel toplulaştırma + DÜRÜST beklenti bloğu.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from statistics import median

import pandas as pd
from sqlalchemy import select
from app.db.upsert import upsert
from sqlalchemy.orm import Session

from app.backtest.event_study import simulate_trade_detail
from app.config_store import get_config
from app.data.history import load_daily
from app.db.models import SetupOutcome, SetupSignal
from app.engine.setups import SETUP_LABELS

log = logging.getLogger(__name__)

# kapandı (final) sayılan statüler — bunlara sahip sinyal yeniden değerlendirilmez.
_FINAL = {"target", "stop", "time_exit", "no_entry"}
_CLOSED_R = {"target", "stop", "time_exit"}  # R üreten (no_entry hariç) kapalı statüler


def _evaluate_one(sig: SetupSignal, bars: pd.DataFrame) -> dict | None:
    """Bir sinyalin tetik-sonrası barlarını yürü → sonuç dict (upsert için) veya None.

    bars: load_daily çıktısı (tarih-indeksli OHLC; adjusted yok — ham OHLC yeterli çünkü
    stop/target ham fiyat düzeyinde). None döner → değerlendirilemedi (bar yok / stop bozuk).
    """
    if bars is None or bars.empty:
        return None
    if sig.stop is None or sig.target is None or sig.time_exit_days is None:
        return None

    # tetik barından SONRAKİ ilk bar = giriş barı (triggered_at'ten büyük ilk tarih)
    idx = bars.index
    trig = sig.triggered_at
    # tarih indeksini date'e indirger (load_daily index'i date; garantiye al)
    dates = [d.date() if isinstance(d, (pd.Timestamp, datetime)) else d for d in idx]
    entry_pos = None
    for k, d in enumerate(dates):
        if d > trig:
            entry_pos = k
            break

    o = bars["open"].reset_index(drop=True)
    h = bars["high"].reset_index(drop=True)
    low = bars["low"].reset_index(drop=True)
    c = bars["close"].reset_index(drop=True)

    if entry_pos is None:
        # tetik-sonrası bar HENÜZ yok → giriş beklemede (BIST henüz kapanmamış olabilir)
        return {
            "signal_id": sig.id, "ticker": sig.ticker, "setup": sig.setup,
            "triggered_at": sig.triggered_at, "entry_date": None, "entry_price": None,
            "status": "pending", "exit_date": None, "exit_price": None,
            "realized_r": None, "realized_pct": None, "days_held": None,
        }

    res = simulate_trade_detail(
        o, h, low, c, entry_pos, float(sig.stop), float(sig.target), int(sig.time_exit_days),
    )

    entry_date = dates[res.entry_index] if res.entry_index is not None else None
    exit_date = dates[res.exit_index] if res.exit_index is not None else None
    realized_pct = None
    if (res.entry_price is not None and res.exit_price is not None
            and res.entry_price not in (0, None)):
        realized_pct = float(res.exit_price / res.entry_price - 1.0)

    return {
        "signal_id": sig.id, "ticker": sig.ticker, "setup": sig.setup,
        "triggered_at": sig.triggered_at,
        "entry_date": entry_date, "entry_price": res.entry_price,
        "status": res.status,
        "exit_date": exit_date, "exit_price": res.exit_price,
        "realized_r": res.r_multiple, "realized_pct": realized_pct,
        "days_held": res.days_held,
    }


def evaluate_outcomes(session: Session) -> dict:
    """Final olmayan her sinyal için sonucu (yeniden) değerlendir → SetupOutcome upsert.

    Değerlendirilecekler: SetupOutcome satırı OLMAYAN sinyaller + status='pending' olanlar.
    (target/stop/time_exit/no_entry final; bir daha dokunulmaz.) Döner: özet sayaçlar.
    """
    # mevcut sonuç durumları (signal_id → status)
    existing = {
        sid: status
        for sid, status in session.execute(
            select(SetupOutcome.signal_id, SetupOutcome.status)
        ).all()
    }

    sigs = session.execute(select(SetupSignal)).scalars().all()
    # bar önbelleği (aynı ticker'ı bir kez oku)
    bars_cache: dict[str, pd.DataFrame] = {}

    n_eval = n_pending = n_filled = n_closed = n_no_entry = 0
    upsert_rows: list[dict] = []

    for sig in sigs:
        prev = existing.get(sig.id)
        if prev in _FINAL:
            continue  # kapanmış → dokunma
        n_eval += 1
        if sig.ticker not in bars_cache:
            bars_cache[sig.ticker] = load_daily(session, sig.ticker)
        row = _evaluate_one(sig, bars_cache[sig.ticker])
        if row is None:
            continue
        upsert_rows.append(row)
        st = row["status"]
        if st == "pending":
            n_pending += 1
            if row["entry_date"] is not None:
                n_filled += 1  # giriş oldu ama henüz kapanmadı
        elif st == "no_entry":
            n_no_entry += 1
        elif st in _CLOSED_R:
            n_closed += 1
            n_filled += 1

    now = datetime.now(timezone.utc)
    for row in upsert_rows:
        row["evaluated_at"] = now
        stmt = upsert(SetupOutcome).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SetupOutcome.signal_id],
            set_={
                "entry_date": stmt.excluded.entry_date,
                "entry_price": stmt.excluded.entry_price,
                "status": stmt.excluded.status,
                "exit_date": stmt.excluded.exit_date,
                "exit_price": stmt.excluded.exit_price,
                "realized_r": stmt.excluded.realized_r,
                "realized_pct": stmt.excluded.realized_pct,
                "days_held": stmt.excluded.days_held,
                "evaluated_at": stmt.excluded.evaluated_at,
            },
        )
        session.execute(stmt)
    session.commit()

    return {
        "evaluated": n_eval, "pending": n_pending, "filled": n_filled,
        "closed": n_closed, "no_entry": n_no_entry, "upserts": len(upsert_rows),
    }


# --- toplulaştırma / beklenti -------------------------------------------

# İşlem maliyeti varsayılanı (config 'costs' yoksa fallback — seed_config ile birebir).
_DEF_COSTS = {"commission_pct_per_side": 0.0004, "spread_slippage_pct_per_side": 0.0010}


def _round_trip_cost_pct(session: Session) -> float:
    """Config 'costs' → round-trip maliyet fraksiyonu = 2×(komisyon+slippage)."""
    c = get_config(session, "costs") or _DEF_COSTS
    commission = float(c.get("commission_pct_per_side", _DEF_COSTS["commission_pct_per_side"]))
    slippage = float(c.get("spread_slippage_pct_per_side", _DEF_COSTS["spread_slippage_pct_per_side"]))
    return 2.0 * (commission + slippage)


def _net_of(realized_r: float | None, realized_pct: float | None,
            cost_pct: float) -> tuple[float | None, float | None]:
    """Saklı GROSS (realized_r, realized_pct) + koşum-anı maliyeti → (net_r, net_pct).

    NET TÜRETİLİR, saklanmaz (retro tarife değişimi temiz yeniden-hesaplanır — özellik).
    - net_pct = realized_pct − cost_pct (fiyat-getiri uzayı).
    - net_r  = realized_r − cost_R;  cost_R = cost_pct × entry/(entry−stop).
      Saklı stop yok ama risk-fraksiyonu = realized_pct/realized_r (event_study R matematiği:
      realized_pct=(exit−entry)/entry, realized_r=(exit−entry)/(entry−stop)) → türetilir:
        cost_R = cost_pct × realized_r / realized_pct   ( = cost_pct / risk_fraksiyonu ).

    MALİYET-FİZİBİLİTE (simulate_trade_detail ile birebir): risk-fraksiyonu round-trip
    maliyeti aşmıyorsa işlem girilemez → net_r=None (net-R toplamasından düşer). net_pct
    yine maliyeti yansıtır. realized_pct==0 (tam başabaş) dejenere hal: risk-fraksiyonu
    türetilemez → net_r=None; net_pct=−cost_pct.
    """
    if realized_r is None or realized_pct is None:
        return realized_r, realized_pct
    net_pct = realized_pct - cost_pct
    if cost_pct <= 0.0:
        return realized_r, net_pct  # maliyet yok → net == gross
    if realized_pct == 0 or realized_r == 0:
        return None, net_pct  # dejenere: risk-fraksiyonu türetilemez → net_r anlamsız
    risk_frac = realized_pct / realized_r
    if risk_frac <= cost_pct:  # maliyet 1R risk bütçesini yer → girilemez işlem
        return None, net_pct
    cost_r = cost_pct * realized_r / realized_pct
    return realized_r - cost_r, net_pct


def _agg_group(rows: list[SetupOutcome], cost_pct: float = 0.0) -> dict:
    """Bir grup (setup veya genel) SetupOutcome satırından istatistik (GROSS + NET).

    `cost_pct` = round-trip maliyet fraksiyonu; net R/pct ON-THE-FLY türetilir (saklanmaz).
    cost_pct=0 → net == gross (varsayılan davranış değişmez).
    """
    closed = [r for r in rows if r.status in _CLOSED_R]
    pending = [r for r in rows if r.status == "pending"]
    no_entry = [r for r in rows if r.status == "no_entry"]
    rs = [r.realized_r for r in closed if r.realized_r is not None]
    pcts = [r.realized_pct for r in closed if r.realized_pct is not None]
    held = [r.days_held for r in closed if r.days_held is not None]
    # net (türetilmiş): gross r+pct ikisi de dolu olan kapalılar
    nets = [_net_of(r.realized_r, r.realized_pct, cost_pct)
            for r in closed if r.realized_r is not None and r.realized_pct is not None]
    rs_net = [nr for nr, _ in nets if nr is not None]
    pcts_net = [npct for _, npct in nets if npct is not None]
    n_target = sum(1 for r in closed if r.status == "target")
    n_stop = sum(1 for r in closed if r.status == "stop")
    n_time = sum(1 for r in closed if r.status == "time_exit")
    n_closed = len(closed)
    return {
        "n_closed": n_closed,
        "n_pending": len(pending),
        "n_no_entry": len(no_entry),
        "n_target": n_target,
        "n_stop": n_stop,
        "n_time_exit": n_time,
        # isabet = R>0 oranı (kapalılar içinde); ayrıca target-oranı
        "isabet": round(sum(1 for r in rs if r > 0) / n_closed, 3) if n_closed else None,
        "target_rate": round(n_target / n_closed, 3) if n_closed else None,
        "stop_rate": round(n_stop / n_closed, 3) if n_closed else None,
        "ort_r": round(_mean(rs), 3) if rs else None,
        "medyan_r": round(float(median(rs)), 3) if rs else None,
        "toplam_r": round(float(sum(rs)), 3) if rs else None,
        "ort_pct": round(_mean(pcts), 4) if pcts else None,
        "ort_gun": round(_mean([float(x) for x in held]), 1) if held else None,
        # NET (maliyet sonrası; türetilmiş) — friction realizmi
        "ort_r_net": round(_mean(rs_net), 3) if rs_net else None,
        "medyan_r_net": round(float(median(rs_net)), 3) if rs_net else None,
        "toplam_r_net": round(float(sum(rs_net)), 3) if rs_net else None,
        "ort_pct_net": round(_mean(pcts_net), 4) if pcts_net else None,
        "isabet_net": round(sum(1 for r in rs_net if r > 0) / len(rs_net), 3) if rs_net else None,
    }


def _mean(xs: list[float]) -> float | None:
    return float(sum(xs) / len(xs)) if xs else None


def _weeks_span(rows: list[SetupOutcome]) -> float:
    """Kapalı işlemlerin triggered_at aralığını HAFTA cinsinden ölç (>=1 hafta tabanı)."""
    trigs = [r.triggered_at for r in rows if r.status in _CLOSED_R and r.triggered_at]
    if len(trigs) < 2:
        # tek gün / tek işlem → 1 hafta say (sıfıra bölme yok, temkinli)
        return 1.0
    span_days = (max(trigs) - min(trigs)).days
    return max(span_days / 7.0, 1.0)


def outcome_summary(session: Session) -> dict:
    """Setup-başına + genel + DÜRÜST beklenti bloğu (GROSS + NET, net TÜRETİLMİŞ).

    Beklenti (risk_per_trade = config risk.base_r, varsayılan 0.01):
      olculen_r_per_week      = toplam_r / hafta_aralığı  (kapalı işlemler; GROSS)
      olculen_r_per_week_net  = toplam_r_net / hafta_aralığı  (round-trip maliyet düşülmüş)
      beklenen_haftalik_pct   = olculen_r_per_week × base_r × 100 (net karşılığı da hesaplanır)
      gereken_r_per_week      = 0.10 / base_r  (%10/hafta için → base_r=0.01'de 10R/hafta;
                                 bu gereken RAKAM maliyeti YOK SAYAR → gerçek açık daha büyük)
      target_gap = gereken - ölçülen_NET (numeric); gap_note NET'e dayanır.
    """
    rows = session.execute(select(SetupOutcome)).scalars().all()

    cost_pct = _round_trip_cost_pct(session)  # koşum-anı maliyeti (net türetilir, saklanmaz)

    per_setup: dict[str, dict] = {}
    by_setup: dict[str, list[SetupOutcome]] = {}
    for r in rows:
        by_setup.setdefault(r.setup, []).append(r)
    for name, grp in by_setup.items():
        d = _agg_group(grp, cost_pct)
        d["setup"] = name
        d["setup_label"] = SETUP_LABELS.get(name, name)
        per_setup[name] = d

    overall = _agg_group(rows, cost_pct)

    risk_cfg = get_config(session, "risk") or {}
    base_r = float(risk_cfg.get("base_r", 0.01))

    n_closed = overall["n_closed"]
    sum_r = overall["toplam_r"] or 0.0
    sum_r_net = overall["toplam_r_net"] or 0.0
    mean_r = overall["ort_r"]
    mean_r_net = overall["ort_r_net"]
    weeks = _weeks_span(rows)
    trades_per_week = round(n_closed / weeks, 3) if n_closed else 0.0
    measured_r_per_week = round(sum_r / weeks, 3) if n_closed else 0.0
    measured_r_per_week_net = round(sum_r_net / weeks, 3) if n_closed else 0.0
    expected_weekly_pct = round(measured_r_per_week * base_r * 100.0, 3) if n_closed else 0.0
    expected_weekly_pct_net = round(measured_r_per_week_net * base_r * 100.0, 3) if n_closed else 0.0
    target_weekly_pct = 10.0
    # gereken RAKAM maliyeti yok sayar (net-of-nothing); gerçek açık daha da büyüktür.
    needed_r_per_week = round(target_weekly_pct / 100.0 / base_r, 2)  # base_r=0.01 → 10R
    gap = round(needed_r_per_week - measured_r_per_week_net, 2)  # NET açık

    if n_closed == 0:
        gap_note = ("Henüz kapanan sinyal yok — canlı beklenti ölçülemiyor; sonuçlar "
                    "biriktikçe dürüst rakam çıkacak.")
    else:
        gap_note = (
            f"NET ~%{expected_weekly_pct_net:.2f}/hafta ({measured_r_per_week_net:.2f}R/hafta, "
            f"brüt %{expected_weekly_pct:.2f}; {n_closed} kapalı işlem, ort NET "
            f"{(mean_r_net if mean_r_net is not None else 0):.2f}R / brüt {mean_r:.2f}R); "
            f"%{target_weekly_pct:.0f}/hafta için {needed_r_per_week:.0f}R/hafta gerekli "
            f"(bu gereken rakam maliyeti YOK SAYAR → gerçek açık daha büyük) — "
            f"NET açık {gap:.1f}R/hafta. Bu fark yapısal: hedef mevcut edge'in çok üstünde."
        )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cost_note": ("Maliyet TÜRETİLİR (saklanmaz): net = brüt − round-trip "
                      "(komisyon+spread), config → costs. Retro tarife değişimi temiz "
                      "yeniden-hesaplanır."),
        "round_trip_cost_pct": round(cost_pct, 6),
        "per_setup": per_setup,
        "overall": overall,
        "expectancy": {
            "risk_per_trade": base_r,
            "n_closed": n_closed,
            "weeks_span": round(weeks, 2),
            "trades_per_week": trades_per_week,
            "mean_r": mean_r,
            "mean_r_net": mean_r_net,
            "measured_r_per_week": measured_r_per_week,
            "measured_r_per_week_net": measured_r_per_week_net,
            "expected_weekly_pct": expected_weekly_pct,
            "expected_weekly_pct_net": expected_weekly_pct_net,
            "target_weekly_pct": target_weekly_pct,
            "needed_r_per_week": needed_r_per_week,
            "needed_ignores_cost": True,
            "gap": gap,
            "gap_note": gap_note,
        },
    }
