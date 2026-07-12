"""AI çağrı bütçesi — free-tier'ı KORUYAN sert günlük tavan (kullanıcı: "AI ağır yüke giremez").

DB-tabanlı günlük sayaç (config 'ai_usage'; her gün sıfırlanır). Her Gemini çağrısı ÖNCE
try_consume'dan geçer; tavan aşılırsa çağrı YAPILMAZ, sistem deterministik devam eder.
Tavan: config 'ai_budget.daily_cap' (varsayılan 50). 0 = AI tamamen kapalı.

NOT (tek-kullanıcı): read-then-write sayaçta teorik yarış var (eşzamanlı 2 çağrı 1 fazla
sayabilir) — tek-kullanıcı masaüstünde önemsiz; kilit eklenmedi (over-engineering).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config_store import get_config, set_config

_DEF_CAP = 50


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cap(session: Session) -> int:
    return int((get_config(session, "ai_budget") or {}).get("daily_cap", _DEF_CAP))


def status(session: Session) -> dict:
    """Günlük kullanım durumu (UI göstergesi + endpoint)."""
    cap = _cap(session)
    u = get_config(session, "ai_usage") or {}
    today = _today_utc()
    used = int(u.get("count", 0)) if u.get("date") == today else 0
    return {
        "date": today, "used": used, "cap": cap,
        "remaining": max(0, cap - used),
        "exhausted": cap <= 0 or used >= cap,
        "enabled": cap > 0,
    }


def try_consume(session: Session, cost: int = 1) -> bool:
    """Bütçeden `cost` çağrı düş. Yeterse True + sayaç artar; yetmezse False (çağrı YAPILMAMALI)."""
    cap = _cap(session)
    if cap <= 0:
        return False
    today = _today_utc()
    u = get_config(session, "ai_usage") or {}
    used = int(u.get("count", 0)) if u.get("date") == today else 0
    if used + cost > cap:
        return False
    set_config(session, "ai_usage", {"date": today, "count": used + cost})
    return True
