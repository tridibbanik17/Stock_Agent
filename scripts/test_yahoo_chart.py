"""Test Yahoo Finance v8 chart API directly for Indian stocks."""

import httpx
import json


def yahoo_chart_direct(symbol: str, period: str = "1y") -> dict | None:
    """
    Hit Yahoo Finance chart API directly — sometimes works when yfinance Ticker fails.
    This is the same endpoint yfinance uses internally.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "range": period,
        "interval": "1d",
        "includePrePost": "false",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    
    # Get a crumb first
    sess = httpx.Client(timeout=15, follow_redirects=True, headers=headers)
    try:
        # Warm session
        sess.get("https://finance.yahoo.com/")
        
        resp = sess.get(url, params=params)
        print(f"  v8 chart status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            chart = data.get("chart", {}).get("result", [])
            if chart:
                result = chart[0]
                meta = result.get("meta", {})
                timestamps = result.get("timestamp", [])
                quotes = result.get("indicators", {}).get("quote", [{}])[0]
                closes = quotes.get("close", [])
                print(f"  Symbol: {meta.get('symbol')}")
                print(f"  Currency: {meta.get('currency')}")
                print(f"  Exchange: {meta.get('exchangeName')}")
                print(f"  Data points: {len(timestamps)}")
                if closes:
                    valid_closes = [c for c in closes if c is not None]
                    print(f"  Valid closes: {len(valid_closes)}")
                    if valid_closes:
                        print(f"  Last close: {valid_closes[-1]}")
                        print(f"  First close: {valid_closes[0]}")
                return result
            else:
                error = data.get("chart", {}).get("error", {})
                print(f"  Error: {error}")
        elif resp.status_code == 404:
            print(f"  Not found (Yahoo doesn't have this symbol)")
        else:
            print(f"  Response: {resp.text[:200]}")
    except Exception as e:
        print(f"  Exception: {e}")
    finally:
        sess.close()
    return None


if __name__ == "__main__":
    for sym in ["LTIM.NS", "LTIM.BO", "LTIMINDTREE.NS", "RELIANCE.NS", "TCS.NS"]:
        print(f"\n{sym}:")
        yahoo_chart_direct(sym)
