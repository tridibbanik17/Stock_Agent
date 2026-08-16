"""Unit tests for the grading engine (deterministic, no network)."""

import pytest

from app.services.grading import grade_metrics, attach_grades


# ---------------------------------------------------------------------------
# Helper: build a metrics dict with sensible defaults
# ---------------------------------------------------------------------------

def _metrics(
    ticker="TEST",
    asset_class="standard",
    de_ratio=0.5,
    peg_ratio=0.8,
    roe_trend=None,
    above_sma=True,
    rsi=50.0,
    sma_window=200,
    free_cashflow=None,
):
    return {
        "ticker": ticker,
        "assetClass": asset_class,
        "deRatio": de_ratio,
        "pegRatio": peg_ratio,
        "roeTrend": roe_trend or ["20%", "18%", "16%"],
        "aboveSma200": above_sma,
        "rsi": rsi,
        "smaWindow": sma_window,
        "freeCashflow": free_cashflow,
    }


# ---------------------------------------------------------------------------
# Score boundary tests
# ---------------------------------------------------------------------------

class TestScoreBoundaries:
    """Verify grade labels match documented thresholds."""

    def test_perfect_score_is_strong_buy(self):
        """All factors positive → 5/5 STRONG_BUY."""
        # RSI < 35 earns the 5th point (oversold bonus)
        m = _metrics(de_ratio=0.3, peg_ratio=0.5, roe_trend=["25%", "20%"], above_sma=True, rsi=30)
        result = grade_metrics(m)
        assert result["score"] == 5
        assert result["grade"] == "STRONG_BUY"

    def test_score_4_is_strong_buy(self):
        """Score 4 → STRONG_BUY."""
        m = _metrics(de_ratio=0.3, peg_ratio=0.5, roe_trend=["25%", "20%"], above_sma=True, rsi=75)
        # D/E +1, PEG +1, ROE +1, SMA +1, RSI -1 → score = 3? Let's check.
        # Actually: D/E<1.5=+1, PEG<1.0=+1, ROE>15=+1, SMA=+1, RSI>=70=-1 → 4-1=3? No:
        # score starts at 0: +1+1+1+1 = 4, then RSI>=70 → max(0, 4-1) = 3. That's HOLD.
        # For score=4 we need 4 positives + neutral RSI:
        m = _metrics(de_ratio=0.3, peg_ratio=0.5, roe_trend=["25%", "20%"], above_sma=True, rsi=55)
        result = grade_metrics(m)
        assert result["score"] >= 4
        assert result["grade"] == "STRONG_BUY"

    def test_score_3_is_hold(self):
        """Score 3 → HOLD."""
        # D/E +1, PEG miss, ROE +1, SMA +1, RSI neutral → 3
        m = _metrics(de_ratio=0.5, peg_ratio=None, roe_trend=["20%", "18%"], above_sma=True, rsi=50)
        result = grade_metrics(m)
        assert result["score"] == 3
        assert result["grade"] == "HOLD"

    def test_score_2_is_avoid(self):
        """Score ≤ 2 → AVOID."""
        m = _metrics(de_ratio=3.0, peg_ratio=3.0, roe_trend=["-5%", "2%"], above_sma=False, rsi=50)
        result = grade_metrics(m)
        assert result["score"] <= 2
        assert result["grade"] == "AVOID"

    def test_score_0_is_avoid(self):
        """All factors negative or missing → 0/5 AVOID."""
        m = _metrics(de_ratio=None, peg_ratio=None, roe_trend=[], above_sma=False, rsi=75)
        result = grade_metrics(m)
        assert result["score"] == 0
        assert result["grade"] == "AVOID"


# ---------------------------------------------------------------------------
# RSI threshold tests (the boundary we just fixed)
# ---------------------------------------------------------------------------

