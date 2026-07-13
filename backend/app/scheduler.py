"""Otonom orkestrasyon — APScheduler (FastAPI lifespan'da başlar; start.bat = otonom mod).

NEDEN in-process: tek kullanıcı + tek makine; ayrı servis/cron karmaşası yerine backend
süreci yaşadıkça görevler kendiliğinden işler (README: PC piyasa saatlerinde açık).
Kullanıcının manuel refresh.bat/poll-kap.bat zinciri OTOMATİKLEŞİR; .bat'lar manuel
alternatif olarak kalır (aynı pipeline fonksiyonları — çatal yok).

Job'lar (config 'scheduler'; saatler Europe/Istanbul):
- daily_refresh       Pzt-Cum 19:15 → 1 aylık bar tazele + skorla+tara+bağlam+sonuçlar.
                      (BIST 18:00 kapanış + 15dk gecikme + yfinance EOD payı.)
- kap_poll            Pzt-Cum 10-18 arası her 30dk → KAP + Gemini yorum; yeni olay
                      geldiyse yeniden skorla (haber faktörü taze kalır). Key yoksa no-op.
- weekly_maintenance  Cmt 09:00 → faktör kalibrasyonu; her N haftada bir event-study
                      yeniden ölçümü (AYNI prior parametreler — arama değil, örneklem büyür).

Her job kendi SessionLocal'ını açar, hatayı yutmaz→loglar ve sonucu config
['scheduler_state']'e yazar; dashboard bunu 'otonom durum' rozetinde gösterir.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import pipeline
from app.config_store import get_config, set_config
from app.db.base import SessionLocal

log = logging.getLogger(__name__)

# Varsayılanlar (config 'scheduler' yoksa) — POLICY; seed_config ile birebir.
_DEF_SCHEDULER: dict = {
    "enabled": True,
    "timezone": "Europe/Istanbul",
    "daily_refresh_time": "19:15",
    "history_period": "1mo",
    "fundamentals_kap_days": 5,      # gecelik: son N günde bilanço açıklayanların F/SUE'su
    "kap_poll_minutes": 30,          # 0 = kapalı
    "rescore_after_kap": True,
    "weekly_day": "sat",
    "weekly_time": "09:00",
    "weekly_fundamentals": True,     # Cmt: tam evren F-Score+SUE sweep'i
    "weekly_valuation": True,        # Cmt: tam evren PE/PB sweep'i (resume-safe)
    "event_study_every_weeks": 4,    # 0 = otomatik event-study kapalı
    "narrative_enabled": True,       # gecelik: grounded analist tezleri + karne (key/kota yoksa no-op)
}

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


# --- config / state yardımcıları -----------------------------------------

def load_cfg() -> dict:
    """Config 'scheduler' + varsayılanlar. DB kapalıysa varsayılanlarla devam."""
    try:
        with SessionLocal() as s:
            c = get_config(s, "scheduler") or {}
    except Exception:  # noqa: BLE001 — startup'ta DB henüz hazır olmayabilir
        log.warning("scheduler config okunamadı (DB kapalı?) — varsayılanlar kullanılıyor")
        c = {}
    return {**_DEF_SCHEDULER, **c}


def _state_write(job: str, ok: bool, note: str) -> None:
    try:
        with SessionLocal() as s:
            st = get_config(s, "scheduler_state") or {}
            st[job] = {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "ok": bool(ok),
                "note": note[:600],
            }
            set_config(s, "scheduler_state", st)
    except Exception:  # noqa: BLE001
        log.exception("scheduler_state yazılamadı (%s)", job)


def _summarize(res: dict) -> str:
    """Job özetini tek satıra indir (state notu / log)."""
    try:
        parts: list[str] = []
        for k, v in res.items():
            if isinstance(v, dict):
                inner = ", ".join(f"{ik}={iv}" for ik, iv in list(v.items())[:4])
                parts.append(f"{k}({inner})")
            else:
                parts.append(f"{k}={v}")
        return " | ".join(parts) or "ok"
    except Exception:  # noqa: BLE001
        return "ok"


def _run(job: str, fn) -> dict | None:
    """Job gövdesi: kendi session'ı + hata yakalama + state yazımı."""
    log.info("[scheduler] %s başladı", job)
    try:
        with SessionLocal() as s:
            res = fn(s) or {}
        note = _summarize(res)
        _state_write(job, True, note)
        log.info("[scheduler] %s bitti: %s", job, note)
        return res
    except Exception as exc:  # noqa: BLE001 — job patlarsa scheduler yaşamaya devam eder
        _state_write(job, False, f"hata: {exc}")
        log.exception("[scheduler] %s HATA", job)
        return None


