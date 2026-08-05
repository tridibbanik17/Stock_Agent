"""Supabase access layer for delivery profiles (privacy-safe columns only)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
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


def _normalize_token(token: str) -> str:
    text = (token or "").strip()
    if not text:
        raise ValueError("unsubscribe token is required")
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise ValueError("unsubscribe token must be a valid UUID") from exc


def mark_user_sent(user_id: str, sent_at: datetime | None = None) -> None:
    """Record a successful cron email so overlapping ticks do not double-send."""
    if not user_id:
        raise ValueError("user_id is required to mark last_sent_at")
    sent_at = sent_at or datetime.now(timezone.utc)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    iso = sent_at.astimezone(timezone.utc).isoformat()
    client = get_supabase()
    try:
        client.table("users").update({"last_sent_at": iso}).eq("id", user_id).execute()
    except Exception:
        logger.exception("Failed to update last_sent_at for id=%s", user_id)
        raise
    logger.info("Marked last_sent_at id=%s at=%s", user_id, iso)


def record_successful_send(
    user_id: str,
    *,
    local_day: str,
    previous_day: str | None,
    previous_count: int,
    slot_stamp: datetime | None = None,
) -> None:
    """
    Stamp last_sent_at and bump daily_send_count for the user's local calendar day.
    Caps abuse where someone edits send times after each delivery.
    """
    if not user_id:
        raise ValueError("user_id is required")
    stamp = slot_stamp or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    iso = stamp.astimezone(timezone.utc).isoformat()

    if previous_day == local_day:
        new_count = max(0, int(previous_count)) + 1
    else:
        new_count = 1

    client = get_supabase()
    payload = {
        "last_sent_at": iso,
        "daily_send_count": new_count,
        "daily_send_on": local_day,
    }
    try:
        client.table("users").update(payload).eq("id", user_id).execute()
    except Exception:
        logger.exception(
            "Failed to record successful send id=%s day=%s count=%s",
            user_id,
            local_day,
            new_count,
        )
        raise
    logger.info(
        "Recorded send id=%s last_sent_at=%s daily_send_on=%s daily_send_count=%s",
        user_id,
        iso,
        local_day,
        new_count,
    )


def ensure_unsubscribe_token(row: dict[str, Any]) -> str:
    """
    Return a stable unsubscribe token for this user, creating one if missing
    (legacy rows before migration / default).
    """
    existing = row.get("unsubscribe_token")
    if existing:
        return str(existing)

    user_id = str(row.get("id") or "")
    if not user_id:
        raise ValueError("user id is required to create unsubscribe_token")

    token = str(uuid.uuid4())
    client = get_supabase()
    try:
        result = (
            client.table("users")
            .update({"unsubscribe_token": token})
            .eq("id", user_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to set unsubscribe_token for id=%s", user_id)
        raise

    data = result.data or []
    if data:
        row["unsubscribe_token"] = data[0].get("unsubscribe_token") or token
    else:
        row["unsubscribe_token"] = token
    logger.info("Created unsubscribe_token for id=%s", user_id)
    return str(row["unsubscribe_token"])


def disable_subscription_by_token(token: str) -> dict[str, Any]:
    """
    Soft-disable delivery (enabled=false) for the matching unsubscribe token.
    Idempotent when already disabled.
    """
    normalized = _normalize_token(token)
    client = get_supabase()
    try:
        result = (
            client.table("users")
            .update({"enabled": False})
            .eq("unsubscribe_token", normalized)
            .execute()
        )
    except Exception:
        logger.exception("Unsubscribe update failed for token prefix=%s", normalized[:8])
        raise

    data = result.data or []
    if not data:
        raise LookupError("No subscription found for that unsubscribe token")

    record = data[0] if isinstance(data, list) else data
    # Trust the write intent if PostgREST omits the column somehow.
    if record.get("enabled") is not False:
        record = {**record, "enabled": False}
    logger.info(
        "Unsubscribed email=%s id=%s enabled=%s",
        record.get("email"),
        record.get("id"),
        record.get("enabled"),
    )
    return record


def enable_subscription_by_token(token: str) -> dict[str, Any]:
    """
    Re-enable delivery (enabled=true) for the matching unsubscribe token.
    Same proof-of-mailbox as unsubscribe (opaque token from the email link).
    """
    normalized = _normalize_token(token)
    client = get_supabase()
    try:
        result = (
            client.table("users")
            .update({"enabled": True})
            .eq("unsubscribe_token", normalized)
            .execute()
        )
    except Exception:
        logger.exception("Resubscribe update failed for token prefix=%s", normalized[:8])
        raise

    data = result.data or []
    if not data:
        raise LookupError("No subscription found for that unsubscribe token")

    record = data[0] if isinstance(data, list) else data
    if record.get("enabled") is not True:
        record = {**record, "enabled": True}
    logger.info(
        "Resubscribed email=%s id=%s enabled=%s",
        record.get("email"),
        record.get("id"),
        record.get("enabled"),
    )
    return record

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

    forbidden = {
        "holdings",
        "shares",
        "buyPrice",
        "buy_price",
        "geminiApiKey",
        "gemini_api_key",
    }
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
    if not record.get("unsubscribe_token"):
        try:
            ensure_unsubscribe_token(record)
        except Exception:
            logger.exception(
                "Upsert OK but could not ensure unsubscribe_token for email=%s",
                payload.email,
            )
    logger.info(
        "Upsert OK id=%s email=%s updated_at=%s",
        record.get("id"),
        record.get("email"),
        record.get("updated_at"),
    )
    return record


def insert_delivery_log(
    *,
    email: str,
    status: str,
    user_id: str | None = None,
    resend_id: str | None = None,
    subject: str | None = None,
    ticker_count: int | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """
    Append a cron send audit row. Never stores holdings or message bodies.
    Failures here are logged by the caller; this raises on DB errors.
    """
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"success", "failure", "dry_run"}:
        raise ValueError(f"invalid delivery log status: {status!r}")

    row: dict[str, Any] = {
        "email": str(email).strip().lower(),
        "status": normalized_status,
        "resend_id": (str(resend_id).strip() or None) if resend_id else None,
        "subject": (str(subject)[:240] if subject else None),
        "ticker_count": ticker_count,
        "error": (str(error)[:1000] if error else None),
    }
    if user_id:
        row["user_id"] = str(user_id)

    client = get_supabase()
    result = client.table("delivery_logs").insert(row).execute()
    data = result.data or []
    record = data[0] if data else None
    logger.info(
        "Delivery log id=%s email=%s status=%s resend_id=%s",
        (record or {}).get("id"),
        row["email"],
        normalized_status,
        row.get("resend_id"),
    )
    return record
