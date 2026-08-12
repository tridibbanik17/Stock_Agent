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
import requests
import yfinance as yf

logger = logging.getLogger("stock_agent.market")

# ---------------------------------------------------------------------------
# Shared yfinance session with warm crumb — fixes Yahoo rate-limiting of
# Indian (.NS / .BO) tickers from non-Indian cloud servers (e.g. Render US).
# Yahoo requires a valid crumb obtained from a prior page visit; without it
# the v8/v10 chart endpoints return empty data for non-US exchanges.
# ---------------------------------------------------------------------------

_INDIAN_SUFFIXES = (".NS", ".BO")
_session: requests.Session | None = None
_session_warmed: bool = False


def _get_session() -> requests.Session:
    """Return a requests Session with browser-like headers for Yahoo Finance."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://finance.yahoo.com/",
            "Origin": "https://finance.yahoo.com",
        })
    return _session


def _warm_session() -> None:
    """
    Visit Yahoo Finance once to set cookies/crumb so subsequent Indian ticker
    requests are not rejected. Called lazily on first Indian ticker fetch.
    Safe to call multiple times — only warms once per process lifetime.
    """
    global _session_warmed
    if _session_warmed:
        return
    try:
        sess = _get_session()
        # Hitting the consent/home page plants the necessary cookies.
        sess.get("https://finance.yahoo.com/", timeout=10)
        _session_warmed = True
        logger.info("Yahoo Finance session warmed for Indian ticker fetches")
    except Exception:
        logger.warning("Yahoo Finance session warm-up failed; Indian tickers may return no price", exc_info=False)
        _session_warmed = True  # Don't retry on every call — best-effort only


def _is_indian_ticker(symbol: str) -> bool:
    sym = (symbol or "").strip().upper()
    return any(sym.endswith(s) for s in _INDIAN_SUFFIXES)

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
# Banking / financial institutions — leverage is structural, not a red flag.
_BANKING_INDUSTRY_KEYS = (
    "bank",
    "banks",
    "banking",
    "diversified banks",
    "regional banks",
    "commercial bank",
    "savings & loans",
    "credit services",
    "financial conglomerates",
    "investment banking",
    "asset management",
    "insurance",
    "life insurance",
    "property & casualty",
    "reinsurance",
    "capital markets",
    "mortgage",
    "financial data",
    "financial exchange",
)
# Pharma / biotech — high R&D spend, patents drive value, D/E norms differ.
_PHARMA_INDUSTRY_KEYS = (
    "drug manufacturers",
    "pharmaceuticals",
    "pharmaceutical",
    "biotechnology",
    "biotech",
    "medical devices",
    "diagnostics & research",
    "health information services",
    "medical instruments",
    "medical distribution",
)
# Conglomerates — diversified businesses, moderate leverage acceptable.
_CONGLOMERATE_INDUSTRY_KEYS = (
    "conglomerates",
    "conglomerate",
    "diversified industrials",
    "industrial conglomerates",
    "general industrials",
)
# Cyclical / commodity sectors — energy, materials, mining.
# ROE and margins swing with commodity cycles; trough-year metrics are misleading.
_CYCLICAL_INDUSTRY_KEYS = (
    "oil & gas",
    "oil & gas integrated",
    "oil & gas e&p",
    "oil & gas refining",
    "oil & gas midstream",
    "oil & gas equipment",
    "mining",
    "gold",
    "silver",
    "copper",
    "steel",
    "aluminum",
    "specialty chemicals",
    "agricultural inputs",
    "coal",
    "uranium",
    "industrial metals",
    "other precious metals",
    "other industrial metals",
)


def classify_asset_from_info(info: dict[str, Any] | None, ticker: str = "") -> str:
    """
    Derive grading asset class from yfinance metadata (not a hardcoded ticker list).
    Returns: index_etf | crypto_proxy | capital_intensive | growth_tech | standard
    """
    info = info or {}
    quote_type = str(info.get("quoteType") or "").strip().upper()
    sector = str(info.get("sector") or "").strip().lower()
    industry = str(info.get("industry") or "").strip().lower()
    category = str(info.get("category") or "").strip().lower()
    short_name = str(info.get("shortName") or "").strip().lower()
    long_name = str(info.get("longName") or "").strip().lower()
    summary = str(
        info.get("longBusinessSummary")
        or long_name
        or short_name
        or ""
    ).lower()
    blob = f"{sector} {industry} {category} {summary}"
    name_blob = f" {short_name} {long_name} {category} "
    sym = (ticker or "").strip().upper()

    # CDR detection: Canadian Depositary Receipts are ETF-wrapped single equities.
    # yfinance reports quoteType="ETF" for them but they should be graded like
    # their underlying stock, not as a passive index fund.
    is_cdr = "cdr" in name_blob or "(cad hedged)" in name_blob

    # CDRs with no sector/industry: classify by their underlying US equity.
    # e.g. MSTR.TO (Strategy CDR) → inherits crypto_proxy from MSTR.
    if is_cdr and not sector and not industry:
        # Try to classify based on the name content (faster than a yfinance call).
        if any(k in blob for k in _CRYPTO_KEYS) or "strategy" in short_name or "microstrategy" in blob:
            return "crypto_proxy"
        # Check name for tech-related terms
        cdr_blob = f"{short_name} {long_name}"
        cdr_tech_hints = ("computer", "semiconductor", "software", "nvidia", "tech", "digital", "intel", "amd", "meta", "apple", "microsoft", "google", "amazon", "tesla")
        if any(hint in cdr_blob for hint in cdr_tech_hints):
            return "growth_tech"
        if any(k in cdr_blob for k in ("bank", "financial", "insurance")):
            return "banking"
        if any(k in cdr_blob for k in ("pharma", "health", "medical", "biotech")):
            return "pharma"
        # Fallback: CDR without detectable sector → standard (still not index_etf).
        return "standard"

    # Passives / funds first — corporate D/E, PEG, ROE do not apply.
    # But skip this for CDRs — grade those by their underlying equity.
    if not is_cdr:
        if quote_type in {"ETF", "MUTUALFUND", "INDEX"}:
            return "index_etf"
        if (
            "etf" in category
            or "index fund" in category
            or " exchange traded" in name_blob
            or " etf" in name_blob
            or name_blob.endswith("etf ")
            or " index etf" in name_blob
            or "index fund" in name_blob
        ):
            return "index_etf"

    if quote_type == "CRYPTOCURRENCY":
        return "crypto_proxy"
    if any(k in blob for k in _CRYPTO_KEYS) or "crypto" in industry:
        return "crypto_proxy"

    # Banking / financials — D/E norms are structural (leverage IS the business).
    if sector == "financial services" or any(k in industry for k in _BANKING_INDUSTRY_KEYS):
        return "banking"

    # Pharma / biotech — R&D heavy, patent cliffs, different valuation.
    if sector == "healthcare" or any(k in industry for k in _PHARMA_INDUSTRY_KEYS):
        return "pharma"

    if sector in _CAPITAL_SECTORS or any(k in industry for k in _CAPITAL_INDUSTRY_KEYS):
        return "capital_intensive"
    if sector == "communication services" and any(
        k in industry for k in ("telecom", "telephone", "wireless")
    ):
        return "capital_intensive"

    # Conglomerates — Reliance, Tata, Adani, Berkshire, etc.
    if any(k in industry for k in _CONGLOMERATE_INDUSTRY_KEYS):
        return "conglomerate"

    # Cyclical / commodity — energy, mining, materials (ROE swings with cycles).
    if sector in {"energy", "basic materials"} or any(k in industry for k in _CYCLICAL_INDUSTRY_KEYS):
        return "cyclical"

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
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return "INR"
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
    if closes is None:
        return None
    series = closes.dropna()
    if len(series) < window + 1:
        return None
    delta = series.diff()
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


# Well-known Indian ADRs / NYSE listings that have a US peer with richer
# fundamental data (PEG, sector, industry) on Yahoo Finance.
# Format: "NSE/BSE_SYMBOL_WITHOUT_SUFFIX" → "US_TICKER"
_INDIAN_ADR_MAP: dict[str, str] = {
    "INFY": "INFY",        # Infosys → NYSE: INFY
    "WIT": "WIT",          # Wipro → NYSE: WIT  (base already is WIT)
    "HDB": "HDB",          # HDFC Bank → NYSE: HDB
    "IBN": "IBN",          # ICICI Bank → NYSE: IBN
    "SIFY": "SIFY",        # Sify Technologies
    "VEDL": "VEDL",        # Vedanta → NYSE: VEDL
    "RDY": "RDY",          # Dr. Reddy's → NYSE: RDY
    "HDFCBANK": "HDB",     # HDFC Bank NSE name → NYSE: HDB
    "WIPRO": "WIT",        # Wipro NSE → NYSE: WIT
    "INFOSYS": "INFY",     # Infosys NSE → NYSE: INFY
    "DRREDDY": "RDY",      # Dr. Reddy's NSE → NYSE: RDY
    "ICICIBANK": "IBN",    # ICICI Bank NSE → NYSE: IBN
}


def _indian_us_peer_symbol(symbol: str) -> str | None:
    """
    For Indian NSE/BSE tickers, return the US ADR ticker if one exists.
    INFOSYS.NS → INFY, TATAMOTORS.BO → TTM, etc.
    Only returns tickers with known liquid US ADRs.
    """
    sym = (symbol or "").strip().upper()
    base: str | None = None
    for suffix in (".NS", ".BO"):
        if sym.endswith(suffix):
            base = sym[: -len(suffix)]
            break
    if not base:
        return None
    peer = _INDIAN_ADR_MAP.get(base)
    # Skip entries that map to themselves or non-US tickers
    if not peer or "." in peer or peer == base:
        return None
    return peer


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
    Also handles Indian tickers with known US ADR equivalents (e.g. INFOSYS.NS → INFY).
    """
    # Try Canadian → US peer first, then Indian → US ADR
    peer_symbol = _canadian_us_peer_symbol(symbol) or _indian_us_peer_symbol(symbol)
    if not peer_symbol:
        return {}
    try:
        peer_info = yf.Ticker(peer_symbol).info or {}
    except Exception as exc:
        # Catch rate limits, 401s, network errors — peer lookup is best-effort.
        logger.warning(
            "US peer lookup failed for %s → %s: %s",
            symbol,
            peer_symbol,
            str(exc)[:100],
        )
        return {}
    if not _looks_like_same_issuer(local_info, peer_info):
        # Indian ADRs often have different short names — skip the name check for them.
        # For known Indian ADR map entries we trust the explicit mapping.
        if not _indian_us_peer_symbol(symbol):
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


