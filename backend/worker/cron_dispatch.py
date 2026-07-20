"""
GitHub Actions / local cron entrypoint.

Every ~15 minutes (offset :04/:19/:34/:49):
  1. Load enabled users from Supabase
  2. Match timezone + days + preferred_hours window
  3. Grade watchlist (yfinance + trusted news flags)
  4. Email report via Resend (or dry-run log)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
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
from app.services.supabase_client import get_supabase

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


def schedule_matches(schedule: dict, now_utc: datetime | None = None) -> bool:
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
        return False

    times = schedule.get("times") or ["09:00"]
    for time_str in times:
        try:
            hour_s, minute_s = str(time_str).split(":")
            target_hour, target_minute = int(hour_s), int(minute_s)
        except ValueError:
            continue
        if local_now.hour != target_hour:
            continue
        if target_minute <= local_now.minute < target_minute + 15:
            return True
    return False


def load_enabled_users() -> list[dict]:
    client = get_supabase()
    result = client.table("users").select("*").eq("enabled", True).execute()
    data = result.data or []
    logger.info("Loaded %d enabled users", len(data))
    return data


def dispatch_user(row: dict) -> bool:
    email = row.get("email")
    watchlist = list(row.get("watchlist") or [])
    if not email or not watchlist:
        logger.warning("Skipping user id=%s — missing email or watchlist", row.get("id"))
        return False

    logger.info("Dispatching report to %s (%d tickers)", email, len(watchlist))
    metrics = analyze_watchlist(watchlist)
    news_flags = fetch_news_for_watchlist(watchlist)
    quotes = attach_grades(metrics, news_flags)
    body = format_report_text(email, quotes)
    subject = f"Stock Agent Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    return send_report_email(email, subject, body)


def main() -> int:
    now = datetime.now(timezone.utc)
    logger.info("Cron tick at %s", now.isoformat())

    try:
        users = load_enabled_users()
    except Exception:
        logger.exception("Failed to load users from Supabase")
        return 1

    matched = []
    for row in users:
        schedule = user_to_schedule(row)
        if schedule_matches(schedule, now):
            matched.append(row)

    if not matched:
        logger.info("No users in the current schedule window — idle exit.")
        return 0

    logger.info("Matched %d user(s) for delivery", len(matched))
    failures = 0
    for row in matched:
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
