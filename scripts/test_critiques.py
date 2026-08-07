"""Validate fixes for all critique points about grading logic."""

import sys
sys.path.insert(0, "backend")

from app.services.grading import grade_metrics

print("=== Critique Validation ===\n")

# Critique 2: Bank with PE=12, growth=5% -> PEG=2.4 should NOT be penalized
print("Critique 2: Bank PEG=2.4 (PE 12 / growth 5%) — should not penalize")
r = grade_metrics({
    "ticker": "JPM", "assetClass": "banking",
    "deRatio": 5.0, "pegRatio": 2.4, "roeTrend": ["14%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
peg_notes = [n for n in r["notes"] if "valuation" in n.lower() or "rich" in n.lower()]
print(f"  PEG penalty: {'NONE (CORRECT)' if not peg_notes else peg_notes}")
print()

# Critique 3: Crypto proxy with D/E=4.0 (BTC price swing) — soft signal, no hard penalty
print("Critique 3: Crypto proxy D/E=4.0 (BTC-driven swing) — should not hard-penalize")
r = grade_metrics({
    "ticker": "MSTR", "assetClass": "crypto_proxy",
    "deRatio": 4.0, "pegRatio": None, "roeTrend": ["5%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
de_notes = [n for n in r["notes"] if "debt" in n.lower() or "leverage" in n.lower()]
print(f"  D/E penalty: {'NONE (CORRECT)' if not de_notes else de_notes}")
print()

# Critique 4: Cyclical energy stock at trough (ROE=3%) — should not be harsh AVOID
print("Critique 4: Energy cyclical at trough (ROE=3%) — should not harshly penalize")
r = grade_metrics({
    "ticker": "ONGC.NS", "assetClass": "cyclical",
    "deRatio": 1.0, "pegRatio": 0.8, "roeTrend": ["3%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
roe_notes = [n for n in r["notes"] if "roe" in n.lower() or "negative" in n.lower()]
print(f"  ROE penalty: {'NONE (CORRECT - trough is normal)' if not roe_notes else roe_notes}")
print()

# Critique 4b: Same energy stock in bad trough (ROE=-8%) — should note but not destroy
print("Critique 4b: Energy deep trough (ROE=-8%) — note it but don't destroy grade")
r = grade_metrics({
    "ticker": "XOM", "assetClass": "cyclical",
    "deRatio": 1.5, "pegRatio": 1.0, "roeTrend": ["-8%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
notes_shown = [n for n in r["notes"] if "roe" in n.lower() or "cycle" in n.lower()]
print(f"  Notes: {notes_shown}")
print()

# Critique 5: Biotech with negative earnings — PEG undefined, should not crash
print("Critique 5: Biotech negative earnings (PEG=None, ROE=-15%) — graceful handling")
r = grade_metrics({
    "ticker": "BIOCON.NS", "assetClass": "pharma",
    "deRatio": 0.3, "pegRatio": None, "roeTrend": ["-15%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
missing = [n for n in r["notes"] if "missing" in n.lower()]
roe_note = [n for n in r["notes"] if "biotech" in n.lower() or "negative roe" in n.lower()]
print(f"  Missing data: {missing}")
print(f"  ROE note: {roe_note}")
print()

# Critique 5b: Growth tech with PEG = infinity/huge (edge case)
print("Critique 5b: PEG=999 (extreme outlier) — should be treated as missing")
r = grade_metrics({
    "ticker": "CRAZY", "assetClass": "growth_tech",
    "deRatio": 0.5, "pegRatio": 999.0, "roeTrend": ["20%"],
    "aboveSma200": True, "rsi": 55, "smaWindow": 200,
})
print(f"  Grade: {r['grade']} ({r['score']}/5)")
peg_notes = [n for n in r["notes"] if "peg" in n.lower() or "growth multiple" in n.lower()]
print(f"  PEG handling: {'Treated as missing (CORRECT)' if not peg_notes else peg_notes}")
missing = [n for n in r["notes"] if "missing" in n.lower()]
print(f"  Missing data: {missing}")