def fetch_quick_price(ticker: str) -> dict[str, Any]:
    """
    Fast price-only fetch (~1-2s). No fundamentals, no grading.
    Returns {ticker, price, currency, error} for immediate UI feedback.
    """
    symbol = ticker.strip().upper()

    # Normalize US share-class tickers: BRK.B → BRK-B
    _EXCHANGE_SUFFIXES = (".TO", ".V", ".CN", ".NS", ".BO", ".L", ".T", ".AX")
    if "." in symbol and not any(symbol.endswith(s) for s in _EXCHANGE_SUFFIXES):
        parts = symbol.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 2:
            symbol = f"{parts[0]}-{parts[1]}"

    if _is_indian_ticker(symbol):
        _warm_session()

    try:
        stock = yf.Ticker(symbol, session=_get_session())
        price = _fast_last_price(stock)

        if price is None:
            # Try history as fallback
            try:
                hist = stock.history(period="5d")
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    closes = hist["Close"].dropna()
                    if not closes.empty:
                        price = round(float(closes.iloc[-1]), 2)
            except Exception:
                pass

        # Google Finance fallback for Indian tickers
        if price is None and _is_indian_ticker(symbol):
            price = _google_finance_price(symbol)

        if price is not None:
            price = round(price, 2)

        currency = resolve_currency(symbol)

        return {
            "ticker": symbol,
            "price": price,
            "currency": currency,
            "error": None if price is not None else "price_unavailable",
        }
    except Exception as exc:
        logger.warning("Quick price fetch failed for %s: %s", symbol, str(exc)[:100])
        return {
            "ticker": symbol,
            "price": None,
            "currency": resolve_currency(symbol),
            "error": "fetch_failed",
        }


