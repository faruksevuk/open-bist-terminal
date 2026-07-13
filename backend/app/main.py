"""FastAPI uygulaması — dashboard'a REST (+ ileride WS).

Lifespan: otonom scheduler'ı başlatır (config scheduler.enabled=false → no-op) —
start.bat ile backend ayakta kaldıkça veri/skor/haber/kalibrasyon kendiliğinden döner.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import scheduler as autoscheduler
from app.api.routes import ai as ai_routes
from app.api.routes import config as config_routes
from app.api.routes import context as context_routes
from app.api.routes import factors as factors_routes
from app.api.routes import health as health_routes
from app.api.routes import narrative as narrative_routes
from app.api.routes import news as news_routes
from app.api.routes import risk as risk_routes
from app.api.routes import scheduler as scheduler_routes
from app.api.routes import scores as scores_routes
from app.api.routes import setups as setups_routes
from app.api.routes import ticker as ticker_routes
from app.api.routes import trades as trades_routes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load_ai_keys()      # DB'de kayıtlı Gemini key'lerini aktive et (Ayarlar'dan girilenler)
    autoscheduler.start()
    yield
    autoscheduler.shutdown()


def _load_ai_keys() -> None:
    """Startup: DB config 'ai_keys' + 'ai_provider' → gemini_client runtime. Hata → varsayılanlar."""
    try:
        from app.config_store import get_config
        from app.db.base import SessionLocal
        from app.llm import gemini_client
        with SessionLocal() as s:
            keys = (get_config(s, "ai_keys") or {}).get("keys")
            provider = get_config(s, "ai_provider")
        if keys:
            gemini_client.set_runtime_keys(keys)
        if provider:
            gemini_client.set_provider(provider)
    except Exception:  # noqa: BLE001 — DB yoksa varsayılanlar (Gemini) çalışır
        pass


app = FastAPI(title="Open BIST Terminal", version="0.3.0", lifespan=lifespan)

# Next.js dashboard (localhost:3000) erişimi
app.add_middleware(
    CORSMiddleware,
    # 4000: Windows Hyper-V/WSL 3000'i rezerve ettiği için (EACCES). 3000 yedek.
    allow_origins=["http://localhost:4000", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_routes.router)
app.include_router(config_routes.router)
app.include_router(factors_routes.router)
app.include_router(risk_routes.router)
app.include_router(scheduler_routes.router)
app.include_router(scores_routes.router)
app.include_router(setups_routes.router)
app.include_router(trades_routes.router)
app.include_router(ai_routes.router)
app.include_router(news_routes.router)
app.include_router(ticker_routes.router)
app.include_router(context_routes.router)
app.include_router(narrative_routes.router)


@app.get("/")
def root() -> dict:
    return {"name": "Open BIST Terminal", "version": "0.3.0", "docs": "/docs"}
