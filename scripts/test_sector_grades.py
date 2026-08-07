"""Test sector-aware grading across different stock types and regions."""

import sys
sys.path.insert(0, "backend")

from app.services.market_data import classify_asset_from_info
from app.services.grading import grade_metrics


def test_classification():
    """Test that different sectors get the right asset class."""
    print("=== Asset Classification ===\n")
    cases = [
        # (info dict, ticker, expected class)
        ({"sector": "Financial Services", "industry": "Banks—Diversified"}, "HDFCBANK.NS", "banking"),
        ({"sector": "Financial Services", "industry": "Insurance—Life"}, "SBIN.NS", "banking"),
        ({"sector": "Healthcare", "industry": "Drug Manufacturers—General"}, "SUNPHARMA.NS", "pharma"),
        ({"sector": "Healthcare", "industry": "Biotechnology"}, "BIOCON.NS", "pharma"),
        ({"sector": "Technology", "industry": "Software—Application"}, "TCS.NS", "growth_tech"),
        ({"sector": "Utilities", "industry": "Electric Utilities"}, "NTPC.NS", "capital_intensive"),
        ({"sector": "Communication Services", "industry": "Telecom Services"}, "BCE.TO", "capital_intensive"),
        ({"sector": "Industrials", "industry": "Conglomerates"}, "RELIANCE.NS", "conglomerate"),
        ({"sector": "Consumer Cyclical", "industry": "Auto Manufacturers"}, "TMCV.NS", "growth_tech"),
        ({"sector": "Energy", "industry": "Oil & Gas Integrated"}, "ONGC.NS", "standard"),
        ({"sector": "Consumer Defensive", "industry": "Tobacco"}, "ITC.NS", "standard"),
        ({"quoteType": "ETF", "shortName": "Vanguard S&P 500"}, "VFV.TO", "index_etf"),
        ({"quoteType": "ETF", "shortName": "NVDA CDR (CAD Hedged)"}, "NVDA.TO", "standard"),
    ]
    for info, ticker, expected in cases:
        result = classify_asset_from_info(info, ticker)
        status = "OK" if result == expected else "FAIL"
        print(f"  {status} {ticker:15s} sector={info.get('sector','?'):25s} -> {result:18s} (expected {expected})")


def test_grading_thresholds():
    """Test that sector-aware thresholds produce sensible grades."""
    print("\n=== Sector-Aware Grading ===\n")
    cases = [
        # Banking: D/E=10 should be fine (banks are leveraged)
        ("HDFCBANK.NS", {"ticker": "HDFCBANK.NS", "assetClass": "banking", "deRatio": 10.0, "pegRatio": 1.0, "roeTrend": ["14%"], "aboveSma200": True, "rsi": 55, "smaWindow": 200}),
        # Tech with D/E=10 should be flagged
        ("TCS.NS", {"ticker": "TCS.NS", "assetClass": "growth_tech", "deRatio": 10.0, "pegRatio": 1.0, "roeTrend": ["25%"], "aboveSma200": True, "rsi": 55, "smaWindow": 200}),
        # Pharma with D/E=0.5 (healthy)
        ("SUNPHARMA.NS", {"ticker": "SUNPHARMA.NS", "assetClass": "pharma", "deRatio": 0.5, "pegRatio": 1.2, "roeTrend": ["18%"], "aboveSma200": True, "rsi": 55, "smaWindow": 200}),
        # Utility with D/E=2.5 (normal for capital-intensive)
        ("NTPC.NS", {"ticker": "NTPC.NS", "assetClass": "capital_intensive", "deRatio": 2.5, "pegRatio": 0.8, "roeTrend": ["10%"], "aboveSma200": True, "rsi": 55, "smaWindow": 200}),
        # Conglomerate with D/E=1.5 (reasonable)
        ("RELIANCE.NS", {"ticker": "RELIANCE.NS", "assetClass": "conglomerate", "deRatio": 1.5, "pegRatio": 1.2, "roeTrend": ["12%"], "aboveSma200": True, "rsi": 55, "smaWindow": 200}),
    ]
    for label, metrics in cases:
        result = grade_metrics(metrics)
        grade = result["grade"]
        score = result["score"]
        notes = result["notes"][:2]
        print(f"  {label:15s} D/E={metrics['deRatio']:<5} -> {grade} ({score}/5)")
        for n in notes:
            print(f"    - {n}")
        print()


if __name__ == "__main__":
    test_classification()
    test_grading_thresholds()
