"""Config yükle/yaz — doğrudan DB (config minik, tek-indeksli tablo; cache gereksiz).

Eskiden Redis-cache'liydi; open-source/tek-süreç için Redis kaldırıldı. get_config hiçbir
sıcak döngüde per-ticker çağrılmıyor (score_universe/news_map/API'de request başına birkaç
kez), dolayısıyla DB-direct okuma bedava sayılır ve cross-process staleness sorununu da yok eder.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Config
from app.db.upsert import upsert


def get_config(session: Session, key: str) -> dict | None:
    row = session.get(Config, key)
    return row.value if row is not None else None


def get_all_config(session: Session) -> dict[str, dict]:
    rows = session.execute(select(Config)).scalars().all()
    return {r.key: r.value for r in rows}


def set_config(session: Session, key: str, value: dict) -> None:
    """Upsert (dialect-agnostik). Bir sonraki get_config taze DB değerini okur."""
    stmt = (
        upsert(Config)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[Config.key],
            set_={"value": value, "updated_at": func.now()},  # onupdate Core upsert'te tetiklenmez
        )
    )
    session.execute(stmt)
    session.commit()
