"""yfinance market + fundamental fetch (ticker symbols only)."""

from __future__ import annotations

import logging
import os
import random
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
    if closes is None or len(closes) < window + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window).mean()
    if loss.iloc[-1] == 0:
        return 100.0
    rs = gain / loss
    value = 100 - (100 / (1 + rs.iloc[-1]))
    return None if pd.isna(value) else round(float(value), 1)


def _name_tokens(text: str) -> set[str]:
    stop = {
        "inc",
        "corp",
        "corporation",
        "ltd",
        "limited",
        "plc",
        "the",
        "and",
        "cdr",
        "cad",
        "hedged",
        "class",
        "common",
        "stock",
        "shares",
    }
    tokens: set[str] = set()
    for raw in str(text or "").lower().replace(",", " ").replace(".", " ").split():
        token = raw.strip()
        if len(token) >= 4 and token not in stop:
            tokens.add(token)
    return tokens


def _canadian_us_peer_symbol(symbol: str) -> str | None:
    """SMCI.TO → SMCI for CDRs / dual-listed Canadian wrappers."""
    sym = (symbol or "").strip().upper()
    for suffix in (".TO", ".V", ".CN"):
        if sym.endswith(suffix) and len(sym) > len(suffix) + 1:
            return sym[: -len(suffix)]
    return None


def _looks_like_same_issuer(local_info: dict[str, Any], peer_info: dict[str, Any]) -> bool:
    local_blob = " ".join(
        str(local_info.get(k) or "")
        for k in ("shortName", "longName", "displayName")
    )
    peer_blob = " ".join(
        str(peer_info.get(k) or "")
        for k in ("shortName", "longName", "displayName")
    )
    if "cdr" in local_blob.lower():
        return True
    local_tokens = _name_tokens(local_blob)
    peer_tokens = _name_tokens(peer_blob)
    return bool(local_tokens and peer_tokens and (local_tokens & peer_tokens))


def _derive_peg(info: dict[str, Any] | None) -> float | None:
    """Prefer Yahoo pegRatio; else approximate from forward/trailing PE ÷ growth %."""
    info = info or {}
    peg = _safe_float(info.get("pegRatio") or info.get("trailingPegRatio"))
    if peg is not None:
        return peg

    pe = _safe_float(info.get("forwardPE")) or _safe_float(info.get("trailingPE"))
    growth = _safe_float(info.get("earningsGrowth"))
    if pe is None or growth is None or growth <= 0:
        return None
    # yfinance growth is usually a decimal (0.15 = 15%); sometimes already percent-like.
    growth_pct = growth * 100.0 if abs(growth) <= 1.0 else growth
    if growth_pct <= 0:
        return None
    return round(float(pe) / growth_pct, 2)


def _peer_fundamentals(symbol: str, local_info: dict[str, Any]) -> dict[str, Any]:
    """
    For thin Canadian listings / CDRs, pull PEG + sector metadata from the US peer
    when issuer names match (never replaces local CAD price/history).
    """
    peer_symbol = _canadian_us_peer_symbol(symbol)
    if not peer_symbol:
        return {}
    try:
        peer_info = yf.Ticker(peer_symbol).info or {}
    except Exception:
        logger.exception("US peer lookup failed for %s → %s", symbol, peer_symbol)
        return {}
    if not _looks_like_same_issuer(local_info, peer_info):
        return {}

    out: dict[str, Any] = {"peerSymbol": peer_symbol}
    peg = _derive_peg(peer_info)
    if peg is not None:
        out["pegRatio"] = peg
    if peer_info.get("sector") and not local_info.get("sector"):
        out["sector"] = peer_info.get("sector")
    if peer_info.get("industry") and not local_info.get("industry"):
        out["industry"] = peer_info.get("industry")
    return out


def _fast_last_price(stock: yf.Ticker) -> float | None:
    """Cheap last-price probe; often works when full info is rate-limited."""
    try:
        fast = getattr(stock, "fast_info", None)
        if fast is None:
            return None
        if isinstance(fast, dict):
            return _safe_float(
                fast.get("last_price")
                or fast.get("lastPrice")
                or fast.get("regular_market_price")
            )
        return _safe_float(
            getattr(fast, "last_price", None)
            or getattr(fast, "lastPrice", None)
            or getattr(fast, "regular_market_price", None)
        )
    except Exception:
        return None


def _load_history(stock: yf.Ticker) -> pd.DataFrame:
    """Try longer then shorter windows; empty frame on total failure."""
    for period in ("2y", "1y", "6mo", "3mo", "1mo", "5d"):
        try:
            history = stock.history(period=period)
            if history is not None and not history.empty:
                return history
        except Exception:
            logger.debug("history(%s) failed for %s", period, getattr(stock, "ticker", "?"))
            continue
    return pd.DataFrame()


def _safe_info(stock: yf.Ticker) -> dict[str, Any]:
    try:
        info = stock.info or {}
        return info if isinstance(info, dict) else {}
    except Exception:
        logger.warning("stock.info failed for %s", getattr(stock, "ticker", "?"))
        return {}


