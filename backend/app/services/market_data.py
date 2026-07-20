"""yfinance market + fundamental fetch (ticker symbols only)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger("stock_agent.market")

# Rough asset-class hints for context-aware grading (not exhaustive).
CRYPTO_PROXIES = {"MSTR", "MARA", "RIOT", "CLSK", "COIN", "HUT", "BITF"}
CAPITAL_INTENSIVE = {"BCE", "BCE.TO", "T", "VZ", "TLK", "AMT", "CCI", "SBAC"}
GROWTH_TECH = {
    "NVDA",
    "TSLA",
    "PLTR",
    "SHOP",
    "SHOP.TO",
    "SPOT",
    "AI",
    "SNOW",
    "CRWD",
    "DDOG",
    "NET",
}


def classify_asset(ticker: str) -> str:
    symbol = ticker.upper()
    base = symbol.split(".")[0]
    if symbol in CRYPTO_PROXIES or base in CRYPTO_PROXIES:
        return "crypto_proxy"
    if symbol in CAPITAL_INTENSIVE or base in CAPITAL_INTENSIVE:
        return "capital_intensive"
    if symbol in GROWTH_TECH or base in GROWTH_TECH:
        return "growth_tech"
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
    asset_class = classify_asset(symbol)

    try:
        stock = yf.Ticker(symbol)
        info = stock.info or {}
        price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        currency = str(info.get("currency") or "USD")
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
            "asOf": as_of,
            "error": None,
        }
    except Exception as exc:
        logger.exception("yfinance failed for %s", symbol)
        return {
            "ticker": symbol,
            "price": None,
            "currency": "USD",
            "pegRatio": None,
            "deRatio": None,
            "roeTrend": ["N/A", "N/A", "N/A"],
            "aboveSma200": None,
            "sma200": None,
            "rsi": None,
            "assetClass": asset_class,
            "asOf": as_of,
            "error": str(exc),
        }


def analyze_watchlist(tickers: list[str], max_workers: int = 6) -> list[dict[str, Any]]:
    """Parallel fetch for a watchlist (max 25)."""
    unique = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if t and t not in unique:
            unique.append(t)
    unique = unique[:25]
    if not unique:
        return []

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as pool:
        futures = {pool.submit(analyze_ticker, t): t for t in unique}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                results[ticker] = fut.result()
            except Exception as exc:
                logger.exception("worker failed for %s", ticker)
                results[ticker] = {
                    "ticker": ticker,
                    "price": None,
                    "currency": "USD",
                    "error": str(exc),
                    "asOf": datetime.now(timezone.utc).isoformat(),
                    "assetClass": classify_asset(ticker),
                }

    return [results[t] for t in unique if t in results]
