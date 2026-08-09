"""Build and send graded watchlist reports via Resend (or log-only dry run)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("stock_agent.email")


@dataclass(frozen=True)
class SendResult:
    """Outcome of a report send attempt (for delivery_logs audit)."""

    ok: bool
    resend_id: str | None = None
    error: str | None = None
    dry_run: bool = False

    @property
    def status(self) -> str:
        if self.dry_run:
            return "dry_run"
        return "success" if self.ok else "failure"


def _plain_ticker(ticker: object) -> str:
    """
    Stop Gmail/Outlook from auto-linking symbols like BCE.TO as https://bce.to.
    Insert a zero-width space after each dot (display looks the same).
    """
    return str(ticker or "").replace(".", ".\u200b")


def _note_lines_for_email(q: dict[str, Any]) -> list[tuple[str, str | None]]:
    """
    Return (text, optional_url) for notes.
    News risk lines get a source URL when grading attached newsRisks.
    """
    notes = list(q.get("notes") or [])
    risks = q.get("newsRisks") or []
    url_by_title: dict[str, str] = {}
    for item in risks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if title and url.startswith("http"):
            url_by_title[title] = url

    out: list[tuple[str, str | None]] = []
    for note in notes:
        text = str(note)
        link: str | None = None
        if text.startswith("News risk: "):
            headline = text[len("News risk: ") :].strip()
            link = url_by_title.get(headline)
        elif text.startswith("Headline: "):
            headline = text[len("Headline: ") :].strip()
            link = url_by_title.get(headline)
        out.append((text, link))
    return out


def build_unsubscribe_url(token: str, base_url: str | None = None) -> str:
    """One-click GET link embedded in report emails."""
    base = (base_url or os.getenv("PUBLIC_API_BASE_URL", "http://127.0.0.1:8000")).strip()
    base = base.rstrip("/")
    return f"{base}/api/unsubscribe?token={quote(str(token), safe='')}"


LEGAL_DISCLAIMER = (
    "Educational tool only. Ratings are calculated via rule-based "
    "technical/fundamental heuristics and do not constitute financial advice. "
    "Past performance does not guarantee future results."
)


def _metric_glossary() -> list[str]:
    """Short plain-language footer — keep email scannable."""
    return [
        "METRIC GUIDE",
        "-" * 46,
        "Grade 4-5 = STRONG BUY, 3 = HOLD, 0-2 = AVOID.",
        "D/E = debt vs equity. PEG = valuation vs growth.",
        "RSI = momentum (0-100). 200-SMA = long-term trend.",
        "Grades use rules plus headline risk flags only.",
        "Privacy: tickers only - never share counts or buy prices.",
        "",
        "DISCLAIMER",
        "-" * 46,
        LEGAL_DISCLAIMER,
    ]


def _grade_bucket(q: dict[str, Any]) -> str:
    """avoid | hold | buy — for urgency-sorted email sections."""
    g = quote_grade(q)
    if g == "STRONG_BUY":
        return "buy"
    if g == "HOLD":
        return "hold"
    return "avoid"


def _quotes_by_urgency(quotes: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """
    Group watchlist into Buy → Hold → Avoid for scannable digests.
    Winners first (positive reinforcement), then neutral, then concerns.
    Returns [(bucket_key, section_title, quotes), ...] omitting empty buckets.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        "buy": [],
        "hold": [],
        "avoid": [],
    }
    for q in quotes:
        buckets[_grade_bucket(q)].append(q)
    titles = {
        "buy": "Buy opportunities (4–5)",
        "hold": "Watch / Hold (3)",
        "avoid": "Action / Avoid (0–2)",
    }
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for key in ("buy", "hold", "avoid"):
        if buckets[key]:
            out.append((key, titles[key], buckets[key]))
    return out


