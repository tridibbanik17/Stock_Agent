"""Build and send graded watchlist reports via Resend (or log-only dry run)."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("stock_agent.email")


def _plain_ticker(ticker: str) -> str:
    """
    Stop Gmail/Outlook from auto-linking symbols like BCE.TO as https://bce.to.
    Insert a zero-width space after each dot (display looks the same).
    """
    return str(ticker or "").replace(".", ".\u200b")


def build_unsubscribe_url(token: str, base_url: str | None = None) -> str:
    """One-click GET link embedded in report emails."""
    base = (base_url or os.getenv("PUBLIC_API_BASE_URL", "http://127.0.0.1:8000")).strip()
    base = base.rstrip("/")
    return f"{base}/api/unsubscribe?token={quote(str(token), safe='')}"


def _metric_glossary() -> list[str]:
    """Plain-language guide aligned with Stock Agent grading rules."""
    return [
        "METRIC GUIDE (simple definitions)",
        "-" * 46,
        "Grade: overall score from 0-5 checks. 4-5 = STRONG BUY, 3 = HOLD, 0-2 = AVOID.",
        "",
        "Debt-to-Equity (D/E): how much debt vs shareholder equity.",
        "  Lower is usually safer. We like under ~1.5 for most stocks",
        "  (looser for capital-heavy names like telecom).",
        "",
        "PEG (Price/Earnings to Growth): valuation vs expected earnings growth.",
        "  Lower can mean cheaper growth. We like under ~1.0 generally,",
        "  under ~1.5 for growth tech.",
        "",
        "ROE (Return on Equity): profit made on shareholder money.",
        "  Higher is usually better. We like latest ROE above ~15%",
        "  (list shows recent years; falling ROE triggers a warning).",
        "",
        "200-SMA (200-day Simple Moving Average): long-term trend line.",
        "  Price above it = healthier uptrend; below = weaker trend.",
        "",
        "RSI (Relative Strength Index, 0-100): recent momentum / heat.",
        "  Under ~35 can look oversold (possible bounce); over ~70 can look overbought.",
        "",
        "Asset class: derived from yfinance sector/industry/quoteType",
        "  (growth_tech, crypto_proxy, capital_intensive, or standard).",
        "",
        "Notes: short explanations of why the grade leaned positive or cautious.",
        "",
        "Privacy: this report uses tickers only - never your share counts or buy prices.",
        "Not investment advice; do your own research.",
    ]


def format_report_text(
    email: str,
    quotes: list[dict[str, Any]],
    unsubscribe_url: str | None = None,
) -> str:
    lines = [
        "STOCK AGENT - SCHEDULED PORTFOLIO INTELLIGENCE",
        "=" * 46,
        f"Recipient: {email}",
        "",
    ]
    for q in quotes:
        ticker = _plain_ticker(q.get("ticker", "?"))
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

    lines.extend(_metric_glossary())
    if unsubscribe_url:
        lines.extend(
            [
                "",
                "UNSUBSCRIBE",
                "-" * 46,
                "To stop these emails (one click):",
                unsubscribe_url,
                "You can turn them back on anytime from the Stock Agent extension.",
            ]
        )
    return "\n".join(lines)


def send_plain_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email via Resend, or dry-run log when unset."""
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

    payload: dict[str, Any] = {
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
        data = {}
        try:
            data = response.json()
        except Exception:
            pass
        logger.info("Resend OK to=%s id=%s", to_email, data.get("id"))
        return True
    except Exception:
        logger.exception("Resend request failed for %s", to_email)
        return False


def send_report_email(
    to_email: str,
    subject: str,
    body: str,
    unsubscribe_url: str | None = None,
) -> bool:
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

    payload: dict[str, Any] = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    if unsubscribe_url:
        # Helps Gmail/Outlook show a native unsubscribe control.
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
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
            if os.getenv("GITHUB_ACTIONS"):
                print(
                    f"::error::Resend HTTP {response.status_code}: {response.text[:300]}",
                    flush=True,
                )
            return False
        data = {}
        try:
            data = response.json()
        except Exception:
            pass
        logger.info("Resend OK to=%s id=%s", to_email, data.get("id"))
        return True
    except Exception as exc:
        logger.exception("Resend request failed for %s", to_email)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::Resend request exception: {exc}", flush=True)
        return False
