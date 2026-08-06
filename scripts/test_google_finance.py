"""Test Google Finance as a fallback for Indian stocks missing from Yahoo."""

import re
import httpx


def google_finance_price(symbol: str, exchange: str = "NSE") -> float | None:
    """Fetch last price from Google Finance page (no API key, no geo-block)."""
    url = f"https://www.google.com/finance/quote/{symbol}:{exchange}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} for {url}")
            return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None

    # Google Finance embeds price in a data-last-price attribute
    match = re.search(r'data-last-price="([0-9.,]+)"', resp.text)
    if match:
        return float(match.group(1).replace(",", ""))

    # Fallback: look in JSON-LD or other structured data
    match2 = re.search(r'"price"\s*:\s*"?([0-9.]+)"?', resp.text)
    if match2:
        return float(match2.group(1))

    print(f"  Could not parse price for {symbol}:{exchange}")
    return None


if __name__ == "__main__":
    test_cases = [
        ("LTIM", "NSE"),
        ("RELIANCE", "NSE"),
        ("TCS", "NSE"),
        ("HDFCBANK", "NSE"),
        ("LTIM", "BSE"),
        ("BAJFINANCE", "NSE"),
        ("ITC", "NSE"),
    ]
    print("Testing Google Finance fallback for Indian stocks:\n")
    for sym, exc in test_cases:
        price = google_finance_price(sym, exc)
        status = f"₹{price:.2f}" if price else "FAILED"
        print(f"  {sym}:{exc} -> {status}")
