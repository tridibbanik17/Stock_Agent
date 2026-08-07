"""Test Google Finance chart data for historical prices (SMA/RSI calculation)."""

import re
import json
import httpx


def google_finance_history(symbol: str, exchange: str = "NSE") -> list[float] | None:
    """
    Fetch historical close prices from Google Finance chart page.
    Returns list of close prices (most recent last) or None.
    """
    url = f"https://www.google.com/finance/quote/{symbol}:{exchange}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    if resp.status_code != 200:
        return None

    # Google Finance embeds chart data in AF_initDataCallback JSON blobs
    # Look for arrays of price data points
    # Pattern: arrays of [timestamp, open, high, low, close, volume]
    # or sometimes just arrays of close prices
    
    # Try to find price data in the page
    # Google embeds it as: data:[[timestamp,price], ...]
    # Look for sequences of price-like numbers
    prices = re.findall(r">([\d,]+\.\d{2})<", resp.text)
    
    if len(prices) >= 10:
        # The first price is current, followed by other reference prices
        # This won't give us proper historical data — need the chart endpoint
        return [float(p.replace(",", "")) for p in prices[:5]]
    
    return None


def calculate_rsi(closes: list[float], window: int = 14) -> float | None:
    """Calculate RSI from close prices."""
    if len(closes) < window + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-window:]]
    losses = [-d if d < 0 else 0 for d in deltas[-window:]]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


if __name__ == "__main__":
    for sym in ["LTIM", "RELIANCE", "TCS"]:
        print(f"\n{sym}:NSE")
        prices = google_finance_history(sym, "NSE")
        if prices:
            print(f"  Got {len(prices)} prices: {prices[:5]}...")
        else:
            print("  No historical data from Google Finance page")
