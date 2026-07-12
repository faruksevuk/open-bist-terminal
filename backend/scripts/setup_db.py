"""DB + tablolar + seed config (SQLite).

Kullanım:
    cd backend
    python scripts/setup_db.py

Tabloları oluşturur (create_all, idempotent) ve SCORING v0.2 §10 seed config'i yazar.
Şema değişiminde: şu an create_all + "start fresh" (şema değişince bist.db'yi sil,
tekrar çalıştır). Dolu DB'yi koruyan göç gerekirse Alembic eklenebilir (bkz README).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows konsolu (cp1252) Türkçe çıktıda çökmesin → UTF-8'e sabitle
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# `app` paketini import edebilmek için backend/ kökünü yola ekle
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import models  # noqa: E402,F401  (modelleri metadata'ya kaydeder)
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db.models import Config  # noqa: E402
from app.seed_config import SEED_CONFIG  # noqa: E402


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    print(f"[ok] {len(Base.metadata.tables)} tablo oluşturuldu/doğrulandı.")


def seed_config(overwrite: bool = False) -> None:
    with SessionLocal() as session:
        existing = {k for (k,) in session.execute(select(Config.key)).all()}
        written_keys: list[str] = []
        for key, value in SEED_CONFIG.items():
            if key in existing and not overwrite:
                continue
            row = session.get(Config, key)
            if row is None:
                session.add(Config(key=key, value=value))
            else:
                row.value = value
            written_keys.append(key)
        session.commit()
        # Config cache'i kaldırıldı (get_config artık DB-direct) → invalidate gerekmez.
        print(f"[ok] config seed: {len(written_keys)} anahtar yazıldı, {len(existing)} mevcuttu.")


if __name__ == "__main__":
    overwrite = "--overwrite" in sys.argv
    create_tables()
    seed_config(overwrite=overwrite)
    print("[done] Milestone 1 DB hazır.")
