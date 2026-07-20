"""Supabase access layer for delivery profiles (privacy-safe columns only)."""

from __future__ import annotations

import logging
from typing import Any

from supabase import Client, create_client

from app.config import require_supabase_config
from app.models.schemas import SubscribeRequest

logger = logging.getLogger("stock_agent.supabase")

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        url, key = require_supabase_config()
        _client = create_client(url, key)
        logger.info("Supabase client initialized for %s", url)
    return _client


def upsert_user_subscription(payload: SubscribeRequest) -> dict[str, Any]:
    """
    Insert or update a user delivery profile keyed by email.
    Never writes holdings, buy prices, or API keys.
    """
    row = {
        "email": payload.email,
        "watchlist": payload.watchlist,
        "schedule_frequency": payload.schedule.frequency,
        "preferred_hours": payload.schedule.times,
        "preferred_days": payload.schedule.days,
        "timezone": payload.schedule.timezone,
        "enabled": payload.enabled,
    }

    # Defense-in-depth: refuse if private keys ever appear.
    forbidden = {"holdings", "shares", "buyPrice", "buy_price", "geminiApiKey", "gemini_api_key"}
    leaked = forbidden.intersection(row)
    if leaked:
        raise ValueError(f"Refusing to persist private fields: {sorted(leaked)}")

    logger.info(
        "Upserting delivery profile email=%s tickers=%d frequency=%s hours=%s",
        payload.email,
        len(payload.watchlist),
        payload.schedule.frequency,
        payload.schedule.times,
    )

    client = get_supabase()
    try:
        result = (
            client.table("users")
            .upsert(row, on_conflict="email")
            .execute()
        )
    except Exception:
        logger.exception("Supabase upsert failed for email=%s", payload.email)
        raise

    data = result.data
    if not data:
        logger.error("Supabase upsert returned empty data for email=%s", payload.email)
        raise RuntimeError("Database upsert returned no rows")

    record = data[0] if isinstance(data, list) else data
    logger.info(
        "Upsert OK id=%s email=%s updated_at=%s",
        record.get("id"),
        record.get("email"),
        record.get("updated_at"),
    )
    return record