def _load_history(stock: yf.Ticker) -> pd.DataFrame:
    """
    Try longer then shorter windows; empty frame on total failure.
    Drop NaN closes — Yahoo often appends an empty/partial session row after hours,
    which made last_close/SMA become NaN and incorrectly score aboveSma200=False.
    """
    for period in ("2y", "1y", "6mo", "3mo", "1mo", "5d"):
        try:
            history = stock.history(period=period)
            if history is None or history.empty or "Close" not in history.columns:
                continue
            cleaned = history.dropna(subset=["Close"])
            if not cleaned.empty:
                return cleaned
        except Exception:
            logger.debug("history(%s) failed for %s", period, getattr(stock, "ticker", "?"))
            continue
    return pd.DataFrame()


def _trend_from_history(
    history: pd.DataFrame,
    price: float | None,
) -> tuple[bool | None, float | None, int | None, float | None]:
    """
    Return (above_sma, sma_value, sma_window, rsi).
    Uses last valid close; prefers live price for the above-SMA check when present.
    """
    if history is None or history.empty or "Close" not in history.columns:
        return None, None, None, None

    closes = history["Close"].dropna()
    if closes.empty:
        return None, None, None, None

    last_close = float(closes.iloc[-1])
    compare_price = float(price) if isinstance(price, (int, float)) else last_close
    rsi = _rsi(closes)

    if len(closes) < 50:
        return None, None, None, rsi

    window = 200 if len(closes) >= 200 else len(closes)
    sma = float(closes.rolling(window=window).mean().iloc[-1])
    if pd.isna(sma):
        return None, None, None, rsi

    sma_rounded = round(sma, 2)
    return compare_price > sma, sma_rounded, window, rsi


