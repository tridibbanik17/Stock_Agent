"""Test that Screener.in fallback actually works for tickers where
yfinance has price but no fundamentals (simulated by clearing the data)."""

import sys
sys.path.insert(0, "backend")

from app.services.market_data import _screener_fundamentals


# Test Screener.in for tickers we know have pages
for sym in ["RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "ITC.NS", "INFY.NS", "BAJFINANCE.NS"]:
    result = _screener_fundamentals(sym)
    if result:
        print(f"{sym}: PE={result.get('pe')} ROE={result.get('roe')} D/E={result.get('de_ratio')} ROCE={result.get('roce')}")
    else:
        print(f"{sym}: NO DATA from Screener.in")
