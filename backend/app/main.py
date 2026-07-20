"""
Stock Agent FastAPI entrypoint.

Cloud surface stores ONLY:
  email, watchlist tickers, schedule frequency/days/times, timezone, enabled.

Never accepts share quantities, buy prices, or Gemini API keys.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import configure_logging, get_settings

configure_logging()
logger = logging.getLogger("stock_agent")

app = FastAPI(
    title="Stock Agent API",
    version="0.2.0",
    description=(
        "Multi-tenant delivery preferences API. "
        "Privacy-first: no holdings, buy prices, or Gemini keys."
    ),
)

# Chrome extension pages + local API tooling.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_origin_regex=r"chrome-extension://[\w-]+",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
async def on_startup() -> None:
    settings = get_settings()
    configured = bool(settings["supabase_url"] and settings["supabase_service_role_key"])
    logger.info(
        "Stock Agent API starting (supabase_configured=%s)",
        configured,
    )
    if not configured:
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — "
            "POST /api/subscribe will return 503 until .env is configured."
        )


@app.get("/")
async def root() -> dict[str, str]:
    """Browser-friendly landing so http://127.0.0.1:8000/ is not a bare 404."""
    return {
        "service": "stock-agent-api",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
        "subscribe": "POST /api/subscribe",
        "quotes": "POST /api/quotes/snapshot",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "stock-agent-api"}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings["host"],
        port=settings["port"],
        reload=True,
    )
