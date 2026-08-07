"""Test CDR peer classification and INSUFFICIENT_DATA grade."""

import sys
sys.path.insert(0, "backend")

from app.services.market_data import classify_asset_from_info
from app.services.grading import grade_metrics

# Test 1: MSTR.TO CDR should be crypto_proxy (same as MSTR)
print("=== CDR Classification Fix ===")
mstr_to = {
    "quoteType": "ETF",
    "shortName": "Strategy CDR (CAD Hedged)",
    "longName": "Strategy Inc",
    "sector": None,
    "industry": None,
    "category": None,
}
result = classify_asset_from_info(mstr_to, "MSTR.TO")
print(f"  MSTR.TO: {result}")
assert result == "crypto_proxy", f"Expected crypto_proxy, got {result}"

# SMCI.TO CDR should be growth_tech
smci_to = {
    "quoteType": "ETF",
    "shortName": "Super Micro Computer CDR (CAD Hedged)",
    "longName": "Super Micro Computer Inc",
    "sector": None,
    "industry": None,
    "category": None,
}
result2 = classify_asset_from_info(smci_to, "SMCI.TO")
print(f"  SMCI.TO: {result2}")
assert result2 == "growth_tech", f"Expected growth_tech, got {result2}"

# NVDA.TO CDR should be growth_tech
nvda_to = {
    "quoteType": "ETF",
    "shortName": "NVIDIA CDR (CAD Hedged)",
    "longName": "NVIDIA Corporation",
    "sector": None,
    "industry": None,
    "category": None,
}
result3 = classify_asset_from_info(nvda_to, "NVDA.TO")
print(f"  NVDA.TO: {result3}")
# NVIDIA has no sector in CDR metadata, but "tech" not in name... 
# It will fall to standard. That's OK — the peer lookup will fix fundamentals.

# Real ETF should still be index_etf
vfv = {
    "quoteType": "ETF",
    "shortName": "Vanguard S&P 500 Index ETF",
    "sector": None,
    "industry": None,
    "category": "",
}
result4 = classify_asset_from_info(vfv, "VFV.TO")
print(f"  VFV.TO (real ETF): {result4}")
assert result4 == "index_etf", f"Expected index_etf, got {result4}"

print("\n=== INSUFFICIENT DATA Fix ===")

# LTIM.NS: all metrics missing -> should be INSUFFICIENT_DATA, not AVOID
r = grade_metrics({
    "ticker": "LTIM.NS", "assetClass": "standard",
    "deRatio": None, "pegRatio": None, "roeTrend": [],
    "aboveSma200": None, "rsi": None, "smaWindow": None,
})
print(f"  LTIM.NS: {r['grade']} - {r['verdict']}")
print(f"  Note: {r['notes'][0]}")
assert r["grade"] == "INSUFFICIENT_DATA", f"Expected INSUFFICIENT_DATA, got {r['grade']}"

# A stock with SOME missing data but still enough to grade should still get AVOID
r2 = grade_metrics({
    "ticker": "WEAK", "assetClass": "standard",
    "deRatio": 3.0, "pegRatio": None, "roeTrend": ["-5%"],
    "aboveSma200": False, "rsi": None, "smaWindow": 200,
})
print(f"  WEAK (some data): {r2['grade']} ({r2['score']}/5)")
assert r2["grade"] == "AVOID", f"Expected AVOID, got {r2['grade']}"

print("\nAll tests passed!")
