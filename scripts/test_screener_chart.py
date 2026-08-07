"""Test Screener.in chart API for historical price data."""

import httpx
import json


def test_chart(name: str) -> None:
    print(f"\n{name}:")
    
    # Try the Screener chart/price endpoint
    urls = [
        f"https://www.screener.in/company/{name}/chart/",
        f"https://www.screener.in/api/company/{name}/chart/?q=Price&days=365",
        f"https://www.screener.in/api/company/{name}/chart/?q=Price-Volume&days=365&consolidated=true",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.screener.in/company/{name}/consolidated/",
    }
    
    for url in urls:
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        ct = resp.headers.get("content-type", "")
        print(f"  {url.split('screener.in')[1]}")
        print(f"    Status: {resp.status_code}, Type: {ct[:40]}")
        if resp.status_code == 200:
            if "json" in ct:
                data = resp.json()
                if isinstance(data, dict):
                    print(f"    Keys: {list(data.keys())[:5]}")
                    datasets = data.get("datasets", data.get("data", []))
                    if datasets and isinstance(datasets, list):
                        for ds in datasets[:2]:
                            if isinstance(ds, dict):
                                label = ds.get("label", "?")
                                values = ds.get("values", ds.get("data", []))
                                print(f"    Dataset '{label}': {len(values)} points")
                                if values:
                                    print(f"      First: {values[0]}, Last: {values[-1]}")
                elif isinstance(data, list):
                    print(f"    List of {len(data)} items")
                    if data:
                        print(f"    First: {data[0]}")
            else:
                # HTML response - check if it has price data embedded
                if "Price" in resp.text[:5000]:
                    print("    Contains 'Price' in first 5000 chars")
            break  # Stop at first successful response


if __name__ == "__main__":
    for name in ["RELIANCE", "TCS", "LTIMINDTREE", "HDFCBANK"]:
        test_chart(name)
