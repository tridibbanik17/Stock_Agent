"""Pydantic contracts for the Stock Agent API.

Privacy: extra fields are forbidden. Shares, buy prices, and Gemini keys
are rejected at the validation layer before any database write.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_WATCHLIST = 25
MAX_SEND_TIMES = 8
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

Frequency = Literal["daily", "weekdays", "weekly", "custom"]


class ScheduleConfig(BaseModel):
    """Custom delivery window from the Chrome extension."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "frequency": "weekly",
                    "days": [6],
                    "times": ["09:00", "17:00"],
                    "timezone": "America/New_York",
                }
            ]
        },
    )

    frequency: Frequency = Field(default="weekly", examples=["weekly"])
    days: list[int] = Field(
        default_factory=lambda: [6],
        description="JS getDay(): 0=Sun … 6=Sat",
        examples=[[6]],
    )
    times: list[str] = Field(
        default_factory=lambda: ["09:00"],
        description="24h HH:MM send times (not the literal word 'string')",
        examples=[["09:00", "17:00"]],
    )
    timezone: str = Field(default="UTC", examples=["America/New_York"])

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: list[int]) -> list[int]:
        cleaned = sorted({d for d in value if isinstance(d, int) and 0 <= d <= 6})
        if not cleaned:
            raise ValueError("days must include at least one weekday (0=Sun … 6=Sat)")
        return cleaned

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if not TIME_RE.match(text):
                raise ValueError(f"invalid time '{raw}' — expected HH:MM (24h)")
            cleaned.append(text)
        cleaned = sorted(set(cleaned))[:MAX_SEND_TIMES]
        if not cleaned:
            raise ValueError("times must include at least one HH:MM value")
        return cleaned

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        tz = (value or "UTC").strip()
        if not tz or len(tz) > 64:
            raise ValueError("timezone must be a non-empty IANA string")
        return tz


class SubscribeRequest(BaseModel):
    """Cloud-safe delivery upsert payload from the extension."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "email": "trader@example.com",
                    "watchlist": ["NVDA", "AAPL", "SHOP.TO"],
                    "schedule": {
                        "frequency": "weekdays",
                        "days": [1, 2, 3, 4, 5],
                        "times": ["09:00"],
                        "timezone": "America/New_York",
                    },
                    "enabled": True,
                }
            ]
        },
    )

    email: EmailStr = Field(examples=["trader@example.com"])
    watchlist: list[str] = Field(
        default_factory=list,
        min_length=1,
        max_length=MAX_WATCHLIST,
        examples=[["NVDA", "AAPL", "SHOP.TO"]],
    )
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    enabled: bool = True

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("watchlist")
    @classmethod
    def validate_watchlist(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            ticker = str(raw).strip().upper()
            if not ticker:
                continue
            if not TICKER_RE.match(ticker):
                raise ValueError(
                    f"invalid ticker '{raw}' — use 1–12 chars (A–Z, 0–9, ., -)"
                )
            if ticker not in cleaned:
                cleaned.append(ticker)
        if not cleaned:
            raise ValueError("watchlist must contain at least one ticker")
        if len(cleaned) > MAX_WATCHLIST:
            raise ValueError(f"watchlist capped at {MAX_WATCHLIST} symbols")
        return cleaned


class SubscribeResponse(BaseModel):
    id: str
    email: str
    watchlist: list[str]
    schedule_frequency: str
    preferred_hours: list[str]
    preferred_days: list[int]
    timezone: str
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchlist: list[str] = Field(default_factory=list, max_length=MAX_WATCHLIST)

    @field_validator("watchlist")
    @classmethod
    def validate_snapshot_watchlist(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            ticker = str(raw).strip().upper()
            if ticker and TICKER_RE.match(ticker) and ticker not in cleaned:
                cleaned.append(ticker)
        return cleaned[:MAX_WATCHLIST]
