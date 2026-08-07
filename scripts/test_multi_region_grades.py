"""Integration test: grade real stocks across US, Canada, India, multiple sectors."""

import sys
import time

sys.path.insert(0, "backend")

from app.services.market_data import analyze_ticker
from app.services.grading import attach_grades


# Diverse multi-region watchlist
tickers = [
    # US
    "NVDA",       # Growth tech (semiconductor)
    "JPM",        # Banking
    "JNJ",        # Pharma
    # Canada
    "BCE.TO",     # Capital intensive (telecom)
    "TD.TO",      # Banking
    "SHOP.TO",    # Growth tech
    # India
    "HDFCBANK.NS",  # Banking
    "TCS.NS",       # Growth tech
    "ITC.NS",       # Consumer (standard)
    "RELIANCE.NS",  # Conglomerate/energy
]

print(f"Testing {len(tickers)} tickers across US/CA/IN...\n")
start = time.time()

results = []
for sym in tickers:
    r = analyze_ticker(sym)
    results.append(r)

graded = attach_grades(results, {})
elapsed = time.time() - start

print(f"{'Ticker':<15} {'Class':<20} {'D/E':<7} {'ROE':<8} {'Grade':<12} {'Score'}")
print("-" * 75)
for g in graded:
    ticker = g.get("ticker", "?")
    asset = g.get("assetClass", "?")
    de = g.get("deRatio")
    roe_list = g.get("roeTrend", [])
    roe = roe_list[0] if roe_list and roe_list[0] != "N/A" else "n/a"
    grade = g.get("grade", "?")
    score = g.get("score", "?")
    de_str = f"{de:.2f}" if isinstance(de, (int, float)) else "n/a"
    print(f"{ticker:<15} {asset:<20} {de_str:<7} {roe:<8} {grade:<12} {score}/5")

    # Show first note for context
    notes = g.get("notes", [])
    if notes:
        print(f"{'':15} -> {notes[0][:70]}")

print(f"\nTotal time: {elapsed:.1f}s")
