"""Test Screener.in data extraction for Indian stock fundamentals."""

import re
import httpx


def fetch_screener_page(symbol: str) -> str | None:
    """Fetch the Screener.in company page HTML."""
    base = symbol.upper()
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
    for path in (f"/company/{base}/consolidated/", f"/company/{base}/"):
        url = f"https://www.screener.in{path}"
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    return None


def parse_screener_ratios(html: str) -> dict:
    """Extract key ratios from Screener.in HTML."""
    data = {}

    # The ratios are in <li> elements with class containing "flex"
    # Format: <span class="name">Label</span> ... <span class="number">Value</span>
    # Or: <li>...<span class="name">Stock P/E</span>...<span ...>28.6</span>...
    ratio_section = html

    # Extract all name-number pairs from the top ratios section
    # Pattern: name span followed by number span
    pairs = re.findall(
        r'<span\s+class="name"[^>]*>\s*([^<]+?)\s*</span>'
        r'.*?'
        r'<span\s+class="[^"]*number[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
        ratio_section,
        re.DOTALL,
    )

    for name, value in pairs:
        name = name.strip()
        value = value.strip().replace(",", "").replace("₹", "").strip()
        try:
            num = float(value)
        except (ValueError, TypeError):
            continue

        if "Stock P/E" in name:
            data["pe"] = num
        elif "PEG Ratio" in name:
            data["peg"] = num
        elif name == "ROE":
            data["roe"] = num
        elif name == "ROCE":
            data["roce"] = num
        elif "Debt to equity" in name or "Debt / Eq" in name:
            data["de_ratio"] = num
        elif "Current Price" in name:
            data["price"] = num
        elif "Market Cap" in name:
            data["market_cap_cr"] = num
        elif "Book Value" in name:
            data["book_value"] = num
        elif "Dividend Yield" in name:
            data["dividend_yield"] = num
        elif "Face Value" in name:
            data["face_value"] = num
        elif "Industry PE" in name:
            data["industry_pe"] = num

    # Also try a simpler approach for the key numbers
    if "pe" not in data:
        m = re.search(r"Stock P/E\s*</span>\s*<span[^>]*>\s*([\d.]+)", html)
        if m:
            data["pe"] = float(m.group(1))

    if "roe" not in data:
        m = re.search(r">ROE\s*</span>\s*<span[^>]*>\s*([\d.]+)", html)
        if m:
            data["roe"] = float(m.group(1))

    if "de_ratio" not in data:
        m = re.search(r"Debt to equity\s*</span>\s*<span[^>]*>\s*([\d.]+)", html)
        if m:
            data["de_ratio"] = float(m.group(1))

    return data


if __name__ == "__main__":
    test_tickers = [
        "RELIANCE.NS",
        "HDFCBANK.NS",
        "TCS.NS",
        "ITC.NS",
        "INFY.NS",
        "LTIM.NS",  # Uses LTIMINDTREE on Screener
    ]

    for ticker in test_tickers:
        print(f"\n{ticker}:")
        html = fetch_screener_page(ticker)
        if not html:
            print("  Page not found")
            continue
        ratios = parse_screener_ratios(html)
        if ratios:
            for k, v in sorted(ratios.items()):
                print(f"  {k}: {v}")
        else:
            print("  No ratios parsed")
