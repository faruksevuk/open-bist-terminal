"""Otonom scheduler birim testleri — scheduler BAŞLATILMAZ (DB/ağ/thread yok).

Sabitlenen ilkeler:
- job kayıt defteri 3 job içerir (daily_refresh / kap_poll / weekly_maintenance),
- saat ayrıştırma bozuk girdide varsayılana düşer (otonom mod çökmez),
- özetleyici tek satır üretir, patlamaz,
- load_cfg DB yokken de varsayılanları döner (startup dayanıklılığı),
- seed_config 'scheduler' bloğu _DEF_SCHEDULER ile birebir (tek doğruluk kaynağı).
"""

from __future__ import annotations

from app.scheduler import _DEF_SCHEDULER, _JOBS, _parse_hhmm, _summarize, load_cfg
from app.seed_config import SEED_CONFIG


def test_job_registry_complete():
    assert set(_JOBS.keys()) == {"daily_refresh", "kap_poll", "weekly_maintenance"}
    for fn, desc in _JOBS.values():
        assert callable(fn) and isinstance(desc, str) and desc


def test_parse_hhmm_valid_and_invalid():
    assert _parse_hhmm("19:15", (0, 0)) == (19, 15)
    assert _parse_hhmm("9:5", (0, 0)) == (9, 5)
    assert _parse_hhmm("çöp", (19, 15)) == (19, 15)      # bozuk → varsayılan
    assert _parse_hhmm(None, (9, 0)) == (9, 0)
    assert _parse_hhmm("25:99", (9, 0)) == (23, 59)       # clamp


def test_summarize_flat_and_nested():
    s = _summarize({"data": {"tickers": 565, "bars_written": 1200},
                    "scores": {"written": 565}, "plain": 3})
    assert "data(" in s and "scores(" in s and "plain=3" in s
    assert "\n" not in s
    assert _summarize({}) == "ok"


def test_load_cfg_defaults_without_db():
    """DB erişilemese bile varsayılanlar dönmeli (startup'ta backend çökmez)."""
    cfg = load_cfg()
    for k in _DEF_SCHEDULER:
        assert k in cfg


def test_seed_scheduler_mirrors_defaults():
    """seed_config 'scheduler' _DEF_SCHEDULER ile birebir — iki kaynak sapmasın."""
    assert SEED_CONFIG["scheduler"] == _DEF_SCHEDULER


def test_seed_priority_block_sane():
    p = SEED_CONFIG["priority"]
    assert p["prior_weight_k"] > 0
    assert p["e_cap_r"] > 0
    assert 0.0 <= p["w_strength"] <= 1.0
    assert p["prior_r"]["_default"] == 0.0          # bilinmeyen setup'a uydurma edge yok
    assert p["prior_r"]["pead_drift"] <= 0.10        # prior mütevazı kalır (research, kanıt değil)
