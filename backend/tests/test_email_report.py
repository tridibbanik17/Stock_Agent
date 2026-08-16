"""Unit tests for email report formatting (no network)."""

import pytest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.email_report import (
    build_report_subject,
    build_unsubscribe_url,
    diff_grades,
    format_report_html,
    format_report_text,
    grades_map_from_quotes,
    quote_grade,
)


# ---------------------------------------------------------------------------
# Grade label parsing
# ---------------------------------------------------------------------------

class TestQuoteGrade:
    """quote_grade normalizes verdict strings to standard labels."""

    def test_strong_buy(self):
        assert quote_grade({"grade": "STRONG_BUY"}) == "STRONG_BUY"
        assert quote_grade({"verdict": "STRONG BUY (5/5)"}) == "STRONG_BUY"

    def test_hold(self):
        assert quote_grade({"grade": "HOLD"}) == "HOLD"
        assert quote_grade({"verdict": "HOLD (3/5)"}) == "HOLD"

    def test_avoid(self):
        assert quote_grade({"grade": "AVOID"}) == "AVOID"
        assert quote_grade({"verdict": "AVOID (1/5)"}) == "AVOID"


# ---------------------------------------------------------------------------
# Subject line
# ---------------------------------------------------------------------------

class TestBuildSubject:
    """Subject includes date + grade summary."""

    def test_basic_subject(self):
        quotes = [
            {"grade": "STRONG_BUY", "ticker": "NVDA"},
            {"grade": "HOLD", "ticker": "AAPL"},
            {"grade": "AVOID", "ticker": "TSLA"},
        ]
        subject = build_report_subject(date(2025, 8, 11), quotes)
        assert "Stock Agent" in subject
        assert "Aug" in subject
        assert "11" in subject
        assert "1 strong buy" in subject
        assert "1 hold" in subject
        assert "1 avoid" in subject

    def test_subject_with_preferred_time(self):
        quotes = [{"grade": "HOLD", "ticker": "X"}]
        preferred = datetime(2025, 8, 11, 9, 0, tzinfo=ZoneInfo("UTC"))
        subject = build_report_subject(date(2025, 8, 11), quotes, preferred_time=preferred)
        assert "9:00" in subject or "9:00" in subject


# ---------------------------------------------------------------------------
# Unsubscribe URL
# ---------------------------------------------------------------------------

class TestUnsubscribeUrl:
    """Unsubscribe URL construction."""

    def test_default_base(self):
        url = build_unsubscribe_url("abc-123", base_url="https://example.com")
        assert url == "https://example.com/api/unsubscribe?token=abc-123"

    def test_trailing_slash_stripped(self):
        url = build_unsubscribe_url("tok", base_url="https://api.test.com/")
        assert "//" not in url.split("://")[1]


# ---------------------------------------------------------------------------
# Grade diff
# ---------------------------------------------------------------------------

class TestDiffGrades:
    """diff_grades detects grade changes between runs."""

    def test_no_change(self):
        prev = {"NVDA": "STRONG_BUY", "AAPL": "HOLD"}
        curr = {"NVDA": "STRONG_BUY", "AAPL": "HOLD"}
        assert diff_grades(prev, curr) == []

    def test_grade_flip(self):
        prev = {"NVDA": "HOLD"}
        curr = {"NVDA": "STRONG_BUY"}
        changes = diff_grades(prev, curr)
        assert len(changes) == 1
        assert changes[0]["ticker"] == "NVDA"
        assert changes[0]["from"] == "HOLD"
        assert changes[0]["to"] == "STRONG_BUY"

    def test_new_ticker_not_a_flip(self):
        """First time seeing a ticker isn't a grade 'change'."""
        prev = {"NVDA": "HOLD"}
        curr = {"NVDA": "HOLD", "TSLA": "AVOID"}
        assert diff_grades(prev, curr) == []

    def test_empty_previous(self):
        curr = {"NVDA": "STRONG_BUY"}
        assert diff_grades(None, curr) == []
        assert diff_grades({}, curr) == []


# ---------------------------------------------------------------------------
# Report formatting (smoke tests)
# ---------------------------------------------------------------------------

class TestFormatReport:
    """Ensure report generation doesn't crash and includes key elements."""

    def _sample_quotes(self):
        return [
            {
                "ticker": "NVDA",
                "price": 130.50,
                "currency": "USD",
                "grade": "STRONG_BUY",
                "verdict": "STRONG BUY (5/5)",
                "score": 5,
                "deRatio": 0.41,
                "pegRatio": 0.6,
                "rsi": 55.0,
                "aboveSma200": True,
                "assetClass": "growth_tech",
                "notes": ["Fundamentals align with momentum."],
                "newsRisks": [],
            },
            {
                "ticker": "TSLA",
                "price": 250.00,
                "currency": "USD",
                "grade": "AVOID",
                "verdict": "AVOID (1/5)",
                "score": 1,
                "deRatio": 0.18,
                "pegRatio": 5.0,
                "rsi": 72.0,
                "aboveSma200": False,
                "assetClass": "growth_tech",
                "notes": ["Growth multiple stretched.", "Price below 200-day SMA."],
                "newsRisks": [],
            },
        ]

    def test_text_report_contains_tickers(self):
        text = format_report_text("test@x.com", self._sample_quotes())
        assert "NVDA" in text
        assert "TSLA" in text
        assert "STRONG BUY" in text
        assert "AVOID" in text

    def test_html_report_contains_tickers(self):
        html = format_report_html("test@x.com", self._sample_quotes())
        assert "NVDA" in html
        assert "TSLA" in html
        assert "<html" in html.lower()

    def test_unsubscribe_link_in_report(self):
        text = format_report_text(
            "test@x.com",
            self._sample_quotes(),
            unsubscribe_url="https://example.com/api/unsubscribe?token=abc",
        )
        assert "unsubscribe" in text.lower()
        assert "https://example.com/api/unsubscribe?token=abc" in text

    def test_html_report_with_unsubscribe(self):
        html = format_report_html(
            "test@x.com",
            self._sample_quotes(),
            unsubscribe_url="https://example.com/api/unsubscribe?token=abc",
        )
        assert "unsubscribe" in html.lower()