# --- job gövdeleri --------------------------------------------------------

def job_daily_refresh() -> None:
    cfg = load_cfg()

    def _fn(s):
        out = {"data": pipeline.refresh_data(s, period=str(cfg.get("history_period", "1mo")))}
        # taze bilanço açıklayan isimlerin F/SUE'su HEMEN güncellensin (PEAD tazeliği);
        # hata skorlamayı durdurmaz
        try:
            out["fundamentals"] = pipeline.refresh_fundamentals_targeted(
                s, days=int(cfg.get("fundamentals_kap_days", 5)))
        except Exception as exc:  # noqa: BLE001
            s.rollback()
            out["fundamentals"] = {"error": str(exc)}
        out.update(pipeline.refresh_scores(s))
        # Trader-Brain: grounded analist tezleri + karne (skor/bağlam hazır olduktan SONRA)
        if cfg.get("narrative_enabled", True):
            try:
                out["narrative"] = pipeline.refresh_narrative(s)
            except Exception as exc:  # noqa: BLE001 — narrative patlasa skorlar yazıldı
                s.rollback()
                out["narrative"] = {"error": str(exc)}
        return out

    _run("daily_refresh", _fn)


def job_kap_poll() -> None:
    cfg = load_cfg()

    def _fn(s):
        res = pipeline.poll_news(s)
        if res.get("stored") and cfg.get("rescore_after_kap", True):
            res["rescore"] = pipeline.refresh_scores(s).get("scores", {})
        return res

    _run("kap_poll", _fn)


def job_weekly_maintenance() -> None:
    cfg = load_cfg()

    def _fn(s):
        out: dict = {}
        # tam evren fundamental (F/SUE) + valuation (PE/PB) sweep'i — skor/kalibrasyon
        # taze veriyle koşsun; her adım kendi başına düşebilir, iş devam eder
        if cfg.get("weekly_fundamentals", True):
            try:
                out["fundamentals"] = pipeline.refresh_fundamentals_full(s)
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                out["fundamentals"] = {"error": str(exc)}
        if cfg.get("weekly_valuation", True):
            try:
                out["valuation"] = pipeline.refresh_valuation(s, full=True)
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                out["valuation"] = {"error": str(exc)}
        out["calibration"] = pipeline.weekly_calibrate(s)
        weeks = int(cfg.get("event_study_every_weeks", 4) or 0)
        if weeks > 0 and _event_study_due(s, weeks):
            es = pipeline.refresh_event_study(s)
            out["event_study"] = es
            _state_write("event_study", True, _summarize(es))
        # sweep sonrası skorları tazele (yeni F/SUE/PE-PB skora yansısın)
        out["rescore"] = pipeline.refresh_scores(s).get("scores", {})
        return out

    _run("weekly_maintenance", _fn)


def _event_study_due(session, weeks: int) -> bool:
    st = get_config(session, "scheduler_state") or {}
    last = (st.get("event_study") or {}).get("last_run")
    if not last:
        return True
    try:
        dt_last = datetime.fromisoformat(last)
        return (datetime.now(timezone.utc) - dt_last).days >= weeks * 7 - 1
    except ValueError:
        return True


