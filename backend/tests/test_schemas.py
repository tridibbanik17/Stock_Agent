"""Unit tests for Pydantic request/response schemas."""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    ScheduleConfig,
    SnapshotRequest,
    SubscribeRequest,
)


class TestScheduleConfig:
    """Schedule validation rules."""

    def test_valid_schedule(self):
        cfg = ScheduleConfig(
            frequency="weekdays",
            days=[1, 2, 3, 4, 5],
            times=["09:00", "17:00"],
            timezone="America/New_York",
        )
        assert cfg.frequency == "weekdays"
        assert cfg.days == [1, 2, 3, 4, 5]
        assert cfg.times == ["09:00", "17:00"]

    def test_days_deduped_and_sorted(self):
        cfg = ScheduleConfig(days=[5, 1, 3, 1, 5], times=["09:00"])
        assert cfg.days == [1, 3, 5]

    def test_invalid_day_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[7], times=["09:00"])

    def test_empty_days_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[], times=["09:00"])

    def test_invalid_time_format_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[1], times=["9am"])

    def test_empty_times_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[1], times=[])

    def test_max_2_times(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[1], times=["09:00", "12:00", "17:00"])

    def test_invalid_timezone_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days=[1], times=["09:00"], timezone="NotATimezone/Fake")

    def test_utc_is_valid(self):
        cfg = ScheduleConfig(days=[6], times=["09:00"], timezone="UTC")
        assert cfg.timezone == "UTC"


class TestSubscribeRequest:
    """Subscribe payload validation."""

    def test_valid_subscribe(self):
        req = SubscribeRequest(
            email="test@example.com",
            watchlist=["NVDA", "AAPL"],
            schedule=ScheduleConfig(days=[1, 2, 3, 4, 5], times=["09:00"]),
            enabled=True,
        )
        assert req.email == "test@example.com"
        assert len(req.watchlist) == 2

    def test_email_normalized_to_lowercase(self):
        req = SubscribeRequest(
            email="Trader@Example.COM",
            watchlist=["NVDA"],
            schedule=ScheduleConfig(days=[6], times=["09:00"]),
        )
        assert req.email == "trader@example.com"

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            SubscribeRequest(email="not-an-email", watchlist=["NVDA"])

    def test_empty_watchlist_rejected(self):
        with pytest.raises(ValidationError):
            SubscribeRequest(email="a@b.com", watchlist=[])

    def test_watchlist_capped_at_25(self):
        tickers = [f"T{i:03d}" for i in range(30)]
        with pytest.raises(ValidationError):
            SubscribeRequest(email="a@b.com", watchlist=tickers)

    def test_ticker_format_validated(self):
        with pytest.raises(ValidationError):
            SubscribeRequest(email="a@b.com", watchlist=["invalid ticker!!"])

    def test_indian_ticker_format_accepted(self):
        req = SubscribeRequest(
            email="a@b.com",
            watchlist=["ADANIENTERPRISES.NS", "500325.BO"],
            schedule=ScheduleConfig(days=[1], times=["09:00"]),
        )
        assert "ADANIENTERPRISES.NS" in req.watchlist
        assert "500325.BO" in req.watchlist

    def test_extra_fields_forbidden(self):
        """Private fields must be rejected at schema level."""
        with pytest.raises(ValidationError):
            SubscribeRequest(
                email="a@b.com",
                watchlist=["NVDA"],
                holdings={"NVDA": {"shares": 100}},  # type: ignore
            )


class TestSnapshotRequest:
    """Snapshot payload validation."""

    def test_valid_snapshot(self):
        req = SnapshotRequest(watchlist=["NVDA", "AAPL", "SHOP.TO"])
        assert len(req.watchlist) == 3

    def test_empty_watchlist_allowed(self):
        req = SnapshotRequest(watchlist=[])
        assert req.watchlist == []

    def test_invalid_tickers_filtered(self):
        req = SnapshotRequest(watchlist=["NVDA", "bad ticker!!", "AAPL"])
        # Invalid tickers are silently filtered (not rejected)
        assert "NVDA" in req.watchlist
        assert "AAPL" in req.watchlist
        assert len(req.watchlist) == 2

    def test_duplicates_removed(self):
        req = SnapshotRequest(watchlist=["NVDA", "NVDA", "AAPL"])
        assert req.watchlist == ["NVDA", "AAPL"]