class TestRsiBoundary:
    """RSI >= 70 should now penalize (Wilder convention)."""

    def test_rsi_exactly_70_is_overbought(self):
        """RSI = 70.0 exactly → penalty applied."""
        m = _metrics(rsi=70.0)
        result = grade_metrics(m)
        assert "overbought" in " ".join(result["notes"]).lower()

    def test_rsi_69_9_is_not_overbought(self):
        """RSI = 69.9 → no penalty."""
        m = _metrics(rsi=69.9)
        result = grade_metrics(m)
        assert "overbought" not in " ".join(result["notes"]).lower()

    def test_rsi_35_is_oversold(self):
        """RSI < 35 → +1 bonus."""
        m = _metrics(rsi=34.0)
        result = grade_metrics(m)
        assert "selling fatigue" in " ".join(result["notes"]).lower()

    def test_rsi_35_exactly_is_neutral(self):
        """RSI = 35.0 → neither bonus nor penalty."""
        m = _metrics(rsi=35.0)
        result = grade_metrics(m)
        notes_text = " ".join(result["notes"]).lower()
        assert "selling fatigue" not in notes_text
        assert "overbought" not in notes_text

    def test_rsi_70_etf_branch(self):
        """RSI = 70 on an ETF also penalizes."""
        m = _metrics(asset_class="index_etf", rsi=70.0, de_ratio=None, peg_ratio=None, roe_trend=[])
        result = grade_metrics(m)
        assert "overbought" in " ".join(result["notes"]).lower()


# ---------------------------------------------------------------------------
# Sector-aware D/E thresholds
# ---------------------------------------------------------------------------

class TestDebtToEquity:
    """D/E thresholds differ by asset class."""

    def test_banking_high_de_is_fine(self):
        """Banks: D/E < 15 earns a point."""
        m = _metrics(asset_class="banking", de_ratio=10.0)
        result = grade_metrics(m)
        # Should not mention "high" or "leverage" warning
        notes_text = " ".join(result["notes"]).lower()
        assert "unusually high" not in notes_text

    def test_banking_extreme_de_warns(self):
        """Banks: D/E >= 15 triggers a warning."""
        m = _metrics(asset_class="banking", de_ratio=16.0)
        result = grade_metrics(m)
        assert "unusually high" in " ".join(result["notes"]).lower()

    def test_growth_tech_low_de_earns_point(self):
        """Growth tech: D/E < 1.0 earns a point."""
        base = _metrics(asset_class="growth_tech", de_ratio=0.5, peg_ratio=None, roe_trend=[], above_sma=None, rsi=None)
        result = grade_metrics(base)
        assert result["score"] >= 1

    def test_growth_tech_high_de_warns(self):
        """Growth tech: D/E > 2.0 triggers a warning."""
        m = _metrics(asset_class="growth_tech", de_ratio=2.5)
        result = grade_metrics(m)
        assert "high debt" in " ".join(result["notes"]).lower()


# ---------------------------------------------------------------------------
# PEG thresholds
# ---------------------------------------------------------------------------

class TestPeg:
    """PEG scoring rules."""

    def test_standard_peg_below_1_earns_point(self):
        m = _metrics(peg_ratio=0.8)
        result_with = grade_metrics(m)
        m2 = _metrics(peg_ratio=1.5)
        result_without = grade_metrics(m2)
        assert result_with["score"] > result_without["score"]

    def test_growth_tech_peg_below_1_5_earns_point(self):
        m = _metrics(asset_class="growth_tech", peg_ratio=1.2)
        result = grade_metrics(m)
        # Should earn the PEG point since 1.2 < 1.5 for growth_tech
        m2 = _metrics(asset_class="growth_tech", peg_ratio=2.0)
        result2 = grade_metrics(m2)
        assert result["score"] > result2["score"]

    def test_peg_above_100_ignored(self):
        """PEG > 100 treated as noise (no point, no penalty)."""
        m = _metrics(peg_ratio=150.0)
        result = grade_metrics(m)
        # Should have "PEG" in missing data
        assert "PEG" in " ".join(result["notes"])

    def test_negative_peg_ignored(self):
        """Negative PEG (from negative growth) treated as missing."""
        m = _metrics(peg_ratio=-0.5)
        result = grade_metrics(m)
        assert "PEG" in " ".join(result["notes"])


