"""HTTP routes for Stock Agent cloud delivery + live quotes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.models.schemas import (
    SnapshotRequest,
    SubscribeRequest,
    SubscribeResponse,
)
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist
from app.services.supabase_client import upsert_user_subscription

logger = logging.getLogger("stock_agent.api")

router = APIRouter()


def _to_subscribe_response(record: dict[str, Any]) -> SubscribeResponse:
    return SubscribeResponse(
        id=str(record.get("id", "")),
        email=str(record.get("email", "")),
        watchlist=list(record.get("watchlist") or []),
        schedule_frequency=str(record.get("schedule_frequency") or "weekly"),
        preferred_hours=list(record.get("preferred_hours") or []),
        preferred_days=[int(d) for d in (record.get("preferred_days") or [])],
        timezone=str(record.get("timezone") or "UTC"),
        enabled=bool(record.get("enabled", True)),
        created_at=str(record["created_at"]) if record.get("created_at") else None,
        updated_at=str(record["updated_at"]) if record.get("updated_at") else None,
    )


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert email delivery preferences",
)
async def subscribe(body: SubscribeRequest) -> SubscribeResponse:
    logger.info(
        "POST /api/subscribe email=%s watchlist=%s frequency=%s",
        body.email,
        body.watchlist,
        body.schedule.frequency,
    )

    try:
        record = upsert_user_subscription(body)
        return _to_subscribe_response(record)
    except RuntimeError as exc:
        logger.error("Subscribe configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        logger.warning("Subscribe validation/privacy error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Subscribe failed for email=%s", body.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Database connection failed. Check Supabase credentials and schema.",
        ) from exc


@router.post("/quotes/snapshot", summary="Live yfinance snapshot + grades")
async def quotes_snapshot(body: SnapshotRequest) -> dict[str, Any]:
    """
    Fetch live prices/metrics for watchlist tickers and attach grades.
    Tickers only — never accepts holdings or API keys.
    """
    if not body.watchlist:
        return {"quotes": []}

    logger.info("POST /api/quotes/snapshot count=%d", len(body.watchlist))
    try:
        metrics = analyze_watchlist(body.watchlist)
        # Popup path skips slow GoogleNews; cron adds news flags later.
        quotes = attach_grades(metrics)
        return {"quotes": quotes}
    except Exception as exc:
        logger.exception("Quote snapshot failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market data fetch failed: {exc}",
        ) from exc
