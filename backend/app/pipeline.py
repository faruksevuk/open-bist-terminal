"""Paylaşılan orkestrasyon (pipeline) — script'ler VE otonom scheduler AYNI fonksiyonları
çağırır; mantık çatallanmaz (simulate_trade_detail deseniyle aynı ilke).

Her adım kendi özet dict'ini döner; print/log üst katmanın işi. Kısmi hatalara dayanıklı:
bir adım patlarsa rollback edilir ve özet 'error' taşır — önceki adımların yazımı korunur
(run_scoring.py'nin try/except davranışı buraya taşındı).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.history import fetch_history
from app.db.models import Horizon, Position, Score
from app.engine.scoring import persist_scores, score_universe
from app.engine.sector_macro import store_market_context
from app.engine.setup_outcomes import evaluate_outcomes
from app.engine.setup_scan import scan_universe

log = logging.getLogger(__name__)


def refresh_data(session: Session, period: str = "1mo",
                 tickers: list[str] | None = None) -> dict:
    """yfinance günlük barları upsert et. Gecelik tazeleme için '1mo' yeterli (upsert)."""
    res = fetch_history(session, tickers=tickers, period=period)
    total = sum(res.values())
    ok = sum(1 for v in res.values() if v > 0)
    return {"tickers": len(res), "with_bars": ok, "bars_written": total}


def refresh_scores(session: Session) -> dict:
    """Skorla → setup tara → makro/sektör bağlam → canlı sonuç-takibi (tek geçiş)."""
    out: dict = {}

    df = score_universe(session)
    if df.empty:
        out["scores"] = {"written": 0, "note": "skorlanacak isim yok (daily_bars boş?)"}
        return out
    n = persist_scores(session, df)
    out["scores"] = {
        "written": n,
        "gated": int(df["passed_gates"].sum()),
        "meets": int(df["meets_absolute_threshold"].sum()),
        "threshold_eff": round(float(df.attrs.get("abs_threshold_eff", 0.0)), 1),
    }

    try:
        sig = scan_universe(session)
        out["setups"] = {
            "signals": int(len(sig)),
            "by_setup": sig.groupby("setup").size().to_dict() if not sig.empty else {},
        }
    except Exception as exc:  # noqa: BLE001 — tarama patlasa skorlar yazıldı
        session.rollback()
        out["setups"] = {"error": str(exc)}

    try:
        ctx = store_market_context(session)
        if ctx:
            m = ctx["macro"]
            out["context"] = {"regime": m["regime"], "regime_score": m["regime_score"],
                              "breadth_ema50": m["breadth_ema50"]}
        else:
            out["context"] = {"note": "yeterli seri yok"}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        out["context"] = {"error": str(exc)}

    try:
        out["outcomes"] = evaluate_outcomes(session)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        out["outcomes"] = {"error": str(exc)}

    return out


def watchlist(session: Session, limit: int = 25) -> list[str]:
    """KAP/AI bütçesi için izleme listesi: eşik-geçen son skorlar + açık pozisyonlar."""
    as_of = session.execute(
        select(Score.as_of).where(Score.horizon == Horizon.swing)
        .order_by(Score.as_of.desc()).limit(1)
    ).scalar()
    tickers: list[str] = []
    if as_of:
        rows = session.execute(
            select(Score.ticker).where(
                Score.horizon == Horizon.swing, Score.as_of == as_of,
                Score.meets_absolute_threshold.is_(True),
            ).order_by(Score.score.desc()).limit(limit)
        ).scalars().all()
        tickers.extend(rows)
    for p in session.execute(select(Position.ticker)).scalars().all():
        if p not in tickers:
            tickers.append(p)
    return tickers


def poll_news(session: Session, limit: int = 25) -> dict:
    """KAP çek + Gemini yorumla (watchlist-scoped). Key yoksa dürüst no-op."""
    from app.llm import gemini_client
    from app.news.events import poll

    if not gemini_client.available():
        return {"skipped": "Gemini key yok (backend/.env GEMINI_API_KEY_1..4)", "stored": 0}
    wl = watchlist(session, limit)
    res = poll(session, wl)
    return {"watchlist": len(wl), **res}


def refresh_narrative(session: Session) -> dict:
    """Trader-Brain: grounded analist tezleri üret + vadesi dolanları notla (karne).

    Gemini + grounding gerektirir; anahtar/kota yoksa zarifçe atlar (sistem devam).
    Önce eski tezleri notla (deterministik), sonra yeni tez üret (bütçe-gate'li).
    """
    from app.engine.thesis_grade import evaluate_theses
    from app.llm.brain import generate_brief
    from app.llm.narrative import generate_theses

    out: dict = {}
    try:
        out["graded"] = evaluate_theses(session)   # deterministik — her zaman koşar
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        out["graded"] = {"error": str(exc)}
    try:
        # AI Brain — portföy değerlendirmesi (deterministik defter + bütçe varsa AI sentezi)
        brief = generate_brief(session)
        out["brain"] = {"holdings": len(brief["facts"]["holdings"]),
                        "candidates": len(brief["facts"]["candidates"]),
                        "ai": brief["ai"] is not None}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        out["brain"] = {"error": str(exc)}
    try:
        out["generated"] = generate_theses(session)  # grounded tez — key/kota yoksa atlar
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        out["generated"] = {"error": str(exc)}
    return out


def refresh_fundamentals_targeted(session: Session, days: int = 5) -> dict:
    """Son N günde finansal_tablo KAP olayı olan isimlerin F-Score+SUE'sunu tazele.

    PEAD taze bilanço ister: yeni açıklama yapan isim gecelik işte HEMEN güncellenir
    (tam evren taraması haftalık — burada yalnız yeni açıklama yapanlar; genelde 0-20 isim).
    KAP kaynağı erişilemiyorsa liste boş kalır — dürüst no-op, haftalık sweep telafi eder.
    """
    from app.db.models import KapEvent, KapType
    from app.engine.fscore import populate_fundamentals

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(KapEvent.tickers).where(
            KapEvent.type == KapType.finansal_tablo,
            KapEvent.published_at >= since,
        )
    ).all()
    tickers = sorted({t.upper() for (tks,) in rows for t in (tks or [])})
    if not tickers:
        return {"tickers": 0, "note": "taze finansal_tablo KAP olayı yok"}
    res = populate_fundamentals(session, tickers)
    ok = sum(1 for v in res.values() if v is not None)
    return {"tickers": len(tickers), "f_computed": ok}


def refresh_fundamentals_full(session: Session) -> dict:
    """TÜM evrenin F-Score+SUE'sunu yeniden hesapla (haftalık; isyatirim ticker-başı, yavaş)."""
    from app.db.models import Security
    from app.engine.fscore import populate_fundamentals

    tickers = list(session.execute(
        select(Security.ticker).where(Security.excluded.is_(False))).scalars().all())
    res = populate_fundamentals(session, tickers)
    ok = sum(1 for v in res.values() if v is not None)
    return {"tickers": len(tickers), "f_computed": ok}


def refresh_valuation(session: Session, full: bool = True) -> dict:
    """PE/PB tazele (fast_info; RESUME-SAFE — ticker başına commit).

    full=True → hepsi yeniden çekilir (fiyat değişti, oran bayatladı); False → yalnız
    boş olanlar doldurulur. Haftalık işte full koşulur.
    """
    from app.data.quotes import populate_valuation
    from app.db.models import Security

    tickers = list(session.execute(
        select(Security.ticker).where(Security.excluded.is_(False))).scalars().all())
    updated = populate_valuation(session, tickers, skip_existing=not full)
    return {"tickers": len(tickers), "updated": updated}


def weekly_calibrate(session: Session) -> dict:
    """Faktör IC'lerini ölç (IC×0.5 deflate; calibration.py).

    require_oos_for_weight_change (config 'calibration', varsayılan True) ise CANLI
    'factor_weights'i EZMEZ — önerileri 'factor_weights_suggested' altına yazar (kullanıcı
    /config'ten inceleyip uygular). Böylece haftalık job kullanıcının ağırlık tercihini
    sessizce silmez (eski davranış: her Cmt üzerine yazıyordu). Flag False ise doğrudan uygular.
    """
    from app.backtest.calibration import calibrate_factor_weights, store_factor_diagnostic
    from app.config_store import get_config, set_config

    # UI 'Ayarlar' sekmesi için ölçülen IC/t/isabet'i tazele (config factor_diagnostic)
    try:
        store_factor_diagnostic(session)
    except Exception:  # noqa: BLE001 — diagnostic patlasa kalibrasyon yine koşsun
        session.rollback()

    require_oos = bool((get_config(session, "calibration") or {}).get(
        "require_oos_for_weight_change", True))
    res = calibrate_factor_weights(session, write=not require_oos)
    if require_oos:
        set_config(session, "factor_weights_suggested", {
            "weights": res["weights"],
            "diagnostic_ic": res.get("diagnostic_ic"),
            "note": ("ÖNERİ — canlı factor_weights EZİLMEDİ (require_oos_for_weight_change=True). "
                     "Uygulamak için değerleri /config → factor_weights'e kopyala ya da flag'i "
                     "False yap."),
        })
    return {"weights": res["weights"], "applied": not require_oos,
            "note": "öneri (canlı ezilmedi)" if require_oos else "uygulandı"}


def refresh_event_study(session: Session) -> dict:
    """Setup kanıt tablosunu AYNI prior parametrelerle yeniden ölç (veri büyüdükçe).

    Bu bir parametre araması DEĞİLDİR: dedektör parametreleri sabit kalır; yalnız
    örneklem büyür ve verdict/istatistik güncellenir (aylık tekrar — plan gereği).
    """
    from app.backtest.event_study import run_event_study

    res = run_event_study(session, write=True)
    setups = res.get("setups") or {}
    return {
        "n_tickers": res.get("n_tickers"),
        "verdicts": {k: v.get("verdict") for k, v in setups.items()},
        "n_events": {k: v.get("n_events") for k, v in setups.items()},
    }
