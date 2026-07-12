"""Otonom scheduler durumu + manuel tetik API'si.

GET  /api/scheduler            → enabled/running + job listesi (sıradaki + son koşum notu).
POST /api/scheduler/run/{job}  → job'ı hemen arka planda koştur (daily_refresh / kap_poll /
                                 weekly_maintenance). Uzun sürebilir; yanıt hemen döner,
                                 sonuç scheduler_state'e yazılır (GET ile izlenir).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import scheduler

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("")
def scheduler_status() -> dict:
    return scheduler.status()


@router.post("/run/{job_id}")
def scheduler_run(job_id: str) -> dict:
    if not scheduler.trigger(job_id):
        raise HTTPException(status_code=404, detail=f"bilinmeyen job: {job_id}")
    return {"started": True, "job": job_id,
            "note": "Arka planda çalışıyor — durumu GET /api/scheduler'dan izle."}
