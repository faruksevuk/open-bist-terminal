"""SQLAlchemy engine, session ve declarative Base.

Varsayılan: SQLite (dosya-tabanlı, sıfır servis). DATABASE_URL Postgres'e çevrilirse de
çalışır (dialect-agnostik). SQLite'ta eşzamanlılık için WAL + busy_timeout ayarlanır
(scheduler thread yazar + API thread'leri okur).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Tüm modeller bundan türer."""


_settings = get_settings()
_url = _settings.database_url
_is_sqlite = _url.startswith("sqlite")

# SQLite: farklı thread'lerden (scheduler + API) tek dosyayı paylaş → check_same_thread=False.
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(_url, pool_pre_ping=True, future=True, connect_args=_connect_args)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        """WAL = eşzamanlı okuyucu + tek yazar; busy_timeout = kilit yerine kısa bekleme."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: istek başına session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
