"""HTTP routes for Stock Agent cloud delivery + live quotes."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.api.abuse import (
    ProtectSnapshot,
    ProtectSubscribe,
    ProtectUnsubscribe,
)
from app.models.schemas import (
    SnapshotRequest,
    SubscribeRequest,
    SubscribeResponse,
    UnsubscribeResponse,
)
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist
from app.services.supabase_client import (
    disable_subscription_by_token,
    enable_subscription_by_token,
    upsert_user_subscription,
)

logger = logging.getLogger("stock_agent.api")

router = APIRouter()


def _to_subscribe_response(
    record: dict[str, Any],
    *,
    report_sent_now: bool = False,
    report_send_status: str | None = None,
) -> SubscribeResponse:
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
        report_sent_now=report_sent_now,
        report_send_status=report_send_status,
    )


def _unsubscribe_response(record: dict[str, Any]) -> UnsubscribeResponse:
    email = str(record.get("email") or "")
    return UnsubscribeResponse(
        ok=True,
        enabled=False,
        email=email or None,
        message="Unsubscribed. Scheduled emails are off. "
        "Open the Stock Agent extension and Save & Subscribe again to re-enable.",
    )


def _subscription_html(
    *,
    title: str,
    body_html: str,
    token: str | None = None,
    mode: str = "unsubscribed",
) -> str:
    """Simple confirmation page for browser unsubscribe / resubscribe."""
    actions = ""
    if mode == "unsubscribed" and token:
        safe_token = html.escape(token, quote=True)
        actions = f"""
        <p style="margin:1.25rem 0 0;">
          <a class="btn" href="/api/resubscribe?token={safe_token}">Resubscribe</a>
        </p>
        <p class="note">Or open the Chrome extension and click <strong>Save &amp; Subscribe</strong>.</p>
        <p class="note">If Gmail shows “You unsubscribed from …”, also undo that in Gmail or future mail may be filtered even after you resubscribe here.</p>
        """
    elif mode == "resubscribed":
        actions = """
        <p class="note">Scheduled emails are on again. If Gmail previously blocked the sender, check Spam or Gmail’s unsubscribe settings for that address.</p>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)} · Stock Agent</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: #0c1117;
      color: #e9eef4;
      padding: 24px;
    }}
    main {{
      max-width: 28rem;
      line-height: 1.5;
    }}
    h1 {{
      font-size: 1.35rem;
      margin: 0 0 0.75rem;
    }}
    p {{ margin: 0; color: #b7c2ce; }}
    p + p {{ margin-top: 0.75rem; }}
    .note {{ font-size: 0.9rem; color: #8b9aab; }}
    .btn {{
      display: inline-block;
      margin-top: 0.25rem;
      padding: 0.65rem 1rem;
      background: #2f6fed;
      color: #fff !important;
      text-decoration: none;
      border-radius: 8px;
      font-weight: 650;
    }}
    .btn:hover {{ filter: brightness(1.08); }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{body_html}</p>
    {actions}
  </main>
</body>
</html>
"""


def _unsubscribe_html(
    record: dict[str, Any] | None,
    error: str | None = None,
    *,
    token: str | None = None,
) -> str:
    if error:
        return _subscription_html(
            title="Unsubscribe failed",
            body_html=html.escape(error),
            mode="error",
        )
    email = html.escape(str((record or {}).get("email") or "your address"))
    enabled = (record or {}).get("enabled")
    status_bit = (
        "Our records now show <strong>enabled = false</strong> (emails off)."
        if enabled is False
        else "If Supabase still shows enabled = true, refresh the table — or tell us; the write may have failed."
    )
    body = (
        f"Scheduled Stock Agent emails for <strong>{email}</strong> are now off. "
        f"{status_bit}"
    )
    return _subscription_html(
        title="Unsubscribed",
        body_html=body,
        token=token,
        mode="unsubscribed",
    )


def _resubscribe_html(record: dict[str, Any] | None, error: str | None = None) -> str:
    if error:
        return _subscription_html(
            title="Resubscribe failed",
            body_html=html.escape(error),
            mode="error",
        )
    email = html.escape(str((record or {}).get("email") or "your address"))
    body = (
        f"Scheduled Stock Agent emails for <strong>{email}</strong> are on again "
        f"(<strong>enabled = true</strong>)."
    )
    return _subscription_html(
        title="Resubscribed",
        body_html=body,
        mode="resubscribed",
    )


def _run_unsubscribe(token: str) -> dict[str, Any]:
    try:
        return disable_subscription_by_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Unsubscribe failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Database connection failed. Check Supabase credentials and schema.",
        ) from exc


def _run_resubscribe(token: str) -> dict[str, Any]:
    try:
        return enable_subscription_by_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Resubscribe failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Database connection failed. Check Supabase credentials and schema.",
        ) from exc

@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert email delivery preferences",
)
async def subscribe(
    body: SubscribeRequest,
    _: ProtectSubscribe,
) -> SubscribeResponse:
    logger.info(
        "POST /api/subscribe email=%s watchlist=%s frequency=%s",
        body.email,
        body.watchlist,
        body.schedule.frequency,
    )

    try:
        record = upsert_user_subscription(body)
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

    # If due now, send BEFORE responding so the client gets truth (sent/failed),
    # not an optimistic "sending" that dies when Render freezes background work.
    report_sent_now = False
    report_send_status: str | None = "not_due"
    try:
        from app.services.dispatch import dispatch_if_due_now, evaluate_due_now

        gate = evaluate_due_now(record)
        report_send_status = str(gate.get("status") or "not_due")
        if report_send_status == "due":
            logger.info(
                "Running immediate report after subscribe email=%s",
                body.email,
            )
            result = await asyncio.to_thread(dispatch_if_due_now, record)
            report_send_status = str(result.get("status") or "failed")
            report_sent_now = bool(result.get("sent"))
            logger.info(
                "Immediate report after subscribe email=%s status=%s sent=%s",
                body.email,
                report_send_status,
                report_sent_now,
            )
    except Exception:
        logger.exception(
            "Immediate dispatch after subscribe failed email=%s",
            body.email,
        )
        report_send_status = "failed"
        report_sent_now = False

    return _to_subscribe_response(
        record,
        report_sent_now=report_sent_now,
        report_send_status=report_send_status,
    )


def _require_dispatch_secret(x_dispatch_secret: str | None) -> None:
    expected = os.getenv("DISPATCH_SECRET", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DISPATCH_SECRET is not configured on this API instance.",
        )
    provided = (x_dispatch_secret or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Dispatch-Secret.",
        )


@router.post(
    "/internal/dispatch-due",
    summary="Hybrid B: send reports for users due soon or overdue",
)
async def dispatch_due(
    x_dispatch_secret: str | None = Header(default=None, alias="X-Dispatch-Secret"),
) -> dict[str, Any]:
    """
    Called by GitHub Actions / Cloud Scheduler every few minutes.
    Sends when local time is in [preferred − early, preferred] or overdue
    within DISPATCH_LATE_MINUTES. Auth: X-Dispatch-Secret header.
    """
    _require_dispatch_secret(x_dispatch_secret)
    logger.info("POST /api/internal/dispatch-due")

    # Import inside the handler so API boot does not require worker path quirks.
    from app.services.dispatch import run_due_dispatch

    try:
        result = await asyncio.to_thread(run_due_dispatch)
        return result
    except RuntimeError as exc:
        logger.error("Dispatch-due configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Dispatch-due failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Dispatch failed: {exc}",
        ) from exc


@router.get(
    "/unsubscribe",
    summary="One-click unsubscribe (email link)",
    response_class=HTMLResponse,
)
async def unsubscribe_get(
    _: ProtectUnsubscribe,
    token: str = Query(..., min_length=8, max_length=64),
) -> HTMLResponse:
    """Browser-friendly one-click link from the report email footer."""
    logger.info("GET /api/unsubscribe token_prefix=%s", token[:8])
    try:
        record = _run_unsubscribe(token)
        return HTMLResponse(
            content=_unsubscribe_html(record, token=token),
            status_code=200,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Unsubscribe failed"
        return HTMLResponse(
            content=_unsubscribe_html(None, error=detail),
            status_code=exc.status_code,
        )


@router.post(
    "/unsubscribe",
    response_model=UnsubscribeResponse,
    summary="Disable scheduled emails by token",
)
async def unsubscribe_post(
    request: Request,
    _: ProtectUnsubscribe,
    token: str | None = Query(default=None, min_length=8, max_length=64),
) -> UnsubscribeResponse:
    """
    Flip enabled=false.

    Gmail/Outlook one-click (RFC 8058) POSTs form body
    `List-Unsubscribe=One-Click` to the List-Unsubscribe URL — token stays in
    the query string. Also accepts JSON `{ "token": "..." }`.
    """
    resolved = token
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
            if isinstance(data, dict) and data.get("token"):
                resolved = str(data["token"])
        except Exception:
            logger.warning("Unsubscribe POST JSON body unreadable; using query token")

    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="token is required (query string or JSON body)",
        )
    logger.info("POST /api/unsubscribe token_prefix=%s", resolved[:8])
    record = _run_unsubscribe(resolved)
    return _unsubscribe_response(record)


@router.get(
    "/resubscribe",
    summary="Re-enable scheduled emails (token from unsubscribe page)",
    response_class=HTMLResponse,
)
async def resubscribe_get(
    _: ProtectUnsubscribe,
    token: str = Query(..., min_length=8, max_length=64),
) -> HTMLResponse:
    """Browser button on the unsubscribe confirmation page."""
    logger.info("GET /api/resubscribe token_prefix=%s", token[:8])
    try:
        record = _run_resubscribe(token)
        return HTMLResponse(content=_resubscribe_html(record), status_code=200)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Resubscribe failed"
        return HTMLResponse(
            content=_resubscribe_html(None, error=detail),
            status_code=exc.status_code,
        )


@router.delete(
    "/unsubscribe/{token}",
    response_model=UnsubscribeResponse,
    summary="Disable scheduled emails by token (DELETE)",
)
async def unsubscribe_delete(
    token: str,
    _: ProtectUnsubscribe,
) -> UnsubscribeResponse:
    logger.info("DELETE /api/unsubscribe token_prefix=%s", token[:8])
    record = _run_unsubscribe(token)
    return _unsubscribe_response(record)


@router.post("/quotes/snapshot", summary="Live yfinance snapshot + grades")
async def quotes_snapshot(
    body: SnapshotRequest,
    _: ProtectSnapshot,
) -> dict[str, Any]:
    """
    Fetch live prices/metrics for watchlist tickers and attach grades.
    Tickers only — never accepts holdings or API keys.
    """
    if not body.watchlist:
        return {"quotes": []}

    logger.info("POST /api/quotes/snapshot count=%d", len(body.watchlist))
    try:
        metrics = analyze_watchlist(body.watchlist)
        news_by_ticker: dict[str, list] = {}
        try:
            from app.services.news import fetch_news_for_watchlist

            news_by_ticker = fetch_news_for_watchlist(body.watchlist)
        except Exception:
            logger.exception("Snapshot news fetch failed; grading without headlines")
        quotes = attach_grades(metrics, news_by_ticker)
        return {"quotes": quotes}
    except Exception as exc:
        logger.exception("Quote snapshot failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market data fetch failed: {exc}",
        ) from exc
