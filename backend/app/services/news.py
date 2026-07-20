"""Trusted-domain news snippets for qualitative risk flags."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger("stock_agent.news")

TRUSTED_DOMAINS = {
    "reuters.com",
    "www.reuters.com",
    "cnbc.com",
    "www.cnbc.com",
    "bloomberg.com",
    "www.bloomberg.com",
    "ft.com",
    "www.ft.com",
    "wsj.com",
    "www.wsj.com",
    "bbc.com",
    "www.bbc.com",
    "forbes.com",
    "www.forbes.com",
    "marketwatch.com",
    "www.marketwatch.com",
}

RISK_KEYWORDS = (
    "probe",
    "investigation",
    "fraud",
    "auditor resign",
    "delist",
    "bankrupt",
    "sec charge",
    "accounting",
    "lawsuit",
    "subpoena",
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def fetch_news_flags(ticker: str, max_items: int = 8) -> list[str]:
    """
    Return short risk flags from trusted outlets only.
    Failures are non-fatal — grading continues without news.
    """
    flags: list[str] = []
    try:
        from GoogleNews import GoogleNews

        googlenews = GoogleNews(lang="en", period="7d")
        googlenews.search(f"{ticker} stock")
        results = googlenews.result() or []
    except Exception as exc:
        logger.warning("GoogleNews unavailable for %s: %s", ticker, exc)
        return []

    for item in results[:max_items]:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "")
        host = _domain(link)
        if host not in TRUSTED_DOMAINS:
            # Sometimes GoogleNews returns news.google.com wrappers - keep title check light.
            if "news.google.com" not in host and host not in TRUSTED_DOMAINS:
                continue
        title = str(item.get("title") or "").strip()
        desc = str(item.get("desc") or item.get("description") or "").strip()
        blob = f"{title} {desc}".lower()
        if any(k in blob for k in RISK_KEYWORDS):
            snippet = title or desc
            if snippet:
                flags.append(snippet[:180])
    return flags[:5]


def fetch_news_for_watchlist(tickers: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ticker in tickers:
        out[ticker] = fetch_news_flags(ticker)
    return out
