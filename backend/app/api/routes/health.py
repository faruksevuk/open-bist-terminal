"""Sağlık kontrolü: DB erişilebilir mi. Cache in-process (Redis yok)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.base import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    db_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 — sağlık kontrolünde sustur
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "cache": "memory",  # in-process TTL (Redis kaldırıldı)
        "version": "0.3.0",
    }
