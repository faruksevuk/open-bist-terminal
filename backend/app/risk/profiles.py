"""Risk profilleri — "ne kadar risk almak istiyorum" TEK anahtarla (temkinli/dengeli/agresif).

DÜRÜSTLÜK: profil EDGE ÜRETMEZ; aynı sinyallerde pozisyon büyüklüğünü ölçekler. Agresif
profil kazancı da kaybı da aynı oranda büyütür — seçmeden önce kayıp-serisi matematiği
(P(seri), drawdown) gösterilir; kullanıcı gözü açık seçer.

Tasarım: profil değerleri config 'risk' anahtarına MERGE edilir (base_r/heat/stop'lar);
sizing.py ve tüm tüketiciler 'risk'i okumaya devam eder — tek doğruluk kaynağı bozulmaz.
Diğer 'risk' alanları (k_atr, edge_*, max_name_pct, cash_floor_pct) profilden BAĞIMSIZ kalır.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config_store import get_config, set_config

# POLICY — sabit-oranlı (fixed-fractional) profiller. Literatür çerçevesi:
# %0.5-1 muhafazakâr/standart, %2 pratikte savunulabilir agresif üst bandı; >%2-3
# küçük hesapta seri-kayıpta toparlanamaz drawdown üretir (research risk_frameworks).
# Değerler araştırma turuyla (2026-07-07) Kelly-çapraz-sağlamalı: isabet ~%45, kazanç/kayıp
# ~1.3 → tam Kelly ≈ %2.7; agresif (%1.5) ≈ yarım-Kelly, %2+ overbet bölgesi (MacLean/Thorp/
# Ziemba). Temkinli heat %3'e, agresif base_r %1.5 / heat %8'e çekildi (kanıtsız süit + küçük
# hesap + manuel/15dk-gecikmeli emir). 'dengeli' eski seed 'risk' ile birebir kalır.
PROFILES: dict[str, dict] = {
    "temkinli": {
        "base_r": 0.005, "max_heat_pct": 0.03,
        "daily_stop_pct": 0.015, "weekly_dd_pct": 0.05,
        "label": "Temkinli", "desc": "işlem başına %0.5 risk — çeyrek-Kelly; sermaye korumada öncelik",
    },
    "dengeli": {
        "base_r": 0.01, "max_heat_pct": 0.06,
        "daily_stop_pct": 0.03, "weekly_dd_pct": 0.10,
        "label": "Dengeli", "desc": "işlem başına %1 risk — standart swing çerçevesi (varsayılan)",
    },
    "agresif": {
        "base_r": 0.015, "max_heat_pct": 0.08,
        "daily_stop_pct": 0.04, "weekly_dd_pct": 0.12,
        "label": "Agresif", "desc": "işlem başına %1.5 risk — yarım-Kelly tavanı; seri kayıpta derin çukur",
    },
}
# profilin 'risk' config'ine taşıdığı anahtarlar (diğerlerine dokunulmaz)
_PROFILE_KEYS = ("base_r", "max_heat_pct", "daily_stop_pct", "weekly_dd_pct")

_DEF_ACTIVE = "dengeli"


# --- saf matematik (test edilebilir; DB yok) ------------------------------

def streak_probability(p_loss: float, k: int, n: int) -> float:
    """n işlemde EN AZ BİR kez k ardışık kayıp görme olasılığı (kesin DP, yaklaşıklık değil).

    Durum = güncel kayıp-serisi uzunluğu (0..k-1); k'ya ulaşan kütle emilir.
    """
    if k <= 0 or n < k:
        return 0.0 if n < k else 1.0
    p = min(max(float(p_loss), 0.0), 1.0)
    q = 1.0 - p
    # state[j] = j uzunluğunda kayıp serisiyle biten, henüz k-serisi görmemiş olasılık
    state = [0.0] * k
    state[0] = 1.0
    absorbed = 0.0
    for _ in range(int(n)):
        nxt = [0.0] * k
        for j, mass in enumerate(state):
            if mass == 0.0:
                continue
            nxt[0] += mass * q          # kazanç → seri sıfırlanır
            if j + 1 >= k:
                absorbed += mass * p    # k'ıncı ardışık kayıp → emildi
            else:
                nxt[j + 1] += mass * p
        state = nxt
    return float(min(1.0, absorbed))


def streak_drawdown(base_r: float, k: int) -> float:
    """k ardışık 1R kayıpta bileşik drawdown fraksiyonu = 1 − (1−base_r)^k."""
    return float(1.0 - (1.0 - float(base_r)) ** int(k))


def profile_math(base_r: float, hit_rate: float = 0.45, n_trades: int = 50) -> dict:
    """Bir profilin dürüst 'seçmeden önce bak' matematiği.

    hit_rate: R>0 oranı (canlı OOS'tan gelir; yoksa muhafazakâr 0.45 varsayılır).
    n_trades: bakılan pencere (≈yarım yıl, haftada ~2 işlemle).
    """
    p_loss = 1.0 - min(max(hit_rate, 0.0), 1.0)
    streaks = {}
    for k in (4, 6, 8):
        streaks[str(k)] = {
            "p_streak": round(streak_probability(p_loss, k, n_trades), 3),
            "drawdown": round(streak_drawdown(base_r, k), 4),
        }
    return {
        "assumed_hit_rate": round(hit_rate, 3),
        "n_trades_window": n_trades,
        "risk_per_trade": base_r,
        "streaks": streaks,
    }


# --- config uygulaması ----------------------------------------------------

def active_profile(session: Session) -> str:
    rp = get_config(session, "risk_profile") or {}
    name = rp.get("active", _DEF_ACTIVE)
    return name if name in PROFILES else _DEF_ACTIVE


def apply_profile(session: Session, name: str) -> dict:
    """Profili aktive et: 'risk' config'ine profil anahtarlarını merge et + 'risk_profile' yaz.

    Bilinmeyen profil → ValueError. Döner: yeni risk config.
    """
    if name not in PROFILES:
        raise ValueError(f"bilinmeyen profil: {name} (geçerli: {', '.join(PROFILES)})")
    risk = dict(get_config(session, "risk") or {})
    for key in _PROFILE_KEYS:
        risk[key] = PROFILES[name][key]
    set_config(session, "risk", risk)
    set_config(session, "risk_profile", {"active": name})
    return risk


def profile_overview(session: Session, live_hit_rate: float | None = None) -> dict:
    """API için: aktif profil + tüm profillerin matematiği (canlı isabetle, yoksa 0.45)."""
    hit = live_hit_rate if live_hit_rate is not None else 0.45
    active = active_profile(session)
    out = {}
    for name, p in PROFILES.items():
        out[name] = {
            "label": p["label"], "desc": p["desc"],
            "base_r": p["base_r"], "max_heat_pct": p["max_heat_pct"],
            "daily_stop_pct": p["daily_stop_pct"], "weekly_dd_pct": p["weekly_dd_pct"],
            "math": profile_math(p["base_r"], hit_rate=hit),
        }
    return {
        "active": active,
        "hit_rate_source": "canlı OOS" if live_hit_rate is not None else "varsayılan (muhafazakâr 0.45)",
        "profiles": out,
        "note": ("Profil edge üretmez; pozisyon büyüklüğünü ölçekler. Agresif = kazanç ve "
                 "kayıp aynı oranda büyür. Ölçülen sistem beklentisi profilden bağımsızdır."),
    }
