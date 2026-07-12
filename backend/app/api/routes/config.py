"""Config okuma/yazma API'si. /config sayfası (Milestone 9) bunu kullanır."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config_store import get_all_config, get_config, set_config
from app.db.base import get_session

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    value: dict


@router.get("")
def read_all(session: Session = Depends(get_session)) -> dict:
    return get_all_config(session)


@router.get("/{key}")
def read_one(key: str, session: Session = Depends(get_session)) -> dict:
    value = get_config(session, key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"config '{key}' yok")
    return {"key": key, "value": value}


@router.put("/{key}")
def update_one(key: str, body: ConfigUpdate, session: Session = Depends(get_session)) -> dict:
    set_config(session, key, body.value)
    return {"key": key, "value": body.value}
