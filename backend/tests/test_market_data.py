"""Unit tests for market_data helpers (no network calls)."""

import pytest

from app.services.market_data import (
    classify_asset_from_info,
    resolve_currency,
)


# ---------------------------------------------------------------------------
# Asset class detection
# ---------------------------------------------------------------------------

class TestClassifyAsset:
    """classify_asset_from_info determines grading bucket from yfinance metadata."""

    def test_etf_by_quote_type(self):
        info = {"quoteType": "ETF", "sector": "", "industry": ""}
        assert classify_asset_from_info(info, "VFV.TO") == "index_etf"

    def test_cdr_not_classified_as_etf(self):
        """CDRs (quoteType=ETF but name contains CDR) → grade as equity."""
        info = {
            "quoteType": "ETF",
            "shortName": "NVIDIA CDR (CAD Hedged)",
            "sector": "",
            "industry": "",
        }
        result = classify_asset_from_info(info, "NVDA.TO")
        assert result != "index_etf"

    def test_banking_by_sector(self):
        info = {"quoteType": "EQUITY", "sector": "Financial Services", "industry": "Banks—Diversified"}
        assert classify_asset_from_info(info, "JPM") == "banking"

    def test_growth_tech_by_sector(self):
        info = {"quoteType": "EQUITY", "sector": "Technology", "industry": "Semiconductors"}
        assert classify_asset_from_info(info, "NVDA") == "growth_tech"

    def test_pharma_by_sector(self):
        info = {"quoteType": "EQUITY", "sector": "Healthcare", "industry": "Drug Manufacturers—General"}
        assert classify_asset_from_info(info, "JNJ") == "pharma"

    def test_crypto_proxy_by_keywords(self):
        info = {
            "quoteType": "EQUITY",
            "sector": "Technology",
            "industry": "Software—Application",
            "longBusinessSummary": "The company acquires and holds bitcoin as its primary treasury reserve asset.",
        }
        assert classify_asset_from_info(info, "MSTR") == "crypto_proxy"

    def test_capital_intensive_utilities(self):
        info = {"quoteType": "EQUITY", "sector": "Utilities", "industry": "Utilities—Regulated Electric"}
        assert classify_asset_from_info(info, "NEE") == "capital_intensive"

    def test_cyclical_energy(self):
        info = {"quoteType": "EQUITY", "sector": "Energy", "industry": "Oil & Gas Integrated"}
        assert classify_asset_from_info(info, "XOM") == "cyclical"

    def test_conglomerate(self):
        info = {"quoteType": "EQUITY", "sector": "Industrials", "industry": "Conglomerates"}
        assert classify_asset_from_info(info, "BRK-B") == "conglomerate"

    def test_standard_fallback(self):
        info = {"quoteType": "EQUITY", "sector": "Consumer Defensive", "industry": "Packaged Foods"}
        assert classify_asset_from_info(info, "WMT") == "standard"

    def test_empty_info_is_standard(self):
        assert classify_asset_from_info({}, "UNKNOWN") == "standard"
        assert classify_asset_from_info(None, "UNKNOWN") == "standard"


# ---------------------------------------------------------------------------
# Currency resolution
# ---------------------------------------------------------------------------

class TestResolveCurrency:
    """resolve_currency uses info.currency or suffix fallback."""

    def test_info_currency_preferred(self):
        assert resolve_currency("NVDA", {"currency": "USD"}) == "USD"

    def test_tsx_suffix_defaults_cad(self):
        assert resolve_currency("SHOP.TO") == "CAD"
        assert resolve_currency("TD.V") == "CAD"

    def test_nse_suffix_defaults_inr(self):
        assert resolve_currency("RELIANCE.NS") == "INR"
        assert resolve_currency("500325.BO") == "INR"

    def test_london_suffix_defaults_gbp(self):
        assert resolve_currency("SHEL.L") == "GBP"

    def test_no_info_us_default(self):
        assert resolve_currency("AAPL") == "USD"

    def test_info_overrides_suffix(self):
        # If yfinance says the currency is CAD, trust it even without a suffix
        assert resolve_currency("CUSTOM", {"currency": "CAD"}) == "CAD"