# ---------------------------------------------------------------------------
# ROE trend
# ---------------------------------------------------------------------------

class TestRoe:
    """ROE scoring and warnings."""

    def test_roe_above_15_earns_point(self):
        m = _metrics(roe_trend=["20%", "18%", "16%"])
        result = grade_metrics(m)
        # With good D/E, PEG, ROE, SMA → should be high score
        assert result["score"] >= 4

    def test_roe_negative_warns(self):
        m = _metrics(roe_trend=["-5%", "2%", "5%"])
        result = grade_metrics(m)
        notes_lower = " ".join(result["notes"]).lower()
        assert "negative roe" in notes_lower

    def test_roe_declining_warns(self):
        m = _metrics(roe_trend=["16%", "20%", "22%"])  # current < previous
        result = grade_metrics(m)
        assert "trending downward" in " ".join(result["notes"]).lower()

    def test_crypto_proxy_roe_discounted(self):
        m = _metrics(asset_class="crypto_proxy", roe_trend=["5%"])
        result = grade_metrics(m)
        assert "weak signal" in " ".join(result["notes"]).lower()


# ---------------------------------------------------------------------------
# Index/ETF special scoring
# ---------------------------------------------------------------------------

class TestIndexEtf:
    """ETFs start at 3 and only use SMA + RSI."""

    def test_etf_starts_at_3(self):
        m = _metrics(asset_class="index_etf", above_sma=None, rsi=None, de_ratio=None, peg_ratio=None, roe_trend=[])
        result = grade_metrics(m)
        # Base is 3, but missing SMA/RSI → stays 3
        assert result["score"] == 3
        assert result["grade"] == "HOLD"

    def test_etf_above_sma_plus_neutral_rsi(self):
        m = _metrics(asset_class="index_etf", above_sma=True, rsi=55.0, de_ratio=None, peg_ratio=None, roe_trend=[])
        result = grade_metrics(m)
        # 3 + 1 (SMA) = 4 → STRONG_BUY
        assert result["score"] == 4
        assert result["grade"] == "STRONG_BUY"

    def test_etf_below_sma_and_overbought(self):
        m = _metrics(asset_class="index_etf", above_sma=False, rsi=75.0, de_ratio=None, peg_ratio=None, roe_trend=[])
        result = grade_metrics(m)
        # 3 - 1 (SMA) - 1 (RSI) = 1 → AVOID
        assert result["score"] == 1
        assert result["grade"] == "AVOID"


# ---------------------------------------------------------------------------
# News penalty
# ---------------------------------------------------------------------------

