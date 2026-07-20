"""Build and send graded watchlist reports via Resend (or log-only dry run)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("stock_agent.email")


def format_report_text(email: str, quotes: list[dict[str, Any]]) -> str:
    lines = [
        "STOCK AGENT - SCHEDULED PORTFOLIO INTELLIGENCE",
        "=" * 46,
        f"Recipient: {email}",
        "",
    ]
    for q in quotes:
        ticker = q.get("ticker", "?")
        price = q.get("price")
        currency = q.get("currency") or "USD"
        price_s = f"{price:.2f} {currency}" if isinstance(price, (int, float)) else "n/a"
        lines.append(f"* {ticker}")
        lines.append(f"  - Price: {price_s}")
        lines.append(f"  - Grade: {q.get('verdict') or q.get('grade') or 'n/a'}")
        lines.append(f"  - Debt-to-Equity: {q.get('deRatio', 'N/A')}")
        lines.append(f"  - PEG: {q.get('pegRatio', 'N/A')}")
        lines.append(f"  - ROE trend: {q.get('roeTrend', [])}")
        lines.append(
            f"  - Above 200-SMA: {q.get('aboveSma200')} (SMA: {q.get('sma200', 'N/A')})"
        )
        lines.append(f"  - RSI: {q.get('rsi', 'N/A')}")
        lines.append(f"  - Asset class: {q.get('assetClass', 'standard')}")
        notes = q.get("notes") or []
        if notes:
            lines.append("  - Notes:")
            for note in notes:
                lines.append(f"     - {note}")
        if q.get("error"):
            lines.append(f"  - Data warning: {q['error']}")
        lines.append("")
    lines.append(
        "Privacy: this report uses tickers only - never your share counts or buy prices."
    )
    return "\n".join(lines)


def send_report_email(to_email: str, subject: str, body: str) -> bool:
    """
    Send via Resend HTTP API when RESEND_API_KEY is set.
    Otherwise log the report (dry-run) so cron still exercises the pipeline.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_addr = os.getenv("REPORT_FROM_EMAIL", "Stock Agent <onboarding@resend.dev>").strip()

    if not api_key:
        logger.warning(
            "RESEND_API_KEY unset - dry-run email to %s\nSubject: %s\n%s",
            to_email,
            subject,
            body[:2000],
        )
        return True

    payload = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        if response.status_code >= 400:
            logger.error(
                "Resend failed status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            return False
        logger.info("Resend OK to=%s id=%s", to_email, response.json().get("id"))
        return True
    except Exception:
        logger.exception("Resend request failed for %s", to_email)
        return False
