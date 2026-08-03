"""yfinance market + fundamental fetch (ticker symbols only)."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger("stock_agent.market")

# Keyword / sector maps for yfinance info → grading buckets.
_CRYPTO_KEYS = (
    "bitcoin",
    "cryptocurrency",
    "crypto mining",
    "crypto miner",
    "digital currency",
    "digital asset",
    "crypto asset",
    "blockchain mining",
)
_CAPITAL_SECTORS = frozenset({"utilities", "real estate"})
_CAPITAL_INDUSTRY_KEYS = (
    "telecom",
    "telephone",
    "wireless",
    "reit",
    "utilities",
    "electric utilities",
    "diversified telecommunication",
    "integrated telecommunication",
    "tower",
    "infrastructure reit",
    "specialty reit",
)
_GROWTH_INDUSTRY_KEYS = (
    "software",
    "semiconductor",
    "information technology services",
    "internet content",
    "internet retail",
    "computer hardware",
    "consumer electronics",
    "electronic components",
    "communication equipment",
    "cloud",
    "cybersecurity",
    "security software",
    "auto manufacturers",
    "scientific & technical instruments",
)


def classify_asset_from_info(info: dict[str, Any] | None, ticker: str = "") -> str:
    """
    Derive grading asset class from yfinance metadata (not a hardcoded ticker list).
    Returns: crypto_proxy | capital_intensive | growth_tech | standard
    """
    info = info or {}
    quote_type = str(info.get("quoteType") or "").strip().upper()
    sector = str(info.get("sector") or "").strip().lower()
    industry = str(info.get("industry") or "").strip().lower()
    summary = str(
        info.get("longBusinessSummary")
        or info.get("longName")
        or info.get("shortName")
        or ""
    ).lower()
    blob = f"{sector} {industry} {summary}"

    if quote_type == "CRYPTOCURRENCY":
        return "crypto_proxy"
    if any(k in blob for k in _CRYPTO_KEYS) or "crypto" in industry:
        return "crypto_proxy"

    if sector in _CAPITAL_SECTORS or any(k in industry for k in _CAPITAL_INDUSTRY_KEYS):
        return "capital_intensive"
    if sector == "communication services" and any(
        k in industry for k in ("telecom", "telephone", "wireless")
    ):
        return "capital_intensive"

    if sector == "technology" or any(k in industry for k in _GROWTH_INDUSTRY_KEYS):
        return "growth_tech"

    return "standard"


def classify_asset(ticker: str, info: dict[str, Any] | None = None) -> str:
    """Backward-compatible wrapper; prefer classify_asset_from_info when info exists."""
    if info:
        return classify_asset_from_info(info, ticker)
    return "standard"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "N/A":
            return None
        num = float(value)
        if pd.isna(num):
            return None
        return num
    except (TypeError, ValueError):
        return None


def resolve_currency(symbol: str, info: dict[str, Any] | None = None) -> str:
    """
    Prefer yfinance info.currency; fall back by exchange suffix.
    TSX/TSXV (.TO / .V) default CAD — never invent USD for Canadian listings.
    """
    info = info or {}
    raw = str(info.get("currency") or "").strip().upper()
    if raw and raw not in {"N/A", "NONE", "NULL"}:
        return raw

    sym = (symbol or "").strip().upper()
    if sym.endswith(".TO") or sym.endswith(".V") or sym.endswith(".CN"):
        return "CAD"
    if sym.endswith(".L"):
        return "GBP"
    if sym.endswith(".T") or sym.endswith(".TOKYO"):
        return "JPY"
    return "USD"


def _roe_trend(income_stmt: pd.DataFrame, balance_sheet: pd.DataFrame) -> list[str]:
    roes: list[str] = []
    if income_stmt is None or income_stmt.empty or balance_sheet is None or balance_sheet.empty:
        return ["N/A", "N/A", "N/A"]
    for i in range(min(3, len(income_stmt.columns))):
        try:
            net_income = income_stmt.loc["Net Income"].iloc[i]
            equity = balance_sheet.loc["Stockholders Equity"].iloc[i]
            if equity and equity != 0:
                roes.append(f"{round((float(net_income) / float(equity)) * 100, 1)}%")
            else:
                roes.append("N/A")
        except Exception:
            roes.append("N/A")
    while len(roes) < 3:
        roes.append("N/A")
    return roes


def _rsi(closes: pd.Series, window: int = 14) -> float | None:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window).mean()
    if loss.iloc[-1] == 0:
        return 100.0
    rs = gain / loss
    value = 100 - (100 / (1 + rs.iloc[-1]))
    return None if pd.isna(value) else round(float(value), 1)


def analyze_ticker(ticker: str) -> dict[str, Any]:
    """Fetch price + core metrics for one symbol. Never touches portfolio lots."""
    symbol = ticker.strip().upper()
    as_of = datetime.now(timezone.utc).isoformat()
    asset_class = "standard"

    try:
        stock = yf.Ticker(symbol)
        info = stock.info or {}
        asset_class = classify_asset_from_info(info, symbol)
        price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        currency = resolve_currency(symbol, info)
        peg = _safe_float(info.get("pegRatio"))

        try:
            balance_sheet = stock.balance_sheet
        except Exception:
            balance_sheet = pd.DataFrame()
        try:
            income_stmt = stock.financials
        except Exception:
            income_stmt = pd.DataFrame()

        de_ratio: float | None = None
        try:
            if balance_sheet is not None and not balance_sheet.empty:
                total_debt = (
                    balance_sheet.loc["Total Debt"].iloc[0]
                    if "Total Debt" in balance_sheet.index
                    else 0
                )
                total_equity = (
                    balance_sheet.loc["Stockholders Equity"].iloc[0]
                    if "Stockholders Equity" in balance_sheet.index
                    else 0
                )
                if total_equity:
                    de_ratio = round(float(total_debt) / float(total_equity), 2)
        except Exception:
            de_ratio = None

        roe_list = _roe_trend(income_stmt, balance_sheet)

        history = stock.history(period="1y")
        above_sma: bool | None = None
        sma_200: float | None = None
        rsi: float | None = None
        if history is not None and len(history) >= 200:
            last_close = float(history["Close"].iloc[-1])
            if price is None:
                price = round(last_close, 2)
            sma_200 = round(float(history["Close"].rolling(window=200).mean().iloc[-1]), 2)
            above_sma = last_close > sma_200
            rsi = _rsi(history["Close"])
        elif history is not None and not history.empty and price is None:
            price = round(float(history["Close"].iloc[-1]), 2)

        return {
            "ticker": symbol,
            "price": price,
            "currency": currency,
            "pegRatio": peg,
            "deRatio": de_ratio,
            "roeTrend": roe_list,
            "aboveSma200": above_sma,
            "sma200": sma_200,
            "rsi": rsi,
            "assetClass": asset_class,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "asOf": as_of,
            "error": None if price is not None else "price_unavailable",
        }
    except Exception as exc:
        logger.exception("yfinance failed for %s", symbol)
        return {
            "ticker": symbol,
            "price": None,
            "currency": resolve_currency(symbol),
            "pegRatio": None,
            "deRatio": None,
            "roeTrend": ["N/A", "N/A", "N/A"],
            "aboveSma200": None,
            "sma200": None,
            "rsi": None,
            "assetClass": asset_class,
            "sector": None,
            "industry": None,
            "asOf": as_of,
            "error": str(exc),
        }


def _quote_needs_retry(result: dict[str, Any]) -> bool:
    """True when the fetch failed hard or returned no usable price."""
    if result.get("error") and result.get("error") != "price_unavailable":
        return True
    return result.get("price") is None


def analyze_ticker_with_retry(
    ticker: str,
    *,
    attempts: int | None = None,
    backoff_seconds: float | None = None,
) -> dict[str, Any]:
    """
    Call analyze_ticker with short retries (helps transient yfinance empty/rate-limit).
    Env: QUOTE_FETCH_RETRIES (default 3), QUOTE_FETCH_BACKOFF_SECONDS (default 0.75).
    """
    if attempts is None:
        try:
            attempts = max(1, int(os.getenv("QUOTE_FETCH_RETRIES", "3")))
        except ValueError:
            attempts = 3
    if backoff_seconds is None:
        try:
            backoff_seconds = max(0.0, float(os.getenv("QUOTE_FETCH_BACKOFF_SECONDS", "0.75")))
        except ValueError:
            backoff_seconds = 0.75

    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        last = analyze_ticker(ticker)
        if not _quote_needs_retry(last):
            if attempt > 1:
                logger.info("Quote retry succeeded ticker=%s attempt=%d", ticker, attempt)
            return last
        if attempt < attempts:
            logger.warning(
                "Quote fetch incomplete ticker=%s attempt=%d/%d error=%s — retrying",
                ticker,
                attempt,
                attempts,
                last.get("error"),
            )
            time.sleep(backoff_seconds * attempt)
    assert last is not None
    return last


def analyze_watchlist(
    tickers: list[str],
    max_workers: int = 6,
    max_tickers: int | None = 25,
) -> list[dict[str, Any]]:
    """
    Parallel fetch for a watchlist.

    `max_tickers` defaults to 25 for the popup API. Pass None (or a higher
    limit) for cron so one tick can grade the union of many users' symbols.
    """
    unique = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if t and t not in unique:
            unique.append(t)
    if max_tickers is not None:
        unique = unique[:max_tickers]
    if not unique:
        return []

    logger.info("Fetching market data for %d unique ticker(s)", len(unique))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as pool:
        futures = {pool.submit(analyze_ticker_with_retry, t): t for t in unique}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                results[ticker] = fut.result()
            except Exception as exc:
                logger.exception("worker failed for %s", ticker)
                results[ticker] = {
                    "ticker": ticker,
                    "price": None,
                    "currency": resolve_currency(ticker),
                    "error": str(exc),
                    "asOf": datetime.now(timezone.utc).isoformat(),
                    "assetClass": classify_asset(ticker),
                }

    return [results[t] for t in unique if t in results]
