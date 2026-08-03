"""
GitHub Actions / local cron entrypoint.

Every ~15 minutes (offset :04/:19/:34/:49):
  1. Load enabled users from Supabase
  2. Match timezone + days + preferred_hours window
  3. Skip if last_sent_at already covers that preferred-hour slot
  4. Grade each unique ticker once (shared quote cache for this tick)
  5. Fan-out email sends across matched users (parallel workers)
  6. Stamp last_sent_at after each successful send
"""

from __future__ import annotations

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Allow `python backend/worker/cron_dispatch.py` from repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(_BACKEND_ROOT / ".env")

from app.services.email_report import (
    build_unsubscribe_url,
    format_report_text,
    send_report_email,
)
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist
from app.services.news import fetch_news_for_watchlist
from app.services.supabase_client import (
    ensure_unsubscribe_token,
    get_supabase,
    insert_delivery_log,
    mark_user_sent,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("stock_agent.cron")


def user_to_schedule(row: dict) -> dict:
    return {
        "frequency": row.get("schedule_frequency") or "custom",
        "days": list(row.get("preferred_days") or []),
        "times": list(row.get("preferred_hours") or []),
        "timezone": row.get("timezone") or "UTC",
    }


def matching_preferred_slot(
    schedule: dict, now_utc: datetime | None = None
) -> datetime | None:
    """
    Return the preferred local send datetime (tz-aware) if `now` falls in its
    15-minute delivery window; otherwise None.

    Cron ticks every ~15 minutes (UTC :04/:19/:34/:49), so the window may cross
    the hour boundary (e.g. 10:52 preferred still matches at 11:04).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    tz_name = schedule.get("timezone") or "UTC"
    try:
        local_now = now_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        local_now = now_utc.astimezone(timezone.utc)

    js_weekday = (local_now.weekday() + 1) % 7
    days = schedule.get("days") or []
    frequency = schedule.get("frequency") or "custom"

    if frequency == "daily":
        day_ok = True
    elif frequency == "weekdays":
        day_ok = js_weekday in {1, 2, 3, 4, 5}
    else:
        day_ok = js_weekday in set(days)

    if not day_ok:
        return None

    times = schedule.get("times") or ["09:00"]
    for time_str in times:
        try:
            hour_s, minute_s = str(time_str).split(":")
            target_hour, target_minute = int(hour_s), int(minute_s)
        except ValueError:
            continue
        preferred = local_now.replace(
            hour=target_hour,
            minute=target_minute,
            second=0,
            microsecond=0,
        )
        delta = local_now - preferred
        if timedelta(0) <= delta < timedelta(minutes=15):
            return preferred
    return None


def schedule_matches(schedule: dict, now_utc: datetime | None = None) -> bool:
    """True when local time is within 15 minutes after a preferred send time."""
    return matching_preferred_slot(schedule, now_utc) is not None


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

    Multi-send same day still works: a 09:00 send does not block a 15:00 slot
    because last_sent_at (≈09:04) is before the 15:00 preferred instant.
    """
    last = parse_timestamptz(row.get("last_sent_at"))
    if last is None:
        return False
    preferred_utc = preferred_local.astimezone(timezone.utc)
    return last >= preferred_utc


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
                "currency": "USD",
                "verdict": "n/a",
                "grade": "HOLD",
                "notes": ["No shared cache entry for this ticker in this cron tick."],
                "error": "cache_miss",
            }
        )
    return quotes


def dispatch_user(row: dict, quote_cache: dict[str, dict[str, Any]]) -> bool:
    email = row.get("email")
    watchlist = normalize_watchlist(row.get("watchlist"))
    user_id = str(row.get("id") or "")
    if not email or not watchlist:
        logger.warning("Skipping user id=%s - missing email or watchlist", row.get("id"))
        return False

    logger.info(
        "Dispatching report to %s (%d tickers, shared cache)",
        email,
        len(watchlist),
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
    body = format_report_text(email, quotes, unsubscribe_url=unsubscribe_url)
    sent_at = datetime.now(timezone.utc)
    subject = f"Stock Agent Report - {sent_at.strftime('%Y-%m-%d')}"
    result = send_report_email(
        email, subject, body, unsubscribe_url=unsubscribe_url
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

    if not result.ok and os.getenv("GITHUB_ACTIONS"):
        print(
            f"::error::Email send failed for {email}. Check RESEND_API_KEY and REPORT_FROM_EMAIL secrets.",
            flush=True,
        )
    if result.ok and user_id:
        try:
            mark_user_sent(user_id, sent_at)
        except Exception:
            # Email already went out; log but do not fail the whole cron tick.
            # Next overlapping tick may retry until last_sent_at sticks.
            logger.exception(
                "Email sent to %s but failed to persist last_sent_at id=%s",
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
    matched_rows: list[dict],
    quote_cache: dict[str, dict[str, Any]],
) -> int:
    """
    Send reports in parallel so one slow Resend/Supabase call does not stall
    the rest of the tick. Returns failure count.
    """
    if not matched_rows:
        return 0

    workers = dispatch_worker_count(len(matched_rows))
    logger.info(
        "Fan-out dispatch users=%d workers=%d",
        len(matched_rows),
        workers,
    )
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(dispatch_user, row, quote_cache): row for row in matched_rows
        }
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                ok = fut.result()
                if not ok:
                    failures += 1
            except Exception:
                failures += 1
                logger.exception("Dispatch failed for %s", row.get("email"))
    return failures


def main() -> int:
    now = datetime.now(timezone.utc)
    logger.info("Cron tick at %s", now.isoformat())

    try:
        users = load_enabled_users()
    except RuntimeError as exc:
        # Missing/invalid env — surface a clear CI annotation.
        msg = str(exc)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::Supabase config error: {msg}", flush=True)
        logger.error("Failed to load users from Supabase: %s", msg)
        return 1
    except Exception:
        logger.exception("Failed to load users from Supabase")
        return 1

    matched: list[tuple[dict, datetime]] = []
    skipped_dedupe = 0
    for row in users:
        schedule = user_to_schedule(row)
        preferred = matching_preferred_slot(schedule, now)
        if preferred is None:
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
        matched.append((row, preferred))

    if not matched:
        logger.info(
            "No users due for delivery (dedupe_skips=%d) — idle exit.",
            skipped_dedupe,
        )
        return 0

    matched_rows = [row for row, _ in matched]
    unique_tickers = collect_unique_tickers(matched_rows)
    logger.info(
        "Matched %d user(s) for delivery (dedupe_skips=%d, unique_tickers=%d)",
        len(matched),
        skipped_dedupe,
        len(unique_tickers),
    )

    try:
        quote_cache = build_shared_quote_cache(unique_tickers)
    except Exception:
        logger.exception("Shared quote cache build failed")
        return 1

    failures = fanout_dispatch(matched_rows, quote_cache)

    logger.info("Cron complete failures=%d / matched=%d", failures, len(matched))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
