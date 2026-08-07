"""Test full analyze_ticker + grading pipeline with Screener.in fallback."""

import sys
import time

sys.path.insert(0, "backend")

from app.services.market_data import analyze_ticker
from app.services.grading import attach_grades


for sym in ["RELIANCE.NS", "ITC.NS", "LTIM.NS"]:
    start = time.time()
    r = analyze_ticker(sym)
    elapsed = time.time() - start
    graded = attach_grades([r], {})[0]
    price = r.get("price")
    cur = r.get("currency")
    de = r.get("deRatio")
    roe = (r.get("roeTrend") or ["N/A"])[0]
    grade = graded.get("grade")
    verdict = graded.get("verdict")
    notes = graded.get("notes", [])[:3]
    print(f"{sym} ({elapsed:.1f}s):")
    print(f"  price={price} {cur}  D/E={de}  ROE={roe}")
    print(f"  GRADE: {grade} {verdict}")
    print(f"  notes: {notes}")
    print()
