"""
GitHub Actions / local cron entrypoint.

Every ~15 minutes (offset :04/:19/:34/:49):
  1. Load enabled users from Supabase
  2. Match timezone + days + preferred_hours window
  3. Skip if last_sent_at already covers that preferred-hour slot
  4. Grade watchlist (yfinance + trusted news flags)
  5. Email report via Resend (or dry-run log), then stamp last_sent_at
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow `python backend/worker/cron_dispatch.py` from repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(_BACKEND_ROOT / ".env")

from app.services.email_report import format_report_text, send_report_email
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist
from app.services.news import fetch_news_for_watchlist
from app.services.supabase_client import get_supabase, mark_user_sent

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


def dispatch_user(row: dict) -> bool:
    email = row.get("email")
    watchlist = list(row.get("watchlist") or [])
    user_id = str(row.get("id") or "")
    if not email or not watchlist:
        logger.warning("Skipping user id=%s - missing email or watchlist", row.get("id"))
        return False

    logger.info("Dispatching report to %s (%d tickers)", email, len(watchlist))
    metrics = analyze_watchlist(watchlist)
    # GoogleNews is flaky on CI / datacenter IPs; skip unless explicitly enabled.
    skip_news = os.getenv("GITHUB_ACTIONS") or os.getenv("STOCK_AGENT_SKIP_NEWS", "").lower() in {
        "1",
        "true",
        "yes",
    }
    news_flags: dict[str, list[str]] = {}
    if not skip_news:
        try:
            news_flags = fetch_news_for_watchlist(watchlist)
        except Exception:
            logger.exception("News fetch failed; continuing without news flags")
    quotes = attach_grades(metrics, news_flags)
    body = format_report_text(email, quotes)
    sent_at = datetime.now(timezone.utc)
    subject = f"Stock Agent Report - {sent_at.strftime('%Y-%m-%d')}"
    ok = send_report_email(email, subject, body)
    if not ok and os.getenv("GITHUB_ACTIONS"):
        print(
            f"::error::Email send failed for {email}. Check RESEND_API_KEY and REPORT_FROM_EMAIL secrets.",
            flush=True,
        )
    if ok and user_id:
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
    return ok


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

    logger.info(
        "Matched %d user(s) for delivery (dedupe_skips=%d)",
        len(matched),
        skipped_dedupe,
    )
    failures = 0
    for row, preferred in matched:
        try:
            ok = dispatch_user(row)
            if not ok:
                failures += 1
        except Exception:
            failures += 1
            logger.exception("Dispatch failed for %s", row.get("email"))

    logger.info("Cron complete failures=%d / matched=%d", failures, len(matched))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
