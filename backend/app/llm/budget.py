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
_DEF_EVENING_RESERVE = 12  # gün içi KAP yorumlarına KAPALI dilim — akşam brain/tez/görüş için saklanır


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cap(session: Session) -> int:
    return int((get_config(session, "ai_budget") or {}).get("daily_cap", _DEF_CAP))


def _reserve(session: Session) -> int:
    """Akşam rezervi: otomatik gün-içi tüketiciler (KAP interpret) cap'in bu kadarını KULLANAMAZ.

    Neden: tek havuzda haber-yoğun gün kotayı öğlene bitiriyor, 19:15'teki en değerli
    çıktılar (brain + grounded tezler + serbest görüş) aç kalıyordu. Kullanıcı-tetikli ve
    akşam çağrıları tam cap'i kullanır; yalnız respect_reserve=True çağrılar kısılır.
    """
    r = int((get_config(session, "ai_budget") or {}).get("evening_reserve", _DEF_EVENING_RESERVE))
    return max(0, min(r, _cap(session)))


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


def try_consume(session: Session, cost: int = 1, respect_reserve: bool = False) -> bool:
    """Bütçeden `cost` çağrı düş. Yeterse True + sayaç artar; yetmezse False (çağrı YAPILMAMALI).

    respect_reserve=True → otomatik gün-içi tüketici (KAP interpret): cap - evening_reserve
    tavanına tabidir; akşam işleri ve kullanıcı-tetikli çağrılar tam cap kullanır.
    """
    cap = _cap(session)
    if cap <= 0:
        return False
    limit = cap - _reserve(session) if respect_reserve else cap
    if limit <= 0:
        return False
    today = _today_utc()
    u = get_config(session, "ai_usage") or {}
    used = int(u.get("count", 0)) if u.get("date") == today else 0
    if used + cost > limit:
        return False
    set_config(session, "ai_usage", {"date": today, "count": used + cost})
    return True
