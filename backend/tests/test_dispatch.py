"""Unit tests for dispatch schedule matching and deduplication logic."""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.services.dispatch import (
    already_sent_for_slot,
    daily_send_limit_reached,
    matching_due_slot,
    normalize_watchlist,
    resolve_timezone,
    schedule_matches,
    user_local_calendar_day,
    user_to_schedule,
)


# ---------------------------------------------------------------------------
# Timezone resolution
# ---------------------------------------------------------------------------

class TestResolveTimezone:
    """Timezone alias resolution."""

    def test_valid_iana(self):
        name, ok = resolve_timezone("America/Toronto")
        assert name == "America/Toronto"
        assert ok is True

    def test_alias_toronto(self):
        name, ok = resolve_timezone("toronto")
        assert name == "America/Toronto"
        assert ok is True

    def test_alias_ist(self):
        name, ok = resolve_timezone("ist")
        assert name == "Asia/Kolkata"
        assert ok is True

    def test_alias_pacific(self):
        name, ok = resolve_timezone("pacific")
        assert name == "America/Los_Angeles"
        assert ok is True

    def test_invalid_falls_back_to_utc(self):
        name, ok = resolve_timezone("Not/A/Zone")
        assert name == "UTC"
        assert ok is False

    def test_none_defaults_to_utc(self):
        name, ok = resolve_timezone(None)
        assert name == "UTC"
        assert ok is True


# ---------------------------------------------------------------------------
# Schedule matching
# ---------------------------------------------------------------------------

class TestScheduleMatching:
    """matching_due_slot checks if now falls in a send window."""

    def test_exactly_at_preferred_time(self):
        schedule = {
            "frequency": "daily",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "times": ["14:00"],
            "timezone": "UTC",
        }
        # Exactly at 14:00 UTC on a Monday
        now = datetime(2025, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is not None

    def test_early_window(self):
        """5 minutes before preferred time → should be in early window."""
        schedule = {
            "frequency": "daily",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "times": ["09:00"],
            "timezone": "UTC",
        }
        now = datetime(2025, 8, 11, 8, 55, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is not None

    def test_too_early(self):
        """30 minutes before preferred → not in window (default early = 10 min)."""
        schedule = {
            "frequency": "daily",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "times": ["09:00"],
            "timezone": "UTC",
        }
        now = datetime(2025, 8, 11, 8, 29, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is None

    def test_overdue_window(self):
        """30 minutes after preferred → still in late catch-up window."""
        schedule = {
            "frequency": "daily",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "times": ["09:00"],
            "timezone": "UTC",
        }
        now = datetime(2025, 8, 11, 9, 30, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is not None

    def test_wrong_day_no_match(self):
        """Schedule for Saturday only, but today is Monday."""
        schedule = {
            "frequency": "weekly",
            "days": [6],  # Saturday
            "times": ["09:00"],
            "timezone": "UTC",
        }
        # Monday
        now = datetime(2025, 8, 11, 9, 0, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is None

    def test_weekdays_schedule(self):
        """Weekdays schedule matches on Tuesday."""
        schedule = {
            "frequency": "weekdays",
            "days": [1, 2, 3, 4, 5],
            "times": ["09:00"],
            "timezone": "UTC",
        }
        # Tuesday
        now = datetime(2025, 8, 12, 9, 0, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is not None

    def test_timezone_conversion(self):
        """9:00 AM Eastern → 13:00 UTC. Check at 13:00 UTC."""
        schedule = {
            "frequency": "daily",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "times": ["09:00"],
            "timezone": "America/New_York",
        }
        # 13:00 UTC = 9:00 AM EDT
        now = datetime(2025, 8, 11, 13, 0, 0, tzinfo=timezone.utc)
        slot = matching_due_slot(schedule, now)
        assert slot is not None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Slot deduplication prevents double-sends."""

    def test_not_sent_yet(self):
        row = {"last_sent_at": None}
        preferred = datetime(2025, 8, 11, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert already_sent_for_slot(row, preferred) is False

    def test_already_sent_for_this_slot(self):
        # Stamp at the preferred instant
        row = {"last_sent_at": "2025-08-11T09:00:00+00:00"}
        preferred = datetime(2025, 8, 11, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert already_sent_for_slot(row, preferred) is True

    def test_sent_for_earlier_slot(self):
        """Sent at 09:00, now checking 17:00 → not already sent."""
        row = {"last_sent_at": "2025-08-11T09:00:00+00:00"}
        preferred = datetime(2025, 8, 11, 17, 0, tzinfo=ZoneInfo("UTC"))
        assert already_sent_for_slot(row, preferred) is False


# ---------------------------------------------------------------------------
# Daily send cap
# ---------------------------------------------------------------------------

class TestDailySendCap:
    """Max 2 emails per user per local calendar day."""

    def test_under_cap(self):
        row = {
            "daily_send_on": "2025-08-11",
            "daily_send_count": 1,
            "timezone": "UTC",
            "schedule_frequency": "daily",
            "preferred_days": [0, 1, 2, 3, 4, 5, 6],
            "preferred_hours": ["09:00", "17:00"],
        }
        now = datetime(2025, 8, 11, 17, 0, tzinfo=timezone.utc)
        assert daily_send_limit_reached(row, now) is False

    def test_at_cap(self):
        row = {
            "daily_send_on": "2025-08-11",
            "daily_send_count": 2,
            "timezone": "UTC",
            "schedule_frequency": "daily",
            "preferred_days": [0, 1, 2, 3, 4, 5, 6],
            "preferred_hours": ["09:00", "17:00"],
        }
        now = datetime(2025, 8, 11, 17, 0, tzinfo=timezone.utc)
        assert daily_send_limit_reached(row, now) is True

    def test_different_day_resets(self):
        row = {
            "daily_send_on": "2025-08-10",  # yesterday
            "daily_send_count": 2,
            "timezone": "UTC",
            "schedule_frequency": "daily",
            "preferred_days": [0, 1, 2, 3, 4, 5, 6],
            "preferred_hours": ["09:00"],
        }
        now = datetime(2025, 8, 11, 9, 0, tzinfo=timezone.utc)
        assert daily_send_limit_reached(row, now) is False


# ---------------------------------------------------------------------------
# Watchlist normalization
# ---------------------------------------------------------------------------

class TestNormalizeWatchlist:
    """normalize_watchlist dedupes, uppercases, filters empty."""

    def test_basic(self):
        assert normalize_watchlist(["nvda", "AAPL", "nvda"]) == ["NVDA", "AAPL"]

    def test_empty(self):
        assert normalize_watchlist(None) == []
        assert normalize_watchlist([]) == []

    def test_whitespace_stripped(self):
        assert normalize_watchlist(["  SHOP.TO  ", "TD.TO"]) == ["SHOP.TO", "TD.TO"]
