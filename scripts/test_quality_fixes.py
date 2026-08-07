"""Test the three email quality fixes: CDR, headlines, HOLD messages."""

import sys
sys.path.insert(0, "backend")

from app.services.market_data import classify_asset_from_info
from app.services.grading import (
    grade_metrics,
    _headline_looks_relevant,
    _contextual_hold_message,
)

print("=== Fix A: CDR Classification ===")
cdr_info = {"quoteType": "ETF", "shortName": "MicroStrategy CDR (CAD Hedged)", "sector": "", "industry": ""}
print("  MSTR.TO (CDR):", classify_asset_from_info(cdr_info, "MSTR.TO"))

smci_cdr = {"quoteType": "ETF", "shortName": "Super Micro Computer CDR (CAD Hedged)", "sector": "", "industry": ""}
print("  SMCI.TO (CDR):", classify_asset_from_info(smci_cdr, "SMCI.TO"))

real_etf = {"quoteType": "ETF", "shortName": "Vanguard S&P 500 Index ETF", "sector": "", "industry": "", "category": ""}
print("  VFV.TO (real ETF):", classify_asset_from_info(real_etf, "VFV.TO"))

print("\n=== Fix B: Headline Relevance Filter ===")
tests = [
    ("SPOT", "Why high-earning former athletes keep going broke", False),
    ("BCE", "Bell first carrier in Canada to detect and protect against spoofed calls with AI", True),
    ("AI", "Does C3.ai's (AI) Lawsuit Win Quietly Recast Its Risk Profile", True),
    ("NVDA", "NVDA stock rallies on strong earnings beat", True),
    ("TSLA", "10 Best Hiking Trails in Colorado", False),
    ("TCS", "TCS wins $2 billion deal from UK insurance firm", True),
]
for ticker, headline, expected in tests:
    result = _headline_looks_relevant(headline, ticker)
    status = "OK" if result == expected else "FAIL"
    print(f"  {status} {ticker}: '{headline[:50]}...' -> {result}")

print("\n=== Fix C: Contextual HOLD Messages ===")
scenarios = [
    (["Price is below the 200-day SMA (macro downtrend)."], "growth_tech"),
    (["Warning: Profit efficiency (ROE) is trending downward."], "growth_tech"),
    (["RSI is overbought (>70) - avoid chasing; risk of pullbacks."], "growth_tech"),
    (["Growth multiple looks stretched vs expected earnings growth."], "growth_tech"),
    ([], "growth_tech"),
    ([], "capital_intensive"),
    ([], "crypto_proxy"),
]
for notes, asset in scenarios:
    msg = _contextual_hold_message(notes, asset)
    context = notes[0][:40] if notes else f"(no notes, {asset})"
    print(f"  {context}")
    print(f"    -> {msg}")