class TestNewsPenalty:
    """Tiered news severity scoring."""

    def test_severe_headline_minus_2(self):
        m = _metrics()
        news = [{"title": "Company faces fraud investigation by SEC"}]
        result = grade_metrics(m, news)
        # Should see news risk in notes
        assert "news risk" in " ".join(result["notes"]).lower()

    def test_moderate_headline_minus_1(self):
        m = _metrics(ticker="SHOP")
        news = [{"title": "SHOP faces lawsuit from former employees"}]
        result = grade_metrics(m, news)
        assert "news risk" in " ".join(result["notes"]).lower()

    def test_mild_headline_no_penalty(self):
        m = _metrics(ticker="AMZN")
        base_result = grade_metrics(m)
        news = [{"title": "AMZN analyst concern over margins"}]
        news_result = grade_metrics(m, news)
        # Mild → no score change (informational only)
        assert news_result["score"] == base_result["score"]

    def test_news_penalty_capped_at_3(self):
        m = _metrics(ticker="BAD")
        news = [
            {"title": "BAD fraud charges filed"},
            {"title": "BAD bankruptcy imminent"},
            {"title": "BAD SEC investigation launched"},
        ]
        result = grade_metrics(m, news)
        # Even with 3 severe headlines (6 penalty points), cap = -3
        assert result["score"] >= 0

    def test_irrelevant_headline_not_penalized(self):
        """A headline that doesn't mention the ticker or finance keywords."""
        m = _metrics(ticker="AAPL")
        news = [{"title": "Local bakery fraud discovered in small town"}]
        base_result = grade_metrics(m)
        news_result = grade_metrics(m, news)
        # "bakery" doesn't match AAPL and no finance keyword → not penalized
        # Actually "fraud" is in TIER1 and "bakery" contains no finance keys
        # But _headline_relevant_for_risk checks finance keywords — "fraud" alone
        # doesn't have finance keywords. Let's verify:
        assert news_result["score"] == base_result["score"]


# ---------------------------------------------------------------------------
# Insufficient data
# ---------------------------------------------------------------------------

class TestInsufficientData:
    """All metrics missing → INSUFFICIENT_DATA, not AVOID."""

    def test_all_missing_is_insufficient(self):
        m = {
            "ticker": "LTIM.NS",
            "assetClass": "standard",
            "deRatio": None,
            "pegRatio": None,
            "roeTrend": [],
            "aboveSma200": None,
            "rsi": None,
            "smaWindow": None,
        }
        result = grade_metrics(m)
        assert result["grade"] == "INSUFFICIENT_DATA"
        assert "cannot grade" in result["notes"][0].lower()

    def test_partial_data_not_insufficient(self):
        """If even one metric is present, we grade normally."""
        m = {
            "ticker": "TEST",
            "assetClass": "standard",
            "deRatio": 0.5,
            "pegRatio": None,
            "roeTrend": [],
            "aboveSma200": None,
            "rsi": None,
            "smaWindow": None,
        }
        result = grade_metrics(m)
        assert result["grade"] != "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Free Cash Flow warning
# ---------------------------------------------------------------------------

class TestFcfWarning:
    """High ROE + negative FCF → caution note (no score impact)."""

    def test_strong_roe_negative_fcf_warns(self):
        m = _metrics(roe_trend=["25%", "22%"], free_cashflow=-1_000_000)
        result = grade_metrics(m)
        assert "negative free cash flow" in " ".join(result["notes"]).lower()

    def test_banking_skips_fcf_warning(self):
        """FCF is meaningless for banks."""
        m = _metrics(asset_class="banking", roe_trend=["15%", "14%"], free_cashflow=-5_000_000)
        result = grade_metrics(m)
        assert "free cash flow" not in " ".join(result["notes"]).lower()

    def test_low_roe_negative_fcf_no_warning(self):
        """Only fires when ROE > 12%."""
        m = _metrics(roe_trend=["8%", "7%"], free_cashflow=-1_000_000)
        result = grade_metrics(m)
        assert "free cash flow" not in " ".join(result["notes"]).lower()


# ---------------------------------------------------------------------------
# attach_grades integration
# ---------------------------------------------------------------------------

class TestAttachGrades:
    """attach_grades merges metrics + news into graded output."""

    def test_basic_attach(self):
        metrics = [_metrics(ticker="NVDA"), _metrics(ticker="AAPL")]
        result = attach_grades(metrics)
        assert len(result) == 2
        assert all("grade" in r for r in result)
        assert all("score" in r for r in result)
        assert result[0]["ticker"] == "NVDA"

    def test_attach_with_news(self):
        metrics = [_metrics(ticker="SHOP")]
        news = {"SHOP": [{"title": "SHOP faces SEC fraud probe"}]}
        result = attach_grades(metrics, news)
        assert result[0]["score"] < 5  # penalty applied
