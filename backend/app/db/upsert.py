"""Dialect-agnostik upsert (INSERT ... ON CONFLICT).

SQLite (3.24+) ve Postgres, `.on_conflict_do_update(index_elements=..., set_=...)` /
`.on_conflict_do_nothing(index_elements=...)` + `.excluded.col` API'sini AYNI şekilde sunar.
Bu yardımcı doğru dialect insert'ini seçer; çağıran kod her iki dialectte de değişmez.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert

from app.db.base import engine


def upsert(table):
    """Aktif dialect'e uygun `insert()` döndür (Postgres ya da SQLite)."""
    if engine.dialect.name == "postgresql":
        return _pg_insert(table)
    return _sqlite_insert(table)
