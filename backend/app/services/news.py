"""Headline risk flags for grading — CI-friendly sources.

Primary: Yahoo Finance RSS (HTTP, works from GitHub Actions).
Fallback: yfinance `.news` when present.
Optional: GoogleNews when STOCK_AGENT_ALLOW_GOOGLENEWS=1 (often blocked in CI).

Results are cached on disk (TTL) so overlapping cron ticks and shared watchlists
do not re-fetch the same ticker repeatedly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

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
    "finance.yahoo.com",
    "www.finance.yahoo.com",
    "yahoo.com",
    "www.yahoo.com",
    # Indian financial media
    "economictimes.indiatimes.com",
    "www.economictimes.indiatimes.com",
    "livemint.com",
    "www.livemint.com",
    "business-standard.com",
    "www.business-standard.com",
    "moneycontrol.com",
    "www.moneycontrol.com",
    "financialexpress.com",
    "www.financialexpress.com",
    "ndtvprofit.com",
    "www.ndtvprofit.com",
    "thehinduBusinessline.com",
    "www.thehindubusinessline.com",
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
    "sec filing",
    "class action",
    "downgrade",
    "default",
    "insolvency",
    # Indian regulatory / enforcement keywords
    "sebi",
    "enforcement directorate",
    "ed probe",
    "ed raid",
    "cbi",
    "income tax raid",
    "it raid",
    "nse notice",
    "bse notice",
    "nse penalty",
    "bse penalty",
    "cci probe",
    "rbi penalty",
    "rbi action",
    "nclt",
    "rera",
    "pmla",
    "money laundering",
    "promoter pledge",
)

# Must match schemas.py TICKER_RE — covers long Indian tickers like
# ADANIENTERPRISES.NS (19 chars) and numeric BSE codes like 500325.BO.
_TICKER_SAFE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,18}$")
_USER_AGENT = (
    "StockAgent/1.0 (+https://github.com; cron headline fetch; contact: local)"
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _cache_dir() -> Path:
    raw = os.getenv("NEWS_CACHE_DIR", "").strip()
    if raw:
        path = Path(raw)
    else:
        path = Path(__file__).resolve().parents[2] / ".cache" / "news"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("NEWS_CACHE_TTL_SECONDS", "21600")))
    except ValueError:
        return 21600  # 6 hours


def _cache_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Z0-9._-]", "_", ticker.upper())
    return _cache_dir() / f"{safe}.json"


def _read_cache(ticker: str) -> list[dict[str, str]] | None:
    path = _cache_path(ticker)
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        ts = float(payload.get("fetched_at") or 0)
        if time.time() - ts > _cache_ttl_seconds():
            return None
        flags = payload.get("flags")
        if not isinstance(flags, list):
            return None
        out: list[dict[str, str]] = []
        for item in flags:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()[:180]
                url = str(item.get("url") or item.get("link") or "").strip()
                if title:
                    out.append({"title": title, "url": url})
            else:
                title = str(item).strip()[:180]
                if title:
                    out.append({"title": title, "url": ""})
            if len(out) >= 5:
                break
        return out
    except Exception:
        logger.debug("News cache read failed for %s", ticker, exc_info=True)
    return None


def _write_cache(ticker: str, flags: list[dict[str, str]]) -> None:
    path = _cache_path(ticker)
    try:
        path.write_text(
            json.dumps(
                {
                    "ticker": ticker,
                    "fetched_at": time.time(),
                    "flags": [
                        {
                            "title": str(f.get("title") or "")[:180],
                            "url": str(f.get("url") or "")[:500],
                        }
                        for f in flags[:5]
                        if str(f.get("title") or "").strip()
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("News cache write failed for %s", ticker, exc_info=True)


def _risk_flags_from_items(
    items: list[dict[str, str]], *, require_trusted_link: bool
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        desc = str(item.get("desc") or "").strip()
        link = str(item.get("link") or "").strip()
        if require_trusted_link and link:
            host = _domain(link)
            if host and host not in TRUSTED_DOMAINS and "news.google.com" not in host:
                continue
        blob = f"{title} {desc}".lower()
        if not any(k in blob for k in RISK_KEYWORDS):
            continue
        snippet = title or desc
        if snippet:
            flags.append({"title": snippet[:180], "url": link[:500] if link else ""})
        if len(flags) >= 5:
            break
    return flags


def _is_indian_ticker(ticker: str) -> bool:
    sym = (ticker or "").strip().upper()
    return sym.endswith(".NS") or sym.endswith(".BO")


def _fetch_yahoo_rss(ticker: str, max_items: int = 12) -> list[dict[str, str]]:
    """
    Yahoo Finance headline RSS — stable HTTPS endpoint suitable for CI.
    Uses region=IN for Indian tickers (.NS/.BO) so local-language results
    are returned; falls back to the no-region URL on failure.
    """
    symbol = quote(ticker, safe=".")
    if _is_indian_ticker(ticker):
        urls = (
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=IN&lang=en-IN",
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
            f"https://finance.yahoo.com/rss/headline?s={symbol}",
        )
    else:
        urls = (
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
            f"https://finance.yahoo.com/rss/headline?s={symbol}",
        )
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    last_error: Exception | None = None
    for url in urls:
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
            if response.status_code >= 400:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                continue
            root = ET.fromstring(response.text)
            items: list[dict[str, str]] = []
            for node in root.findall("./channel/item"):
                title = (node.findtext("title") or "").strip()
                link = (node.findtext("link") or "").strip()
                desc = (node.findtext("description") or "").strip()
                pub_date = (node.findtext("pubDate") or "").strip()

                # Freshness filter: drop headlines older than 7 days.
                if pub_date:
                    try:
                        from email.utils import parsedate_to_datetime

                        pub_dt = parsedate_to_datetime(pub_date)
                        age_days = (time.time() - pub_dt.timestamp()) / 86400
                        if age_days > 7:
                            continue
                    except Exception:
                        pass  # Can't parse date — keep the headline

                # Strip trivial HTML from description.
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()
                if title or desc:
                    items.append({"title": title, "link": link, "desc": desc})
                if len(items) >= max_items:
                    break
            if items:
                return items
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        logger.debug("Yahoo RSS failed for %s: %s", ticker, last_error)
    return []


def _fetch_yfinance_news(ticker: str, max_items: int = 10) -> list[dict[str, str]]:
    try:
        import yfinance as yf
    except Exception as exc:
        logger.debug("yfinance unavailable for news: %s", exc)
        return []

    try:
        raw = getattr(yf.Ticker(ticker), "news", None) or []
    except Exception as exc:
        logger.debug("yfinance news failed for %s: %s", ticker, exc)
        return []

    items: list[dict[str, str]] = []
    for entry in raw[:max_items]:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        title = str(entry.get("title") or content.get("title") or "").strip()

        link = str(entry.get("link") or entry.get("url") or "").strip()
        if not link:
            click = content.get("clickThroughUrl")
            if isinstance(click, dict):
                link = str(click.get("url") or "").strip()
        if not link:
            canonical = content.get("canonicalUrl")
            if isinstance(canonical, dict):
                link = str(canonical.get("url") or "").strip()
            elif isinstance(canonical, str):
                link = canonical.strip()

        desc = str(entry.get("publisher") or "").strip()
        if not desc:
            provider = content.get("provider")
            if isinstance(provider, dict):
                desc = str(provider.get("displayName") or "").strip()
        if not desc:
            desc = str(content.get("summary") or "").strip()

        if title:
            items.append({"title": title, "link": link, "desc": desc})
    return items


def _fetch_googlenews(ticker: str, max_items: int = 8) -> list[dict[str, str]]:
    try:
        from GoogleNews import GoogleNews
    except Exception as exc:
        logger.debug("GoogleNews package missing: %s", exc)
        return []

    try:
        googlenews = GoogleNews(lang="en", period="7d")
        googlenews.search(f"{ticker} stock")
        results = googlenews.result() or []
    except Exception as exc:
        logger.debug("GoogleNews search failed for %s: %s", ticker, exc)
        return []

    items: list[dict[str, str]] = []
    for item in results[:max_items]:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "title": str(item.get("title") or "").strip(),
                "link": str(item.get("link") or "").strip(),
                "desc": str(item.get("desc") or item.get("description") or "").strip(),
            }
        )
    return items


def _allow_googlenews() -> bool:
    return os.getenv("STOCK_AGENT_ALLOW_GOOGLENEWS", "").lower() in {
        "1",
        "true",
        "yes",
    }


def fetch_news_flags(ticker: str, max_items: int = 8) -> list[dict[str, str]]:
    """
    Return risk headline dicts {title, url} for grading + email links.
    Failures are non-fatal — grading continues without news.
    """
    symbol = str(ticker or "").strip().upper()
    if not symbol or not _TICKER_SAFE.match(symbol):
        return []

    cached = _read_cache(symbol)
    if cached is not None:
        return cached[:5]

    items: list[dict[str, str]] = _fetch_yahoo_rss(symbol, max_items=max(max_items, 12))
    source = "yahoo_rss"
    if not items:
        items = _fetch_yfinance_news(symbol, max_items=max(max_items, 10))
        source = "yfinance"
    if not items and _allow_googlenews():
        items = _fetch_googlenews(symbol, max_items=max_items)
        source = "googlenews"

    # Yahoo/yfinance titles are already market headlines; keyword filter is enough.
    # GoogleNews still prefers trusted outlet links when present.
    require_trusted = source == "googlenews"
    flags = _risk_flags_from_items(items, require_trusted_link=require_trusted)

    # Cache even empty results briefly so we do not hammer a dead endpoint.
    _write_cache(symbol, flags)
    if flags:
        logger.info("News flags ticker=%s source=%s count=%d", symbol, source, len(flags))
    else:
        logger.debug("No risk headlines ticker=%s source=%s", symbol, source or "none")
    return flags[:5]


def fetch_news_for_watchlist(
    tickers: list[str], max_workers: int = 6
) -> dict[str, list[dict[str, str]]]:
    """Fetch news flags in parallel so one slow ticker does not stall the rest."""
    unique: list[str] = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if t and t not in unique:
            unique.append(t)
    if not unique:
        return {}

    workers = max(1, min(max_workers, len(unique)))
    out: dict[str, list[dict[str, str]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_news_flags, t): t for t in unique}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                out[ticker] = fut.result()
            except Exception:
                logger.exception("News worker failed for %s", ticker)
                out[ticker] = []
    return out
