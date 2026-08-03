"""HTTP routes for Stock Agent cloud delivery + live quotes."""

from __future__ import annotations

import html
import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.api.abuse import (
    ProtectRecover,
    ProtectSnapshot,
    ProtectSubscribe,
    ProtectUnsubscribe,
)
from app.models.schemas import (
    RecoverRequest,
    RecoverResponse,
    SnapshotRequest,
    SubscribeRequest,
    SubscribeResponse,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from app.services.email_report import send_plain_email
from app.services.grading import attach_grades
from app.services.market_data import analyze_watchlist
from app.services.supabase_client import (
    OwnershipError,
    complete_subscription_recovery,
    disable_subscription_by_token,
    start_subscription_recovery,
    upsert_user_subscription,
)

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
        manageToken=str(record["manage_token"]) if record.get("manage_token") else None,
        created_at=str(record["created_at"]) if record.get("created_at") else None,
        updated_at=str(record["updated_at"]) if record.get("updated_at") else None,
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


def _unsubscribe_html(record: dict[str, Any] | None, error: str | None = None) -> str:
    if error:
        title = "Unsubscribe failed"
        body = html.escape(error)
    else:
        email = html.escape(str((record or {}).get("email") or "your address"))
        title = "Unsubscribed"
        body = (
            f"Scheduled Stock Agent emails for <strong>{email}</strong> are now off. "
            "Open the Chrome extension and use <strong>Save &amp; Subscribe</strong> "
            "again if you want them back."
        )
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
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{body}</p>
  </main>
</body>
</html>
"""


def _recover_html(record: dict[str, Any] | None, error: str | None = None) -> str:
    if error:
        title = "Recovery failed"
        body = html.escape(error)
        token_block = ""
    else:
        email = html.escape(str((record or {}).get("email") or ""))
        token = html.escape(str((record or {}).get("manage_token") or ""))
        title = "Ownership restored"
        body = (
            f"A new ownership token was issued for <strong>{email}</strong>. "
            "Open the Stock Agent extension, paste it into the Ownership token field "
            "(or it may auto-save if you use Recover from the popup), then "
            "<strong>Save &amp; Subscribe</strong>."
        )
        token_block = f"""
    <p style="margin-top:1rem;color:#e9eef4;">Your new ownership token:</p>
    <pre style="margin-top:0.5rem;padding:12px;background:#151c25;border-radius:8px;overflow:auto;font-size:0.85rem;">{token}</pre>
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
    main {{ max-width: 32rem; line-height: 1.5; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.75rem; }}
    p {{ margin: 0; color: #b7c2ce; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{body}</p>
    {token_block}
  </main>
</body>
</html>
"""


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


def _public_api_base() -> str:
    return os.getenv("PUBLIC_API_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")


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
        "POST /api/subscribe email=%s watchlist=%s frequency=%s has_token=%s",
        body.email,
        body.watchlist,
        body.schedule.frequency,
        bool(body.manageToken),
    )

    try:
        record = upsert_user_subscription(body)
        return _to_subscribe_response(record)
    except OwnershipError as exc:
        logger.warning("Subscribe ownership denied email=%s: %s", body.email, exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
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


@router.post(
    "/subscribe/recover",
    response_model=RecoverResponse,
    summary="Email a one-time link to reclaim subscription ownership",
)
async def subscribe_recover(
    body: RecoverRequest,
    _: ProtectRecover,
) -> RecoverResponse:
    """
    Always returns a generic success message (no email enumeration).
    Sends a recover link when the email exists.
    """
    logger.info("POST /api/subscribe/recover email=%s", body.email)
    try:
        _row, token = start_subscription_recovery(body.email)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Recovery start failed for email=%s", body.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Database connection failed. Check Supabase credentials and schema.",
        ) from None

    if token:
        confirm_url = (
            f"{_public_api_base()}/api/subscribe/recover/confirm"
            f"?token={quote(token, safe='')}"
        )
        mail_body = "\n".join(
            [
                "STOCK AGENT — reclaim subscription ownership",
                "",
                "Someone requested access to update your Stock Agent email schedule.",
                "If this was you, open this link within 1 hour:",
                "",
                confirm_url,
                "",
                "The page will show a new ownership token. Paste it into the Chrome",
                "extension, then Save & Subscribe.",
                "",
                "If you did not request this, you can ignore this email.",
            ]
        )
        send_plain_email(
            body.email,
            "Stock Agent — reclaim your subscription",
            mail_body,
        )

    return RecoverResponse()


@router.get(
    "/subscribe/recover/confirm",
    summary="Confirm recovery link and rotate manageToken",
    response_class=HTMLResponse,
)
async def subscribe_recover_confirm(
    _: ProtectRecover,
    token: str = Query(..., min_length=8, max_length=64),
) -> HTMLResponse:
    logger.info("GET /api/subscribe/recover/confirm token_prefix=%s", token[:8])
    try:
        record = complete_subscription_recovery(token)
        return HTMLResponse(content=_recover_html(record), status_code=200)
    except ValueError as exc:
        return HTMLResponse(
            content=_recover_html(None, error=str(exc)),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except LookupError as exc:
        return HTMLResponse(
            content=_recover_html(None, error=str(exc)),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except RuntimeError as exc:
        return HTMLResponse(
            content=_recover_html(None, error=str(exc)),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception:
        logger.exception("Recovery confirm failed")
        return HTMLResponse(
            content=_recover_html(None, error="Recovery failed. Try requesting a new link."),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


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
        return HTMLResponse(content=_unsubscribe_html(record), status_code=200)
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
    _: ProtectUnsubscribe,
    token: str | None = Query(default=None, min_length=8, max_length=64),
    body: UnsubscribeRequest | None = Body(default=None),
) -> UnsubscribeResponse:
    """
    Flip enabled=false. Accepts JSON `{ "token": "..." }` or `?token=`
    (RFC 8058 one-click List-Unsubscribe style).
    """
    resolved = (body.token if body else None) or token
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="token is required (JSON body or query string)",
        )
    logger.info("POST /api/unsubscribe token_prefix=%s", resolved[:8])
    record = _run_unsubscribe(resolved)
    return _unsubscribe_response(record)


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
        # Popup path skips slow GoogleNews; cron adds news flags later.
        quotes = attach_grades(metrics)
        return {"quotes": quotes}
    except Exception as exc:
        logger.exception("Quote snapshot failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market data fetch failed: {exc}",
        ) from exc
