"""Supabase access layer for delivery profiles (privacy-safe columns only)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client, create_client

from app.config import require_supabase_config
from app.models.schemas import SubscribeRequest

logger = logging.getLogger("stock_agent.supabase")

_client: Client | None = None
RECOVER_TTL = timedelta(hours=1)


class OwnershipError(PermissionError):
    """Caller failed manage_token ownership check."""


def get_supabase() -> Client:
    global _client
    if _client is None:
        url, key = require_supabase_config()
        _client = create_client(url, key)
        logger.info("Supabase client initialized for %s", url)
    return _client


def _normalize_uuid_token(token: str, *, label: str) -> str:
    text = (token or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid UUID") from exc


def _normalize_token(token: str) -> str:
    return _normalize_uuid_token(token, label="unsubscribe token")


def get_user_by_email(email: str) -> dict[str, Any] | None:
    client = get_supabase()
    result = (
        client.table("users")
        .select("*")
        .eq("email", str(email).strip().lower())
        .limit(1)
        .execute()
    )
    data = result.data or []
    return data[0] if data else None


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


def ensure_manage_token(row: dict[str, Any]) -> str:
    existing = row.get("manage_token")
    if existing:
        return str(existing)

    user_id = str(row.get("id") or "")
    if not user_id:
        raise ValueError("user id is required to create manage_token")

    token = str(uuid.uuid4())
    client = get_supabase()
    try:
        result = (
            client.table("users")
            .update({"manage_token": token})
            .eq("id", user_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to set manage_token for id=%s", user_id)
        raise

    data = result.data or []
    if data:
        row["manage_token"] = data[0].get("manage_token") or token
    else:
        row["manage_token"] = token
    logger.info("Created manage_token for id=%s", user_id)
    return str(row["manage_token"])


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
    logger.info(
        "Unsubscribed email=%s id=%s enabled=%s",
        record.get("email"),
        record.get("id"),
        record.get("enabled"),
    )
    return record


def upsert_user_subscription(payload: SubscribeRequest) -> dict[str, Any]:
    """
    Insert or update a user delivery profile keyed by email.

    New email → create + issue manage_token (returned to the extension).
    Existing email → require matching manageToken or raise OwnershipError.
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
        "email_on_grade_change_only": bool(payload.emailOnGradeChangeOnly),
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

    existing = get_user_by_email(payload.email)
    client = get_supabase()

    if existing:
        stored = ensure_manage_token(existing)
        presented = (payload.manageToken or "").strip()
        if not presented:
            raise OwnershipError(
                "This email already has a subscription. "
                "Provide manageToken from this device, or use Recover access."
            )
        try:
            presented_norm = _normalize_uuid_token(presented, label="manageToken")
        except ValueError as exc:
            raise OwnershipError(str(exc)) from exc
        if presented_norm != str(uuid.UUID(str(stored))):
            raise OwnershipError(
                "manageToken does not match this subscription. "
                "Use Recover access if you lost the extension data."
            )

        logger.info(
            "Updating delivery profile email=%s tickers=%d (ownership ok)",
            payload.email,
            len(payload.watchlist),
        )
        try:
            result = (
                client.table("users")
                .update(row)
                .eq("id", existing["id"])
                .execute()
            )
        except Exception:
            logger.exception("Supabase update failed for email=%s", payload.email)
            raise
    else:
        manage_token = str(uuid.uuid4())
        unsubscribe_token = str(uuid.uuid4())
        insert_row = {
            **row,
            "manage_token": manage_token,
            "unsubscribe_token": unsubscribe_token,
        }
        logger.info(
            "Creating delivery profile email=%s tickers=%d",
            payload.email,
            len(payload.watchlist),
        )
        try:
            result = client.table("users").insert(insert_row).execute()
        except Exception:
            logger.exception("Supabase insert failed for email=%s", payload.email)
            raise

    data = result.data
    if not data:
        logger.error("Supabase write returned empty data for email=%s", payload.email)
        raise RuntimeError("Database write returned no rows")

    record = data[0] if isinstance(data, list) else data
    if not record.get("unsubscribe_token"):
        try:
            ensure_unsubscribe_token(record)
        except Exception:
            logger.exception(
                "Write OK but could not ensure unsubscribe_token for email=%s",
                payload.email,
            )
    if not record.get("manage_token"):
        try:
            ensure_manage_token(record)
        except Exception:
            logger.exception(
                "Write OK but could not ensure manage_token for email=%s",
                payload.email,
            )

    logger.info(
        "Subscribe OK id=%s email=%s updated_at=%s",
        record.get("id"),
        record.get("email"),
        record.get("updated_at"),
    )
    return record


def start_subscription_recovery(email: str) -> tuple[dict[str, Any] | None, str | None]:
    """
    Issue a one-time recover_token for an existing subscription.
    Returns (row, recover_token). If email unknown, returns (None, None)
    without leaking existence to the caller (route still returns generic OK).
    """
    existing = get_user_by_email(email)
    if not existing:
        return None, None

    token = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + RECOVER_TTL
    client = get_supabase()
    try:
        result = (
            client.table("users")
            .update(
                {
                    "recover_token": token,
                    "recover_token_expires_at": expires.isoformat(),
                }
            )
            .eq("id", existing["id"])
            .execute()
        )
    except Exception:
        logger.exception("Failed to set recover_token for email=%s", email)
        raise

    data = result.data or []
    record = data[0] if data else existing
    logger.info("Recovery started for email=%s expires=%s", email, expires.isoformat())
    return record, token


def complete_subscription_recovery(recover_token: str) -> dict[str, Any]:
    """
    Validate recover_token, rotate manage_token, clear recover fields.
    Returns the updated row including the new manage_token.
    """
    normalized = _normalize_uuid_token(recover_token, label="recover token")
    client = get_supabase()
    result = (
        client.table("users")
        .select("*")
        .eq("recover_token", normalized)
        .limit(1)
        .execute()
    )
    data = result.data or []
    if not data:
        raise LookupError("Invalid or expired recovery link")

    row = data[0]
    expires_raw = row.get("recover_token_expires_at")
    if expires_raw:
        text = str(expires_raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            expires = datetime.fromisoformat(text)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                raise LookupError("Recovery link has expired. Request a new one.")
        except LookupError:
            raise
        except ValueError:
            logger.warning("Bad recover_token_expires_at=%r", expires_raw)

    new_manage = str(uuid.uuid4())
    try:
        updated = (
            client.table("users")
            .update(
                {
                    "manage_token": new_manage,
                    "recover_token": None,
                    "recover_token_expires_at": None,
                }
            )
            .eq("id", row["id"])
            .execute()
        )
    except Exception:
        logger.exception("Failed to rotate manage_token for id=%s", row.get("id"))
        raise

    out = (updated.data or [None])[0]
    if not out:
        raise RuntimeError("Recovery update returned no rows")
    logger.info("Recovery complete email=%s id=%s", out.get("email"), out.get("id"))
    return out


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


def update_user_last_grades(user_id: str, grades: dict[str, str]) -> None:
    """Persist the grades snapshot that was just emailed (for flip detection)."""
    if not user_id:
        raise ValueError("user_id is required to update last_grades")
    cleaned = {
        str(k).strip().upper(): str(v).strip().upper()
        for k, v in (grades or {}).items()
        if str(k).strip()
    }
    client = get_supabase()
    client.table("users").update({"last_grades": cleaned}).eq("id", user_id).execute()
    logger.info("Updated last_grades id=%s tickers=%d", user_id, len(cleaned))
