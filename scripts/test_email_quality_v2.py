"""Test email quality fixes: Spotify bug, C3.ai relevance, order."""

import sys
sys.path.insert(0, "backend")

from app.services.grading import grade_metrics
from app.services.email_report import _quotes_by_urgency

# Test 1: Spotify athlete bankruptcy headline should NOT penalize SPOT
print("=== Spotify Bug Fix ===")
r = grade_metrics({
    "ticker": "SPOT", "assetClass": "growth_tech",
    "deRatio": 0.23, "pegRatio": 1.67, "roeTrend": ["15%"],
    "aboveSma200": False, "rsi": 48, "smaWindow": 200,
}, [{"title": "Why high-earning former athletes keep going broke despite knowing all the terrible bankruptcy stories", "url": ""}])
news_notes = [n for n in r["notes"] if "News risk" in n or "athlete" in n.lower()]
print(f"  SPOT grade: {r['grade']} ({r['score']}/5)")
if news_notes:
    print(f"  BUG STILL EXISTS: {news_notes}")
else:
    print("  Irrelevant news filtered out: FIXED")
print()

# Test 2: C3.ai headline SHOULD still show (mentions AI ticker)
print("=== C3.ai (relevant risk headline) ===")
r2 = grade_metrics({
    "ticker": "AI", "assetClass": "growth_tech",
    "deRatio": 0.01, "pegRatio": None, "roeTrend": ["-10%"],
    "aboveSma200": False, "rsi": 76, "smaWindow": 200,
}, [{"title": "C3.ai (AI) Gets Relief From Lawsuit Exit As Valuation Questions Linger", "url": ""}])
news_notes2 = [n for n in r2["notes"] if "News risk" in n]
print(f"  AI grade: {r2['grade']} ({r2['score']}/5)")
if news_notes2:
    print(f"  Relevant risk correctly shown: {news_notes2[0]}")
else:
    print("  ERROR: relevant risk headline was filtered out!")
print()

# Test 3: Order is now Buy -> Hold -> Avoid
print("=== Email Presentation Order ===")
quotes = [
    {"grade": "AVOID", "ticker": "STLA"},
    {"grade": "STRONG_BUY", "ticker": "NVDA"},
    {"grade": "HOLD", "ticker": "PLTR"},
    {"grade": "AVOID", "ticker": "TSLA"},
    {"grade": "STRONG_BUY", "ticker": "VFV.TO"},
]
sections = _quotes_by_urgency(quotes)
order = [s[0] for s in sections]
print(f"  Section order: {order}")
expected = ["buy", "hold", "avoid"]
if order == expected:
    print("  CORRECT: Winners first, then neutral, then concerns")
else:
    print(f"  WRONG: expected {expected}")
