"""Ortam ayarları (pydantic-settings).

.env OPSİYONEL — yoksa varsayılanlar geçerlidir (SQLite + boş anahtarlar). AI anahtarları
Ayarlar sekmesinden girilir ve bist.db'de saklanır; .env yalnızca gelişmiş override içindir.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Veritabanı — varsayılan SQLite (dosya-tabanlı, sıfır servis; open-source "clone & run").
    # Postgres istersen DATABASE_URL'i postgresql+psycopg://… yap (kod dialect-agnostik).
    # Cache artık in-process (Redis yok) — bkz. app/cache.py.
    database_url: str = "sqlite:///./bist.db"

    # LLM (Milestone 7'de kullanılacak)
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""

    # Telegram (Milestone 8)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Makro (Milestone 2)
    evds_api_key: str = ""

    # App
    app_env: str = "dev"
    log_level: str = "INFO"

    @property
    def gemini_keys(self) -> list[str]:
        keys = [
            self.gemini_api_key_1,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            self.gemini_api_key_4,
        ]
        return [k for k in keys if k]


@lru_cache
def get_settings() -> Settings:
    return Settings()