def _safe_info(stock: yf.Ticker) -> dict[str, Any]:
    try:
        info = stock.info or {}
        return info if isinstance(info, dict) else {}
    except Exception:
        logger.warning("stock.info failed for %s", getattr(stock, "ticker", "?"))
        return {}


# ---------------------------------------------------------------------------
# Google Finance fallback — free, no API key, no geo-block.
# Used when yfinance returns no price for Indian tickers (Yahoo coverage gap).
# ---------------------------------------------------------------------------

_GOOGLE_FINANCE_EXCHANGE_MAP = {
    ".NS": "NSE",
    ".BO": "BSE",
}


def _google_finance_price(symbol: str) -> float | None:
    """
    Scrape last price from Google Finance quote page.
    Returns None on any failure — caller falls through to price_unavailable.
    """
    base = symbol.strip().upper()
    exchange: str | None = None
    for suffix, exc in _GOOGLE_FINANCE_EXCHANGE_MAP.items():
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            exchange = exc
            break
    if not exchange:
        return None

    url = f"https://www.google.com/finance/quote/{base}:{exchange}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        import re as _re

        import httpx as _httpx

        resp = _httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Google Finance HTTP %d for %s", resp.status_code, symbol)
            return None
        # Google Finance renders prices as >1,118.45< in the HTML
        prices = _re.findall(r">([\d,]+\.\d{2})<", resp.text)
        if prices:
            price = float(prices[0].replace(",", ""))
            logger.info("Google Finance fallback price for %s = %s", symbol, price)
            return price
        logger.debug("Google Finance no price parsed for %s", symbol)
        return None
    except Exception:
        logger.debug("Google Finance fallback failed for %s", symbol, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Screener.in fallback — fundamentals (PE, ROE, D/E) for Indian tickers
# missing from Yahoo Finance. Free, no API key, no geo-block.
# ---------------------------------------------------------------------------

def _screener_fundamentals(symbol: str) -> dict[str, Any]:
    """
    Fetch PE, ROE, D/E from Screener.in for Indian tickers.
    Returns dict with keys: pe, roe, de_ratio, roce, book_value, or empty dict.
    """
    import re as _re

    import httpx as _httpx

    base = symbol.strip().upper()
    for suffix in (".NS", ".BO"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Try consolidated first, then standalone
    html: str | None = None
    for path in (f"/company/{base}/consolidated/", f"/company/{base}/"):
        url = f"https://www.screener.in{path}"
        try:
            resp = _httpx.get(url, headers=headers, timeout=12, follow_redirects=True)
            if resp.status_code == 200:
                html = resp.text
                break
        except Exception:
            continue

    if not html:
        logger.debug("Screener.in page not found for %s (base=%s)", symbol, base)
        return {}

    data: dict[str, Any] = {}

    # Extract name-number pairs from Screener's HTML
    pairs = _re.findall(
        r'<span\s+class="name"[^>]*>\s*([^<]+?)\s*</span>'
        r'.*?'
        r'<span\s+class="[^"]*number[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
        html,
        _re.DOTALL,
    )

    for name, value in pairs:
        name = name.strip()
        value = value.strip().replace(",", "").replace("\u20b9", "").strip()
        try:
            num = float(value)
        except (ValueError, TypeError):
            continue

        if "Stock P/E" in name:
            data["pe"] = num
        elif name == "ROE":
            data["roe"] = num
        elif name == "ROCE":
            data["roce"] = num
        elif "Debt to equity" in name or "Debt / Eq" in name:
            data["de_ratio"] = num

    if data:
        logger.info(
            "Screener.in fundamentals for %s: PE=%s ROE=%s D/E=%s",
            symbol,
            data.get("pe"),
            data.get("roe"),
            data.get("de_ratio"),
        )
    else:
        logger.debug("Screener.in no ratios parsed for %s", symbol)
    return data


def analyze_ticker(ticker: str) -> dict[str, Any]:
    """Fetch price + core metrics for one symbol. Never touches portfolio lots."""
    symbol = ticker.strip().upper()

    # Normalize US share-class tickers: BRK.B → BRK-B, BF.B → BF-B.
    # Yahoo Finance uses hyphens for share classes, but users commonly type dots.
    # Only apply to tickers without an exchange suffix (e.g., .TO, .NS, .BO).
    _EXCHANGE_SUFFIXES = (".TO", ".V", ".CN", ".NS", ".BO", ".L", ".T", ".AX")
    if "." in symbol and not any(symbol.endswith(s) for s in _EXCHANGE_SUFFIXES):
        # e.g. BRK.B → BRK-B (single dot, last segment is 1-2 chars = share class)
        parts = symbol.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 2:
            symbol = f"{parts[0]}-{parts[1]}"

    as_of = datetime.now(timezone.utc).isoformat()
    asset_class = "standard"

    # Warm the Yahoo Finance session before fetching Indian tickers to avoid
    # empty responses from Yahoo's geo-restricted endpoints on cloud servers.
    if _is_indian_ticker(symbol):
        _warm_session()

    try:
        stock = yf.Ticker(symbol, session=_get_session())

        # Price first: history / fast_info are more reliable than stock.info under load.
        history = _load_history(stock)
        price = _fast_last_price(stock)
        if price is not None:
            price = round(price, 2)
        if history is not None and not history.empty and price is None:
            price = round(float(history["Close"].iloc[-1]), 2)

        info = _safe_info(stock)
        if price is None:
            price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
            if price is not None:
                price = round(price, 2)

        # Google Finance fallback for Indian tickers missing from Yahoo Finance.
        if price is None and _is_indian_ticker(symbol):
            gf_price = _google_finance_price(symbol)
            if gf_price is not None:
                price = round(gf_price, 2)

        currency = resolve_currency(symbol, info)
        peg = _derive_peg(info)

        peer: dict[str, Any] = {}
        # Only hit the US peer when local fundamentals are thin (CDRs / dual lists).
        if peg is None or not info.get("sector") or not info.get("industry"):
            peer = _peer_fundamentals(symbol, info)
            if peg is None and peer.get("pegRatio") is not None:
                peg = peer["pegRatio"]

        # Classify asset class from original info FIRST (CDR detection needs
        # empty sector/industry to trigger the early-return logic).
        # Only enrich with peer sector/industry if the CDR path did not fire.
        asset_class = classify_asset_from_info(info, symbol)
        sector = info.get("sector") or peer.get("sector")
        industry = info.get("industry") or peer.get("industry")
        if asset_class == "standard" and (peer.get("sector") or peer.get("industry")):
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

        # Screener.in fallback for Indian tickers with missing fundamentals.
        # Fills in D/E, ROE when yfinance returns nothing (Yahoo coverage gap).
        if _is_indian_ticker(symbol) and (de_ratio is None or not roe_list or roe_list == ["N/A", "N/A", "N/A"]):
            screener = _screener_fundamentals(symbol)
            if screener:
                if de_ratio is None and screener.get("de_ratio") is not None:
                    de_ratio = screener["de_ratio"]
                if (not roe_list or roe_list == ["N/A", "N/A", "N/A"]) and screener.get("roe") is not None:
                    roe_list = [f"{screener['roe']}%", "N/A", "N/A"]
                if peg is None and screener.get("pe") is not None:
                    # Approximate PEG from PE if we have it (rough — assumes ~15% growth)
                    # Better than nothing for the grading engine.
                    pass  # Don't invent PEG from PE alone — leave it None

        above_sma, sma_200, sma_window, rsi = _trend_from_history(history, price)

        # Free cash flow — used for warning notes only (not scored).
        free_cashflow = _safe_float(info.get("freeCashflow"))

        return {
            "ticker": symbol,
            "price": price,
            "currency": currency,
            "pegRatio": peg,
            "deRatio": de_ratio,
            "roeTrend": roe_list,
            "freeCashflow": free_cashflow,
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
        # Last-resort Google Finance fallback for Indian tickers
        fallback_price = None
        if _is_indian_ticker(symbol):
            fallback_price = _google_finance_price(symbol)
            if fallback_price is not None:
                fallback_price = round(fallback_price, 2)
        return {
            "ticker": symbol,
            "price": fallback_price,
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
            "error": None if fallback_price is not None else str(exc),
        }


def _quote_needs_retry(result: dict[str, Any]) -> bool:
    """
    True when the fetch failed hard, returned no price, or is missing trend
    inputs that make grades jump +1 on a later refresh (common after cold start).
    """
    if result.get("error") and result.get("error") != "price_unavailable":
        return True
    if result.get("price") is None:
        return True
    # If we got a price (possibly via Google Finance fallback) but no SMA,
    # only retry for non-Indian tickers. Indian tickers that needed the
    # Google Finance fallback won't have history data no matter how many
    # retries — yfinance simply doesn't have the ticker.
    if result.get("aboveSma200") is None:
        ticker = str(result.get("ticker") or "").upper()
        if ticker.endswith(".NS") or ticker.endswith(".BO"):
            # Got price via fallback, no history available — don't retry.
            return False
        return True
    return False

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

    # Second pass: cooler sequential retries for incomplete price / trend data.
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