def analyze_ticker(ticker: str) -> dict[str, Any]:
    """Fetch price + core metrics for one symbol. Never touches portfolio lots."""
    symbol = ticker.strip().upper()
    as_of = datetime.now(timezone.utc).isoformat()
    asset_class = "standard"

    try:
        stock = yf.Ticker(symbol)

        # Price first: history / fast_info are more reliable than stock.info under load.
        history = _load_history(stock)
        price = _fast_last_price(stock)
        if price is not None:
            price = round(price, 2)
        if history is not None and not history.empty:
            last_close = float(history["Close"].iloc[-1])
            if price is None:
                price = round(last_close, 2)

        info = _safe_info(stock)
        if price is None:
            price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
            if price is not None:
                price = round(price, 2)

        currency = resolve_currency(symbol, info)
        peg = _derive_peg(info)

        peer: dict[str, Any] = {}
        # Only hit the US peer when local fundamentals are thin (CDRs / dual lists).
        if peg is None or not info.get("sector") or not info.get("industry"):
            peer = _peer_fundamentals(symbol, info)
            if peg is None and peer.get("pegRatio") is not None:
                peg = peer["pegRatio"]

        sector = info.get("sector") or peer.get("sector")
        industry = info.get("industry") or peer.get("industry")
        enriched_info = {**info, "sector": sector, "industry": industry}
        asset_class = classify_asset_from_info(enriched_info, symbol)

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

        above_sma: bool | None = None
        sma_200: float | None = None
        sma_window: int | None = None
        rsi: float | None = None
        if history is not None and not history.empty:
            last_close = float(history["Close"].iloc[-1])
            if price is None:
                price = round(last_close, 2)
            rsi = _rsi(history["Close"])
            if len(history) >= 50:
                window = 200 if len(history) >= 200 else len(history)
                sma_window = window
                sma_200 = round(
                    float(history["Close"].rolling(window=window).mean().iloc[-1]),
                    2,
                )
                above_sma = last_close > sma_200

        return {
            "ticker": symbol,
            "price": price,
            "currency": currency,
            "pegRatio": peg,
            "deRatio": de_ratio,
            "roeTrend": roe_list,
            "aboveSma200": above_sma,
            "sma200": sma_200,
            "smaWindow": sma_window,
            "rsi": rsi,
            "assetClass": asset_class,
            "sector": sector,
            "industry": industry,
            "peerSymbol": peer.get("peerSymbol"),
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
            "smaWindow": None,
            "rsi": None,
            "assetClass": asset_class,
            "sector": None,
            "industry": None,
            "peerSymbol": None,
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
    Env: QUOTE_FETCH_RETRIES (default 4), QUOTE_FETCH_BACKOFF_SECONDS (default 1.0).
    """
    if attempts is None:
        try:
            attempts = max(1, int(os.getenv("QUOTE_FETCH_RETRIES", "4")))
        except ValueError:
            attempts = 4
    if backoff_seconds is None:
        try:
            backoff_seconds = max(0.0, float(os.getenv("QUOTE_FETCH_BACKOFF_SECONDS", "1.0")))
        except ValueError:
            backoff_seconds = 1.0

    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        last = analyze_ticker(ticker)
        if not _quote_needs_retry(last):
            if attempt > 1:
                logger.info("Quote retry succeeded ticker=%s attempt=%d", ticker, attempt)
            return last
        if attempt < attempts:
            delay = backoff_seconds * attempt + random.uniform(0.15, 0.6)
            logger.warning(
                "Quote fetch incomplete ticker=%s attempt=%d/%d error=%s — retrying in %.1fs",
                ticker,
                attempt,
                attempts,
                last.get("error"),
                delay,
            )
            time.sleep(delay)
    assert last is not None
    return last


def analyze_watchlist(
    tickers: list[str],
    max_workers: int = 4,
    max_tickers: int | None = 25,
) -> list[dict[str, Any]]:
    """
    Parallel fetch for a watchlist.

    `max_tickers` defaults to 25 for the popup API. Pass None (or a higher
    limit) for cron so one tick can grade the union of many users' symbols.
    Failures get a sequential second pass so one Yahoo blip does not leave
    mega-caps as permanent "Quote unavailable" in the popup.
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
    workers = max(1, min(max_workers, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
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

    # Second pass: cooler sequential retries for anything still missing a price.
    missing = [t for t in unique if _quote_needs_retry(results.get(t) or {})]
    if missing:
        logger.warning(
            "Second-pass quote fetch for %d ticker(s): %s",
            len(missing),
            ", ".join(missing),
        )
        for ticker in missing:
            time.sleep(0.75)
            try:
                retry_attempts = max(2, int(os.getenv("QUOTE_FETCH_RETRIES", "4")))
            except ValueError:
                retry_attempts = 4
            try:
                retry_backoff = max(1.0, float(os.getenv("QUOTE_FETCH_BACKOFF_SECONDS", "1.0")))
            except ValueError:
                retry_backoff = 1.0
            results[ticker] = analyze_ticker_with_retry(
                ticker,
                attempts=retry_attempts,
                backoff_seconds=retry_backoff,
            )

    return [results[t] for t in unique if t in results]
