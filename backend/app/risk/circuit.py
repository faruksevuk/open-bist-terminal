"""Devre kesiciler — risk profilinin daily_stop/weekly_dd vaadini GERÇEKTEN uygular.

DENETİM BULGUSU (2026-07-16): daily_stop_pct/weekly_dd_pct yalnız tanım+gösterimdi; portföy
P&L'ini eşiklerle karşılaştırıp işlemi durduran tek satır kod yoktu — profilin 4
parametresinden 2'si placebo'ydu. Bu modül o boşluğu kapatır:

- Gün-başı equity ve hafta-içi tepe equity 'circuit_marks' config'inde tutulur
  (günün/haftanın İLK değerlendirmesinde damgalanır; backend gün ortası açıldıysa taban o
  andan başlar — muhafazakâr değil ama dürüst, not düşülür).
- daily: bugünkü getiri ≤ -daily_stop_pct → kesici atar.
- weekly: hafta-içi tepeden düşüş ≤ -weekly_dd_pct → kesici atar.
- ETKİ: /api/setups al-adayı sinyalleri "girme"ye çevirir (gerekçesiyle) + UI banner.
  Sistem emir göndermediği için bu bir "yeni pozisyon önerme" frenidir; mevcut
  pozisyonların stop'ları sinyal planlarında yaşamaya devam eder.

Saf matematik `evaluate_circuit`'te (test edilebilir); DB/mark yönetimi `circuit_state`'te.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.config_store import get_config, set_config

log = logging.getLogger(__name__)

_DEF_DAILY = 0.03   # risk config'te yoksa (profiller yazar) muhafazakâr varsayılan
_DEF_WEEKLY = 0.10


def evaluate_circuit(total: float, day_base: float | None, week_high: float | None,
                     daily_stop_pct: float, weekly_dd_pct: float) -> dict:
    """SAF kesici matematiği: getiriler + hangi kesicinin attığı. Test edilebilir."""
    daily_ret = (total / day_base - 1.0) if day_base else 0.0
    weekly_dd = (total / week_high - 1.0) if week_high else 0.0
    tripped: list[str] = []
    if daily_stop_pct > 0 and daily_ret <= -daily_stop_pct:
        tripped.append("daily")
    if weekly_dd_pct > 0 and weekly_dd <= -weekly_dd_pct:
        tripped.append("weekly")
    return {
        "daily_ret": round(daily_ret, 4),
        "weekly_dd": round(weekly_dd, 4),
        "daily_stop_pct": daily_stop_pct,
        "weekly_dd_pct": weekly_dd_pct,
        "tripped": tripped,          # [] | ["daily"] | ["weekly"] | ["daily","weekly"]
        "active": bool(tripped),
    }


def circuit_state(session: Session, total_try: float | None = None) -> dict:
    """Kesici durumu (gün/hafta işaretlerini gerekirse damgalar; yalnız değişince yazar).

    total_try verilmezse portföy anlık değeri hesaplanır (reconcile=False — DB'ye pozisyon
    yazmaz; yalnız mark güncellemesi gerektiğinde config'e küçük bir yazma olur).
    """
    risk = get_config(session, "risk") or {}
    daily_stop = float(risk.get("daily_stop_pct", _DEF_DAILY) or 0.0)
    weekly_dd = float(risk.get("weekly_dd_pct", _DEF_WEEKLY) or 0.0)

    if total_try is None:
        from app.risk.portfolio import portfolio_snapshot
        total_try = float(portfolio_snapshot(session, reconcile=False).get("total_try") or 0.0)

    today = date.today()
    iso = today.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"

    marks = dict(get_config(session, "circuit_marks") or {})
    changed = False
    if marks.get("date") != today.isoformat():
        marks["date"] = today.isoformat()
        marks["day_base"] = total_try
        changed = True
    if marks.get("week") != week_key:
        marks["week"] = week_key
        marks["week_high"] = total_try
        changed = True
    if total_try > float(marks.get("week_high") or 0.0):
        marks["week_high"] = total_try
        changed = True
    if changed:
        try:
            set_config(session, "circuit_marks", marks)
            session.commit()
        except Exception:  # noqa: BLE001 — mark yazılamasa da durum hesaplanır
            session.rollback()
            log.warning("circuit_marks yazılamadı — durum geçici hafızayla hesaplandı")

    st = evaluate_circuit(total_try, marks.get("day_base"), marks.get("week_high"),
                          daily_stop, weekly_dd)
    st["day_base"] = marks.get("day_base")
    st["week_high"] = marks.get("week_high")
    st["total_try"] = round(total_try, 2)
    if st["active"]:
        parts = []
        if "daily" in st["tripped"]:
            parts.append(f"günlük stop aşıldı ({st['daily_ret'] * 100:+.1f}% ≤ -%{daily_stop * 100:.0f})")
        if "weekly" in st["tripped"]:
            parts.append(f"haftalık düşüş limiti aşıldı ({st['weekly_dd'] * 100:+.1f}% ≤ -%{weekly_dd * 100:.0f})")
        st["reason"] = "devre kesici: " + " + ".join(parts) + " — bugün yeni pozisyon önerilmez"
    else:
        st["reason"] = None
    return st