_JOBS = {
    "daily_refresh": (job_daily_refresh, "Gecelik veri + skor + tarama + sonuç takibi"),
    "kap_poll": (job_kap_poll, "KAP haber + Gemini yorum (piyasa saatleri)"),
    "weekly_maintenance": (job_weekly_maintenance, "Haftalık kalibrasyon + periyodik event-study"),
}


# --- yaşam döngüsü --------------------------------------------------------

def _parse_hhmm(v: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        hh, mm = str(v).strip().split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except (ValueError, AttributeError):
        return default


def start() -> BackgroundScheduler | None:
    """Scheduler'ı kur + başlat (idempotent). config.scheduler.enabled=false → no-op."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return _scheduler
        cfg = load_cfg()
        if not cfg.get("enabled", True):
            log.info("[scheduler] devre dışı (config scheduler.enabled=false)")
            return None
        try:
            tz = ZoneInfo(str(cfg.get("timezone", "Europe/Istanbul")))
        except Exception:  # noqa: BLE001 — tz verisi yoksa sistem saatiyle devam
            log.warning("[scheduler] timezone yüklenemedi — sistem saati kullanılacak")
            tz = None

        sch = BackgroundScheduler(timezone=tz)
        hh, mm = _parse_hhmm(cfg.get("daily_refresh_time", "19:15"), (19, 15))
        sch.add_job(
            job_daily_refresh,
            CronTrigger(day_of_week="mon-fri", hour=hh, minute=mm, timezone=tz),
            id="daily_refresh", name=_JOBS["daily_refresh"][1],
            coalesce=True, max_instances=1, misfire_grace_time=6 * 3600,
        )
        kmin = int(cfg.get("kap_poll_minutes", 30) or 0)
        if kmin > 0:
            sch.add_job(
                job_kap_poll,
                CronTrigger(day_of_week="mon-fri", hour="10-18",
                            minute=f"*/{max(5, kmin)}", timezone=tz),
                id="kap_poll", name=_JOBS["kap_poll"][1],
                coalesce=True, max_instances=1, misfire_grace_time=600,
            )
        whh, wmm = _parse_hhmm(cfg.get("weekly_time", "09:00"), (9, 0))
        sch.add_job(
            job_weekly_maintenance,
            CronTrigger(day_of_week=str(cfg.get("weekly_day", "sat")),
                        hour=whh, minute=wmm, timezone=tz),
            id="weekly_maintenance", name=_JOBS["weekly_maintenance"][1],
            coalesce=True, max_instances=1, misfire_grace_time=24 * 3600,
        )
        sch.start()
        _scheduler = sch
        log.info("[scheduler] otonom mod başladı — %d job", len(sch.get_jobs()))
        return sch


def shutdown() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
            log.info("[scheduler] durduruldu")


def status() -> dict:
    """API için durum: enabled/çalışıyor + job'lar (sıradaki koşum) + son koşum notları."""
    cfg = load_cfg()
    st: dict = {}
    try:
        with SessionLocal() as s:
            st = get_config(s, "scheduler_state") or {}
    except Exception:  # noqa: BLE001
        pass
    jobs: list[dict] = []
    sch = _scheduler
    live = {j.id: j for j in sch.get_jobs()} if sch else {}
    for jid, (_, desc) in _JOBS.items():
        j = live.get(jid)
        nxt = getattr(j, "next_run_time", None) if j else None
        jobs.append({
            "id": jid,
            "name": desc,
            "scheduled": j is not None,
            "next_run": nxt.isoformat() if nxt else None,
            "last": st.get(jid),
        })
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "running": sch is not None and sch.running,
        "timezone": cfg.get("timezone"),
        "jobs": jobs,
        "event_study_state": st.get("event_study"),
    }


def trigger(job_id: str) -> bool:
    """Job'ı hemen arka planda koştur (manuel tetik — UI 'şimdi çalıştır')."""
    entry = _JOBS.get(job_id)
    if entry is None:
        return False
    threading.Thread(target=entry[0], name=f"manual-{job_id}", daemon=True).start()
    return True