def format_report_text(
    email: str,
    quotes: list[dict[str, Any]],
    unsubscribe_url: str | None = None,
) -> str:
    lines = [
        "STOCK AGENT CHROME EXTENSION — WATCHLIST REPORT",
        "=" * 46,
        _grade_summary_line(quotes),
        _watchlist_health_line(quotes),
        "",
    ]
    for _key, title, group in _quotes_by_urgency(quotes):
        lines.append(title.upper())
        lines.append("-" * 46)
        for q in group:
            ticker = _plain_ticker(q.get("ticker", "?"))
            price = q.get("price")
            currency = q.get("currency") or "USD"
            price_s = f"{price:.2f} {currency}" if isinstance(price, (int, float)) else "n/a"
            lines.append(f"* {ticker}")
            lines.append(f"  - Price: {price_s}")
            lines.append(f"  - Grade: {q.get('verdict') or q.get('grade') or 'n/a'}")
            lines.append(f"  - Debt-to-Equity: {q.get('deRatio') if q.get('deRatio') is not None else 'n/a'}")
            lines.append(f"  - PEG: {q.get('pegRatio') if q.get('pegRatio') is not None else 'n/a'}")
            lines.append(f"  - ROE trend: {q.get('roeTrend', [])}")
            lines.append(
                f"  - Above 200-SMA: {q.get('aboveSma200') if q.get('aboveSma200') is not None else 'n/a'} "
                f"(SMA: {q.get('sma200') if q.get('sma200') is not None else 'n/a'})"
            )
            lines.append(f"  - RSI: {q.get('rsi') if q.get('rsi') is not None else 'n/a'}")
            lines.append(f"  - Asset class: {q.get('assetClass', 'standard')}")
            notes = q.get("notes") or []
            if notes:
                lines.append("  - Notes:")
                for text, link in _note_lines_for_email(q):
                    if link:
                        lines.append(f"     - {text}")
                        lines.append(f"       Read: {link}")
                    else:
                        lines.append(f"     - {text}")
            if q.get("error"):
                lines.append(f"  - Data warning: {q['error']}")
            lines.append("")
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
                "You can turn them back on anytime from the Stock Agent Chrome extension.",
            ]
        )
    return "\n".join(lines)


def quote_grade(q: dict[str, Any]) -> str:
    raw = q.get("grade") or q.get("verdict") or "n/a"
    text = str(raw).strip().upper()
    if "STRONG" in text and "BUY" in text:
        return "STRONG_BUY"
    if "HOLD" in text:
        return "HOLD"
    if "AVOID" in text:
        return "AVOID"
    return text.replace(" ", "_")[:32] or "n/a"


