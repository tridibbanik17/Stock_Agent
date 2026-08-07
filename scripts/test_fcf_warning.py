"""Test FCF warning note: high ROE + negative FCF = caution."""

import sys
sys.path.insert(0, "backend")

from app.services.grading import grade_metrics

print("=== FCF Warning Tests ===\n")

# Case 1: High ROE, negative FCF, growth_tech -> should warn
print("1. Tech: ROE=25%, FCF=-500M (should warn)")
r = grade_metrics({
    "ticker": "FAKE", "assetClass": "growth_tech",
    "deRatio": 0.5, "pegRatio": 1.2, "roeTrend": ["25%"],
    "freeCashflow": -500_000_000,
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
fcf_notes = [n for n in r["notes"] if "cash flow" in n.lower()]
print(f"   Grade: {r['grade']} ({r['score']}/5)")
print(f"   FCF note: {fcf_notes[0] if fcf_notes else 'NONE'}")
print()

# Case 2: High ROE, positive FCF -> no warning
print("2. Tech: ROE=25%, FCF=+500M (should NOT warn)")
r = grade_metrics({
    "ticker": "NVDA", "assetClass": "growth_tech",
    "deRatio": 0.1, "pegRatio": 0.8, "roeTrend": ["25%"],
    "freeCashflow": 500_000_000,
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
fcf_notes = [n for n in r["notes"] if "cash flow" in n.lower()]
print(f"   Grade: {r['grade']} ({r['score']}/5)")
print(f"   FCF note: {fcf_notes[0] if fcf_notes else 'NONE (correct)'}")
print()

# Case 3: Banking with negative FCF -> should NOT warn (FCF meaningless for banks)
print("3. Bank: ROE=14%, FCF=-1B (should NOT warn — banking)")
r = grade_metrics({
    "ticker": "JPM", "assetClass": "banking",
    "deRatio": 5.0, "pegRatio": 0.9, "roeTrend": ["14%"],
    "freeCashflow": -1_000_000_000,
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
fcf_notes = [n for n in r["notes"] if "cash flow" in n.lower()]
print(f"   Grade: {r['grade']} ({r['score']}/5)")
print(f"   FCF note: {fcf_notes[0] if fcf_notes else 'NONE (correct — excluded for banks)'}")
print()

# Case 4: Pharma with negative FCF -> should NOT warn (R&D phase)
print("4. Pharma: ROE=18%, FCF=-200M (should NOT warn — R&D phase)")
r = grade_metrics({
    "ticker": "BIOCON", "assetClass": "pharma",
    "deRatio": 0.5, "pegRatio": 1.0, "roeTrend": ["18%"],
    "freeCashflow": -200_000_000,
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
fcf_notes = [n for n in r["notes"] if "cash flow" in n.lower()]
print(f"   Grade: {r['grade']} ({r['score']}/5)")
print(f"   FCF note: {fcf_notes[0] if fcf_notes else 'NONE (correct — excluded for pharma)'}")
print()

# Case 5: Low ROE + negative FCF -> no warning (ROE not high enough to trigger)
print("5. Standard: ROE=5%, FCF=-100M (should NOT warn — ROE too low)")
r = grade_metrics({
    "ticker": "WEAK", "assetClass": "standard",
    "deRatio": 1.0, "pegRatio": 1.5, "roeTrend": ["5%"],
    "freeCashflow": -100_000_000,
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
fcf_notes = [n for n in r["notes"] if "cash flow" in n.lower()]
print(f"   Grade: {r['grade']} ({r['score']}/5)")
print(f"   FCF note: {fcf_notes[0] if fcf_notes else 'NONE (correct — ROE < 12%)'}")
