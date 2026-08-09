"""
Due-email dispatcher (hybrid B).

Preferred send time is a deadline:
  - Early window: [preferred − DISPATCH_EARLY_MINUTES, preferred] → send
  - Overdue catch-up: (preferred, preferred + DISPATCH_LATE_MINUTES] → send once
  - Slot dedupe via last_sent_at stamped at the preferred UTC instant
  - Daily cap: at most MAX_SEND_TIMES successful sends per user local calendar day
    (independent of schedule edits)

HTTP: POST /api/internal/dispatch-due with X-Dispatch-Secret
CLI:  python backend/worker/cron_dispatch.py
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.models.schemas import MAX_SEND_TIMES
from app.services.email_report import (
    build_report_subject,
    build_unsubscribe_url,
    format_report_html,
    format_report_text,
    send_plain_email,
    send_report_email,
)
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist, resolve_currency
from app.services.news import fetch_news_for_watchlist
from app.services.supabase_client import (
    ensure_unsubscribe_token,
    get_supabase,
    insert_delivery_log,
    record_successful_send,
)

logger = logging.getLogger("stock_agent.dispatch")


def _env_minutes(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def early_minutes() -> int:
    """Start sending this many minutes before the user's preferred time."""
    return _env_minutes("DISPATCH_EARLY_MINUTES", 10)


def late_minutes() -> int:
    """Catch-up window after preferred time (no silent skip)."""
    return _env_minutes("DISPATCH_LATE_MINUTES", 120)


# Common non-IANA labels → real zones (bad TZ previously fell back to UTC and missed slots).
_TZ_ALIASES = {
    # Canada
    "toronto": "America/Toronto",
    "america/toronto": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "halifax": "America/Halifax",
    "calgary": "America/Edmonton",
    "edmonton": "America/Edmonton",
    "winnipeg": "America/Winnipeg",
    "vancouver": "America/Vancouver",
    # US Eastern
    "eastern": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "new york": "America/New_York",
    "america/new_york": "America/New_York",
    # US Central
    "central": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "chicago": "America/Chicago",
    # US Mountain
    "mountain": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "denver": "America/Denver",
    # US Pacific
    "pacific": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    # India — Asia/Kolkata is the only IANA zone for all of India
    "kolkata": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "ist": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "pune": "Asia/Kolkata",
}