def grades_map_from_quotes(quotes: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for q in quotes:
        ticker = str(q.get("ticker") or "").strip().upper()
        if ticker:
            out[ticker] = quote_grade(q)
    return out


def diff_grades(
    previous: dict[str, Any] | None, current: dict[str, str]
) -> list[dict[str, str]]:
    """Return [{ticker, from, to}, ...] for tickers whose grade changed."""
    prev = previous or {}
    if not isinstance(prev, dict):
        prev = {}
    changes: list[dict[str, str]] = []
    for ticker, grade in current.items():
        old = str(prev.get(ticker) or "").strip().upper() or None
        new = str(grade or "").strip().upper()
        if old is None:
            # First sighting of a ticker is not a "flip" for digest mode.
            continue
        if old != new:
            changes.append({"ticker": ticker, "from": old, "to": new})
    return changes


def _esc(value: object) -> str:
    text = "n/a" if value is None or value == "" else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _human_asset_class(raw: object) -> str:
    key = str(raw or "standard").strip().lower()
    labels = {
        "standard": "Standard",
        "growth_tech": "Growth / tech",
        "capital_intensive": "Capital intensive",
        "crypto_proxy": "Crypto proxy",
        "index_etf": "Index / ETF",
        "banking": "Banking / financial",
        "pharma": "Pharma / biotech",
        "conglomerate": "Conglomerate",
        "cyclical": "Cyclical / commodity",
        "financial": "Financial",
        "utility": "Utility",
    }
    return labels.get(key, key.replace("_", " ").title() or "Standard")


def _human_bool(raw: object) -> str:
    if raw is True:
        return "Yes"
    if raw is False:
        return "No"
    return "n/a"


def _grade_summary_line(quotes: list[dict[str, Any]]) -> str:
    """Short scannable counts for the email intro / subject."""
    strong = hold = avoid = 0
    for q in quotes:
        g = quote_grade(q)
        if g == "STRONG_BUY":
            strong += 1
        elif g == "HOLD":
            hold += 1
        elif g == "AVOID":
            avoid += 1
    parts: list[str] = []
    if strong:
        parts.append(f"{strong} strong buy")
    if hold:
        parts.append(f"{hold} hold")
    if avoid:
        parts.append(f"{avoid} avoid")
    return " · ".join(parts) if parts else f"{len(quotes)} tickers"


def _watchlist_health_line(quotes: list[dict[str, Any]]) -> str:
    """
    Aggregate watchlist health signal:
    - % above 200-SMA (market breadth proxy)
    - Weighted health score
    """
    total = len(quotes)
    if total == 0:
        return ""

    strong = sum(1 for q in quotes if quote_grade(q) == "STRONG_BUY")
    hold = sum(1 for q in quotes if quote_grade(q) == "HOLD")
    avoid = sum(1 for q in quotes if quote_grade(q) == "AVOID")

    above_sma = sum(1 for q in quotes if q.get("aboveSma200") is True)
    sma_counted = sum(1 for q in quotes if q.get("aboveSma200") is not None)

    # Weighted health: strong=100, hold=60, avoid=20
    health_score = int(
        ((strong * 100) + (hold * 60) + (avoid * 20)) / total
    )

    parts = [f"Watchlist health: {health_score}%"]
    if sma_counted > 0:
        sma_pct = int((above_sma / sma_counted) * 100)
        parts.append(f"{sma_pct}% above 200-SMA")
        if sma_pct < 30:
            parts.append("broad market weakness")
        elif sma_pct > 70:
            parts.append("strong market breadth")

    return " · ".join(parts)


def build_report_subject(report_day, quotes: list[dict[str, Any]]) -> str:
    """Inbox-friendly subject with local date + grade mix."""
    month = report_day.strftime("%b")
    day = str(int(report_day.day))
    summary = _grade_summary_line(quotes)
    return f"Stock Agent · {month} {day} · {summary}"


def _grade_color(grade: str) -> str:
    g = str(grade or "").upper()
    if "STRONG" in g or "BUY" in g:
        return "#1f8f5f"
    if "HOLD" in g:
        return "#b8860b"
    if "AVOID" in g:
        return "#c0392b"
    return "#5a6a7a"


def format_report_html(
    email: str,
    quotes: list[dict[str, Any]],
    *,
    unsubscribe_url: str | None = None,
    changes: list[dict[str, str]] | None = None,
    no_change_digest: bool = False,
) -> str:
    """HTML body for Resend (table layout + inline styles for email clients)."""
    change_block = ""
    if changes:
        rows = "".join(
            "<tr>"
            f"<td style='padding:6px 10px;font-family:Consolas,monospace;'>{_esc(_plain_ticker(c['ticker']))}</td>"
            f"<td style='padding:6px 10px;color:{_grade_color(c['from'])};'>{_esc(c['from'])}</td>"
            f"<td style='padding:6px 10px;color:{_grade_color(c['to'])};font-weight:700;'>{_esc(c['to'])}</td>"
            "</tr>"
            for c in changes
        )
        change_block = f"""
        <h2 style="font-size:16px;margin:20px 0 8px;color:#0c1117;">Grade changes</h2>
        <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px;border:1px solid #d8e0ea;">
          <thead>
            <tr style="background:#eef3f8;text-align:left;">
              <th style="padding:6px 10px;">Ticker</th>
              <th style="padding:6px 10px;">Was</th>
              <th style="padding:6px 10px;">Now</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        """

    if no_change_digest:
        intro = (
            "<p style='margin:0 0 12px;color:#334155;font-size:14px;line-height:1.5;'>"
            "No grade changes since your last report. "
            "Here’s a quick snapshot so you know the scheduler is running."
            "</p>"
        )
        title = "No grade changes"
        summary_html = ""
    else:
        summary = _grade_summary_line(quotes)
        health = _watchlist_health_line(quotes)
        intro = (
            "<p style='margin:0 0 12px;color:#334155;font-size:14px;line-height:1.5;'>"
            "Rule-based grades for your watchlist. "
            "Metrics and notes explain each score — not personalized advice."
            "</p>"
        )
        title = "Watchlist report"
        health_html = (
            f"<p style='margin:0 0 4px;font-size:12px;color:#64748b;'>"
            f"{_esc(health)}"
            f"</p>"
        ) if health else ""
        summary_html = (
            f"<p style='margin:0 0 4px;font-size:13px;color:#0f172a;font-weight:650;'>"
            f"{_esc(summary)}"
            f"</p>"
            f"{health_html}"
        )

    card_sections: list[str] = []
    for _key, section_title, group in _quotes_by_urgency(quotes):
        section_rows: list[str] = []
        for q in group:
            ticker = _plain_ticker(q.get("ticker", "?"))
            grade = quote_grade(q)
            verdict = q.get("verdict") or grade
            price = q.get("price")
            currency = q.get("currency") or "USD"
            price_s = (
                f"{price:.2f} {currency}" if isinstance(price, (int, float)) else "n/a"
            )
            notes_html = ""
            if not no_change_digest and (q.get("notes") or []):
                items_html = []
                for text, link in _note_lines_for_email(q)[:4]:
                    if link:
                        items_html.append(
                            f"<li>{_esc(text)} "
                            f"<a href='{_esc(link)}' style='color:#2563eb;'>Read</a></li>"
                        )
                    else:
                        items_html.append(f"<li>{_esc(text)}</li>")
                notes_html = (
                    "<ul style='margin:6px 0 0;padding-left:18px;color:#475569;font-size:12px;'>"
                    + "".join(items_html)
                    + "</ul>"
                )
            metrics = ""
            if not no_change_digest:
                metrics = f"""
                <p style="margin:8px 0 0;font-size:12px;color:#64748b;line-height:1.45;">
                  D/E {_esc(q.get('deRatio', 'N/A'))}
                  · PEG {_esc(q.get('pegRatio', 'N/A'))}
                  · RSI {_esc(q.get('rsi', 'N/A'))}
                  · Above 200-SMA {_esc(_human_bool(q.get('aboveSma200')))}
                  · {_esc(_human_asset_class(q.get('assetClass')))}
                </p>
                """
            section_rows.append(
                f"""
                <tr>
                  <td style="padding:14px 0;border-bottom:1px solid #e2e8f0;">
                    <div style="font-family:Consolas,monospace;font-size:15px;font-weight:700;color:#0f172a;">
                      {_esc(ticker)}
                      <span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:4px;background:{_grade_color(grade)};color:#fff;font-size:11px;font-weight:700;">
                        {_esc(verdict)}
                      </span>
                    </div>
                    <div style="margin-top:4px;font-size:13px;color:#334155;">Price: {_esc(price_s)}</div>
                    {metrics}
                    {notes_html}
                  </td>
                </tr>
                """
            )
        card_sections.append(
            f"""
            <h2 style="font-size:15px;margin:22px 0 6px;color:#0c1117;">{_esc(section_title)}</h2>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              {''.join(section_rows)}
            </table>
            """
        )

    quotes_block = (
        "".join(card_sections)
        if card_sections
        else '<p style="color:#64748b;">No tickers in this report.</p>'
    )

    unsub = ""
    if unsubscribe_url:
        unsub = f"""
        <p style="margin:24px 0 0;font-size:12px;color:#64748b;line-height:1.5;">
          <a href="{_esc(unsubscribe_url)}" style="color:#2563eb;">Unsubscribe</a>
          from scheduled emails. You can re-enable anytime in the Stock Agent Chrome extension.
        </p>
        """

    glossary = ""
    if not no_change_digest:
        glossary = f"""
        <h2 style="font-size:14px;margin:28px 0 8px;color:#0c1117;">Metric guide</h2>
        <p style="margin:0;font-size:12px;color:#64748b;line-height:1.55;">
          Grade 4–5 = STRONG BUY, 3 = HOLD, 0–2 = AVOID.
          D/E = debt vs equity. PEG = valuation vs growth. RSI = momentum (0–100).
          200-SMA = long-term trend. Grades use rules plus headline risk flags only.
        </p>
        """

    legal = f"""
    <p style="margin:16px 0 0;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;font-size:11px;color:#64748b;line-height:1.55;">
      {_esc(LEGAL_DISCLAIMER)}
    </p>
    <p style="margin:12px 0 0;font-size:11px;color:#94a3b8;">Privacy: tickers only — never your share counts or buy prices.</p>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#f1f5f9;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;padding:24px 22px;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
          <tr>
            <td>
              <p style="margin:0;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#64748b;">Stock Agent Chrome Extension</p>
              <h1 style="margin:6px 0 12px;font-size:22px;color:#0f172a;">{_esc(title)}</h1>
              {intro}
              {summary_html}
              {change_block}
              {quotes_block}
              {glossary}
              {unsub}
              {legal}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def format_no_change_text(
    email: str,
    quotes: list[dict[str, Any]],
    unsubscribe_url: str | None = None,
) -> str:
    lines = [
        "STOCK AGENT — NO GRADE CHANGES",
        "=" * 46,
        f"Recipient: {email}",
        "",
        "No grade changes since your last report. Current snapshot:",
        "",
    ]
    for q in quotes:
        lines.append(
            f"* {_plain_ticker(q.get('ticker', '?'))}: "
            f"{q.get('verdict') or quote_grade(q)}"
        )
    lines.extend(["", "DISCLAIMER", "-" * 46, LEGAL_DISCLAIMER])
    if unsubscribe_url:
        lines.extend(["", "Unsubscribe:", unsubscribe_url])
    return "\n".join(lines)


def send_plain_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email via the configured provider, or dry-run log when unset."""
    result = _send_email_via_provider(
        to_email=to_email,
        subject=subject,
        text_body=body,
    )
    return result.ok


def send_report_email(
    to_email: str,
    subject: str,
    body: str,
    unsubscribe_url: str | None = None,
    html_body: str | None = None,
) -> SendResult:
    """
    Send via the configured email provider (SES or Resend).
    Otherwise log the report (dry-run) so cron still exercises the pipeline.
    """
    return _send_email_via_provider(
        to_email=to_email,
        subject=subject,
        text_body=body,
        html_body=html_body,
        unsubscribe_url=unsubscribe_url,
    )


# ---------------------------------------------------------------------------
# Provider-agnostic email sending layer.
# Priority: AWS SES (if AWS_SES_REGION set) → Resend (if RESEND_API_KEY set) → dry-run.
# ---------------------------------------------------------------------------


def _get_email_provider() -> str:
    """Determine which email provider to use based on environment variables."""
    if os.getenv("AWS_SES_REGION", "").strip():
        return "ses"
    if os.getenv("RESEND_API_KEY", "").strip():
        return "resend"
    return "dry_run"


def _send_email_via_provider(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    unsubscribe_url: str | None = None,
) -> SendResult:
    """Route email to the configured provider."""
    provider = _get_email_provider()
    from_addr = os.getenv(
        "REPORT_FROM_EMAIL",
        "Stock Agent <noreply@stockagent.app>",
    ).strip()

    if provider == "ses":
        return _send_via_ses(
            to_email=to_email,
            from_addr=from_addr,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            unsubscribe_url=unsubscribe_url,
        )
    elif provider == "resend":
        return _send_via_resend(
            to_email=to_email,
            from_addr=from_addr,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            unsubscribe_url=unsubscribe_url,
        )
    else:
        logger.warning(
            "No email provider configured (set AWS_SES_REGION or RESEND_API_KEY) — dry-run to %s\nSubject: %s",
            to_email,
            subject,
        )
        return SendResult(ok=True, dry_run=True)


def _send_via_ses(
    *,
    to_email: str,
    from_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    unsubscribe_url: str | None = None,
) -> SendResult:
    """Send email via Amazon SES using boto3."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        logger.error("boto3 not installed — cannot send via SES. pip install boto3")
        return SendResult(ok=False, error="boto3 not installed")

    region = os.getenv("AWS_SES_REGION", "us-east-1").strip()
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()

    try:
        # Use explicit credentials if provided, else rely on instance role / env defaults.
        kwargs: dict[str, Any] = {"region_name": region, "service_name": "sesv2"}
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

        client = boto3.client(**kwargs)

        # Build the email message
        content: dict[str, Any] = {
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                },
            }
        }
        if html_body:
            content["Simple"]["Body"]["Html"] = {"Data": html_body, "Charset": "UTF-8"}

        # Build headers for List-Unsubscribe (Gmail/Outlook native unsubscribe)
        headers: list[dict[str, str]] = []
        if unsubscribe_url:
            headers.append({"Name": "List-Unsubscribe", "Value": f"<{unsubscribe_url}>"})
            headers.append({"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"})

        send_kwargs: dict[str, Any] = {
            "FromEmailAddress": from_addr,
            "Destination": {"ToAddresses": [to_email]},
            "Content": content,
        }
        if headers:
            # SESv2 SendEmail supports Headers in the Simple format directly.
            send_kwargs["Content"]["Simple"]["Headers"] = headers

        response = client.send_email(**send_kwargs)
        message_id = response.get("MessageId", "")
        logger.info("SES OK to=%s message_id=%s", to_email, message_id)
        return SendResult(ok=True, resend_id=message_id)

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        err = f"SES {error_code}: {error_msg}"
        logger.error("SES failed to=%s error=%s", to_email, err)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::{err}", flush=True)
        return SendResult(ok=False, error=err[:500])
    except Exception as exc:
        logger.exception("SES request failed for %s", to_email)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::SES exception: {exc}", flush=True)
        return SendResult(ok=False, error=str(exc)[:500])


def _send_via_resend(
    *,
    to_email: str,
    from_addr: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    unsubscribe_url: str | None = None,
) -> SendResult:
    """Send email via Resend HTTP API (legacy provider, still supported)."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return SendResult(ok=False, error="RESEND_API_KEY not set")

    payload: dict[str, Any] = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    if unsubscribe_url:
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
            err = f"Resend HTTP {response.status_code}: {response.text[:300]}"
            logger.error("Resend failed status=%s body=%s", response.status_code, response.text[:500])
            if os.getenv("GITHUB_ACTIONS"):
                print(f"::error::{err}", flush=True)
            return SendResult(ok=False, error=err)
        data: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass
        resend_id = str(data.get("id") or "").strip() or None
        logger.info("Resend OK to=%s id=%s", to_email, resend_id)
        return SendResult(ok=True, resend_id=resend_id)
    except Exception as exc:
        logger.exception("Resend request failed for %s", to_email)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::Resend request exception: {exc}", flush=True)
        return SendResult(ok=False, error=str(exc)[:500])