def resolve_timezone(name: str | None) -> tuple[str, bool]:
    """
    Return (iana_name, ok).
    ok=False means we had to fall back to UTC (matching will be wrong for local clocks).
    """
    raw = (name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(raw)
        return raw, True
    except Exception:
        pass
    alias = _TZ_ALIASES.get(raw.lower())
    if alias:
        try:
            ZoneInfo(alias)
            return alias, True
        except Exception:
            pass
    logger.warning(
        "Invalid timezone %r — falling back to UTC (sends may miss local slots)",
        raw,
    )
    return "UTC", False


def user_to_schedule(row: dict) -> dict:
    tz, _ok = resolve_timezone(row.get("timezone"))
    return {
        "frequency": row.get("schedule_frequency") or "custom",
        "days": list(row.get("preferred_days") or []),
        "times": list(row.get("preferred_hours") or []),
        "timezone": tz,
    }


def _local_now(schedule: dict, now_utc: datetime) -> datetime:
    tz_name, ok = resolve_timezone(schedule.get("timezone"))
    if not ok:
        return now_utc.astimezone(timezone.utc)
    try:
        return now_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        return now_utc.astimezone(timezone.utc)


def _normalize_schedule_days(days: object) -> set[int]:
    """Coerce preferred_days from DB (may be strings) to JS weekday ints."""
    out: set[int] = set()
    if not isinstance(days, (list, tuple, set)):
        return out
    for raw in days:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            out.add(day)
    return out


def _day_matches(schedule: dict, local_dt: datetime) -> bool:
    js_weekday = (local_dt.weekday() + 1) % 7
    days = _normalize_schedule_days(schedule.get("days"))
    frequency = schedule.get("frequency") or "custom"

    if frequency == "daily":
        return True
    if frequency == "weekdays":
        return js_weekday in {1, 2, 3, 4, 5}
    return js_weekday in days


def matching_due_slot(
    schedule: dict, now_utc: datetime | None = None
) -> datetime | None:
    """
    Return the preferred local send datetime if due now.

    Early:  preferred − early ≤ now ≤ preferred
    Late:   preferred < now ≤ preferred + late
    Checks today and yesterday so overdue can cross midnight.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = _local_now(schedule, now_utc)
    early = timedelta(minutes=early_minutes())
    late = timedelta(minutes=late_minutes())
    times = schedule.get("times") or ["09:00"]

    # (priority, preferred) — 0=early preferred, 1=overdue
    candidates: list[tuple[int, datetime]] = []

    for time_str in times:
        try:
            hour_s, minute_s = str(time_str).split(":")
            target_hour, target_minute = int(hour_s), int(minute_s)
        except ValueError:
            continue

        for day_offset in (0, -1):
            base = local_now + timedelta(days=day_offset)
            preferred = base.replace(
                hour=target_hour,
                minute=target_minute,
                second=0,
                microsecond=0,
            )
            if not _day_matches(schedule, preferred):
                continue

            early_start = preferred - early
            late_end = preferred + late
            if early_start <= local_now <= preferred:
                candidates.append((0, preferred))
            elif preferred < local_now <= late_end:
                candidates.append((1, preferred))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def matching_preferred_slot(
    schedule: dict, now_utc: datetime | None = None
) -> datetime | None:
    """Alias for matching_due_slot (hybrid B early + overdue windows)."""
    return matching_due_slot(schedule, now_utc)


def schedule_matches(schedule: dict, now_utc: datetime | None = None) -> bool:
    """True when a preferred slot is in the early or overdue catch-up window."""
    return matching_due_slot(schedule, now_utc) is not None


def parse_timestamptz(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Supabase may return "...+00:00" or "...Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse last_sent_at=%r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def already_sent_for_slot(row: dict, preferred_local: datetime) -> bool:
    """
    Skip if we already successfully emailed for this preferred-hour slot.

    last_sent_at is stamped at the preferred UTC instant (even for early sends),
    so a 09:00 send does not block a later 17:00 slot.
    """
    last = parse_timestamptz(row.get("last_sent_at"))
    if last is None:
        return False
    preferred_utc = preferred_local.astimezone(timezone.utc)
    return last >= preferred_utc


def user_local_calendar_day(row: dict, now_utc: datetime) -> str:
    """YYYY-MM-DD in the user's timezone (for daily send caps)."""
    schedule = user_to_schedule(row)
    return _local_now(schedule, now_utc).date().isoformat()


def daily_send_on_value(row: dict) -> str | None:
    value = row.get("daily_send_on")
    if value is None or value == "":
        return None
    return str(value)[:10]


def daily_send_limit_reached(row: dict, now_utc: datetime) -> bool:
    """
    True when the user already got MAX_SEND_TIMES successful emails today
    (local calendar day). Blocks abuse via editing preferred times after each send.
    """
    today = user_local_calendar_day(row, now_utc)
    if daily_send_on_value(row) != today:
        return False
    try:
        count = int(row.get("daily_send_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return count >= MAX_SEND_TIMES


def load_enabled_users() -> list[dict]:
    client = get_supabase()
    result = client.table("users").select("*").eq("enabled", True).execute()
    data = result.data or []
    logger.info("Loaded %d enabled users", len(data))
    return data


def normalize_watchlist(raw: list | None) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        ticker = str(item).strip().upper()
        if ticker and ticker not in out:
            out.append(ticker)
    return out


def collect_unique_tickers(rows: list[dict]) -> list[str]:
    """Union of watchlists for users matched this tick (stable order)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        for ticker in normalize_watchlist(row.get("watchlist")):
            if ticker not in seen:
                seen.add(ticker)
                ordered.append(ticker)
    return ordered


def _should_skip_news() -> bool:
    """News runs in CI via Yahoo RSS; only skip when explicitly requested."""
    return os.getenv("STOCK_AGENT_SKIP_NEWS", "").lower() in {"1", "true", "yes"}


def build_shared_quote_cache(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """
    Fetch + grade each ticker once for this cron tick.
    Shared across all matched users so overlapping watchlists do not re-hit yfinance.
    """
    if not tickers:
        return {}

    logger.info("Building shared quote cache for %d unique ticker(s)", len(tickers))
    metrics = analyze_watchlist(tickers, max_tickers=None)
    news_flags: dict[str, list[str]] = {}
    if not _should_skip_news():
        try:
            news_flags = fetch_news_for_watchlist(tickers)
        except Exception:
            logger.exception("News fetch failed; continuing without news flags")
    graded = attach_grades(metrics, news_flags)
    cache = {str(q.get("ticker", "")).upper(): q for q in graded if q.get("ticker")}
    logger.info("Shared quote cache ready size=%d", len(cache))
    return cache


def quotes_from_cache(
    watchlist: list[str], cache: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Preserve each user's watchlist order; stub any rare cache miss."""
    quotes: list[dict[str, Any]] = []
    for ticker in watchlist:
        key = ticker.upper()
        hit = cache.get(key)
        if hit is not None:
            quotes.append(hit)
            continue
        quotes.append(
            {
                "ticker": key,
                "price": None,
                "currency": resolve_currency(key),
                "verdict": "n/a",
                "grade": "HOLD",
                "notes": ["No shared cache entry for this ticker in this cron tick."],
                "error": "cache_miss",
            }
        )
    return quotes


def _notify_delivery_failure(email: str, error: str | None) -> None:
    """Best-effort notice so failures are not silent for the user."""
    detail = (error or "unknown error").strip()
    if len(detail) > 400:
        detail = detail[:397] + "..."
    body = "\n".join(
        [
            "Stock Agent could not deliver your scheduled report.",
            "",
            f"Details: {detail}",
            "",
            "We will retry on the next dispatcher check while your send slot",
            "is still in the catch-up window. If this keeps happening, check",
            "that your subscription is enabled in the Chrome extension.",
            "",
            "This is an automated message.",
        ]
    )
    try:
        send_plain_email(
            str(email),
            "Stock Agent — delivery failed",
            body,
        )
    except Exception:
        logger.exception("Failed to send delivery-failure notice to %s", email)


def dispatch_user(
    row: dict,
    quote_cache: dict[str, dict[str, Any]],
    preferred_local: datetime | None = None,
) -> bool:
    email = row.get("email")
    watchlist = normalize_watchlist(row.get("watchlist"))
    user_id = str(row.get("id") or "")
    if not email or not watchlist:
        logger.warning("Skipping user id=%s - missing email or watchlist", row.get("id"))
        return False

    logger.info(
        "Dispatching report to %s (%d tickers, shared cache, slot=%s)",
        email,
        len(watchlist),
        preferred_local.isoformat() if preferred_local else "n/a",
    )
    quotes = quotes_from_cache(watchlist, quote_cache)
    unsubscribe_url = None
    try:
        token = ensure_unsubscribe_token(row)
        unsubscribe_url = build_unsubscribe_url(token)
    except Exception:
        logger.exception(
            "Could not build unsubscribe link for %s; sending without it",
            email,
        )

    sent_at = datetime.now(timezone.utc)
    # Subject date = user's local calendar day (UTC made 8–11 PM ET look like "tomorrow").
    if preferred_local is not None:
        report_day = preferred_local.date()
    else:
        report_day = _local_now(user_to_schedule(row), sent_at).date()
    subject = build_report_subject(report_day, quotes, preferred_time=preferred_local)
    text_body = format_report_text(email, quotes, unsubscribe_url=unsubscribe_url)
    html_body = format_report_html(email, quotes, unsubscribe_url=unsubscribe_url)

    result = send_report_email(
        email,
        subject,
        text_body,
        unsubscribe_url=unsubscribe_url,
        html_body=html_body,
    )
    try:
        insert_delivery_log(
            email=str(email),
            status=result.status,
            user_id=user_id or None,
            resend_id=result.resend_id,
            subject=subject,
            ticker_count=len(watchlist),
            error=result.error,
        )
    except Exception:
        logger.exception(
            "Failed to write delivery_logs for %s status=%s",
            email,
            result.status,
        )

    if not result.ok:
        if os.getenv("GITHUB_ACTIONS"):
            print(
                f"::error::Email send failed for {email}. Check RESEND_API_KEY and REPORT_FROM_EMAIL secrets.",
                flush=True,
            )
        _notify_delivery_failure(str(email), result.error)

    if result.ok and user_id:
        # Stamp the slot's preferred instant so early sends dedupe correctly.
        stamp = (
            preferred_local.astimezone(timezone.utc)
            if preferred_local is not None
            else sent_at
        )
        local_day = user_local_calendar_day(row, sent_at)
        try:
            prev_count = int(row.get("daily_send_count") or 0)
        except (TypeError, ValueError):
            prev_count = 0
        try:
            record_successful_send(
                user_id,
                local_day=local_day,
                previous_day=daily_send_on_value(row),
                previous_count=prev_count,
                slot_stamp=stamp,
            )
        except Exception:
            logger.exception(
                "Email sent to %s but failed to persist send record id=%s",
                email,
                user_id,
            )
    return result.ok


def dispatch_worker_count(job_count: int) -> int:
    """Parallel email workers; override with CRON_DISPATCH_WORKERS (1–32)."""
    raw = os.getenv("CRON_DISPATCH_WORKERS", "8").strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = 8
    configured = max(1, min(configured, 32))
    return max(1, min(configured, job_count))


def fanout_dispatch(
    matched: list[tuple[dict, datetime]],
    quote_cache: dict[str, dict[str, Any]],
) -> int:
    """
    Send reports in parallel so one slow Resend/Supabase call does not stall
    the rest of the tick. Returns failure count.
    """
    if not matched:
        return 0

    workers = dispatch_worker_count(len(matched))
    logger.info(
        "Fan-out dispatch users=%d workers=%d",
        len(matched),
        workers,
    )
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(dispatch_user, row, quote_cache, preferred): (row, preferred)
            for row, preferred in matched
        }
        for fut in as_completed(futures):
            row, _preferred = futures[fut]
            try:
                ok = fut.result()
                if not ok:
                    failures += 1
            except Exception:
                failures += 1
                logger.exception("Dispatch failed for %s", row.get("email"))
    return failures


def evaluate_due_now(
    row: dict,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """
    Cheap gate check (no yfinance / Resend): is this user inside a send window?
    status: due | not_due | disabled | empty_watchlist | daily_cap | already_sent
    """
    now = now_utc or datetime.now(timezone.utc)
    if not row.get("enabled", True):
        return {"status": "disabled", "preferred": None}
    if not normalize_watchlist(row.get("watchlist")):
        return {"status": "empty_watchlist", "preferred": None}

    preferred = matching_due_slot(user_to_schedule(row), now)
    if preferred is None:
        return {"status": "not_due", "preferred": None}
    if daily_send_limit_reached(row, now):
        return {"status": "daily_cap", "preferred": preferred}
    if already_sent_for_slot(row, preferred):
        return {"status": "already_sent", "preferred": preferred}
    return {"status": "due", "preferred": preferred}


def dispatch_if_due_now(
    row: dict,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """
    If this user is inside the early/overdue window right now, send once.
    Used by Save & Subscribe so users do not wait for the next GitHub cron tick.
    """
    now = now_utc or datetime.now(timezone.utc)
    email = row.get("email")
    gate = evaluate_due_now(row, now)
    status = str(gate.get("status") or "not_due")
    preferred = gate.get("preferred")

    if status != "due" or preferred is None:
        logger.info(
            "Subscribe immediate-send skip email=%s reason=%s",
            email,
            status,
        )
        return {"attempted": False, "sent": False, "status": status}

    logger.info(
        "Subscribe immediate-send starting email=%s slot=%s",
        email,
        preferred.isoformat(),
    )
    try:
        watchlist = normalize_watchlist(row.get("watchlist"))
        cache = build_shared_quote_cache(watchlist)
        ok = dispatch_user(row, cache, preferred)
    except Exception:
        logger.exception("Subscribe immediate-send crashed email=%s", email)
        return {"attempted": True, "sent": False, "status": "failed"}

    if ok:
        logger.info("Subscribe immediate-send OK email=%s", email)
        return {
            "attempted": True,
            "sent": True,
            "status": "sent",
            "slot": preferred.isoformat(),
        }

    logger.warning("Subscribe immediate-send failed email=%s", email)
    return {"attempted": True, "sent": False, "status": "failed"}


def run_due_dispatch(now_utc: datetime | None = None) -> dict[str, Any]:
    """
    Load enabled users, send anyone in early or overdue windows.
    Safe to call from HTTP (hybrid B) or CLI.
    """
    now = now_utc or datetime.now(timezone.utc)
    logger.info(
        "Dispatch-due tick at %s (early=%dm late=%dm)",
        now.isoformat(),
        early_minutes(),
        late_minutes(),
    )

    users = load_enabled_users()
    matched: list[tuple[dict, datetime]] = []
    skipped_dedupe = 0
    skipped_daily_cap = 0
    not_due = 0
    invalid_tz = 0
    empty_watchlist = 0
    for row in users:
        raw_tz = row.get("timezone")
        _resolved, tz_ok = resolve_timezone(raw_tz if isinstance(raw_tz, str) else None)
        if not tz_ok:
            invalid_tz += 1
        schedule = user_to_schedule(row)
        preferred = matching_due_slot(schedule, now)
        if preferred is None:
            not_due += 1
            continue
        if daily_send_limit_reached(row, now):
            skipped_daily_cap += 1
            logger.info(
                "Skip daily cap email=%s day=%s count=%s/%s",
                row.get("email"),
                user_local_calendar_day(row, now),
                row.get("daily_send_count"),
                MAX_SEND_TIMES,
            )
            continue
        if already_sent_for_slot(row, preferred):
            skipped_dedupe += 1
            logger.info(
                "Skip dedupe email=%s slot=%s last_sent_at=%s",
                row.get("email"),
                preferred.isoformat(),
                row.get("last_sent_at"),
            )
            continue
        if not normalize_watchlist(row.get("watchlist")):
            empty_watchlist += 1
            logger.warning(
                "Due but empty watchlist email=%s slot=%s — skip send",
                row.get("email"),
                preferred.isoformat(),
            )
            continue
        matched.append((row, preferred))

    if not matched:
        logger.info(
            "No users due for delivery (enabled=%d not_due=%d dedupe=%d "
            "daily_cap=%d invalid_tz=%d empty_watchlist=%d) — idle exit.",
            len(users),
            not_due,
            skipped_dedupe,
            skipped_daily_cap,
            invalid_tz,
            empty_watchlist,
        )
        return {
            "ok": True,
            "matched": 0,
            "sent_ok": 0,
            "failures": 0,
            "dedupe_skips": skipped_dedupe,
            "daily_cap_skips": skipped_daily_cap,
            "idle": True,
            "enabled_users": len(users),
            "not_due": not_due,
            "invalid_timezone": invalid_tz,
            "empty_watchlist": empty_watchlist,
        }

    matched_rows = [row for row, _ in matched]
    unique_tickers = collect_unique_tickers(matched_rows)
    logger.info(
        "Matched %d user(s) for delivery (dedupe_skips=%d daily_cap_skips=%d, unique_tickers=%d)",
        len(matched),
        skipped_dedupe,
        skipped_daily_cap,
        len(unique_tickers),
    )

    quote_cache = build_shared_quote_cache(unique_tickers)
    failures = fanout_dispatch(matched, quote_cache)
    sent_ok = len(matched) - failures
    logger.info("Dispatch-due complete failures=%d / matched=%d", failures, len(matched))
    return {
        "ok": failures == 0,
        "matched": len(matched),
        "sent_ok": sent_ok,
        "failures": failures,
        "dedupe_skips": skipped_dedupe,
        "daily_cap_skips": skipped_daily_cap,
        "idle": False,
    }
