"""Context-aware grading over yfinance metrics (+ optional news flags)."""

from __future__ import annotations

from typing import Any

# Headlines that justify a score penalty (lawsuits, probes, misses, etc.).
_RISKY_NEWS_KEYS = (
    "lawsuit",
    "sued",
    "suing",
    "sec ",
    "fraud",
    "investigation",
    "investigat",
    "probe",
    "bankrupt",
    "downgrade",
    "misses",
    "missed earnings",
    "earnings miss",
    "recall",
    "layoff",
    "layoffs",
    "subpoena",
    "indict",
    "default",
    "delist",
    "trading halt",
    "smuggl",
    "antitrust",
    "fine ",
    "fined",
    "penalty",
    "scandal",
    "cut guidance",
    "warning letter",
    "class action",
    "whistleblower",
    "accounting irregular",
    "restat",
    # Indian regulatory / enforcement keywords
    "sebi",
    "enforcement directorate",
    "ed probe",
    "ed raid",
    "cbi",
    "income tax raid",
    "it raid",
    "nse notice",
    "bse notice",
    "nse penalty",
    "bse penalty",
    "cci probe",
    "rbi penalty",
    "rbi action",
    "nclt",
    "pmla",
    "money laundering",
    "promoter pledge",
)


def _parse_roe_pct(roe_list: list[str]) -> list[float]:
    values: list[float] = []
    for item in roe_list or []:
        if not item or item == "N/A":
            continue
        try:
            values.append(float(str(item).replace("%", "")))
        except ValueError:
            continue
    return values


def _normalize_news_items(
    news_flags: list[Any] | None,
) -> list[dict[str, str]]:
    """Accept legacy title strings or {title, url} dicts."""
    out: list[dict[str, str]] = []
    for item in news_flags or []:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or item.get("link") or "").strip()
            if title:
                out.append({"title": title[:180], "url": url[:500]})
        else:
            title = str(item).strip()
            if title:
                out.append({"title": title[:180], "url": ""})
        if len(out) >= 5:
            break
    return out


def _is_risky_headline(title: str) -> bool:
    text = f" {str(title or '').lower()} "
    return any(key in text for key in _RISKY_NEWS_KEYS)


# Finance-related keywords that suggest a headline is about markets/stocks.
_FINANCE_HEADLINE_KEYS = (
    "stock",
    "share",
    "market",
    "invest",
    "earnings",
    "revenue",
    "profit",
    "dividend",
    "quarter",
    "ipo",
    "analyst",
    "rating",
    "target",
    "bull",
    "bear",
    "rally",
    "sell",
    "buy",
    "growth",
    "sector",
    "fund",
    "etf",
    "return",
    "portfolio",
    "valuation",
    "pe ratio",
    "forecast",
    "outlook",
    "guidance",
    "ceo",
    "acquisition",
    "merger",
    "split",
    "listing",
    "nse",
    "bse",
    "nasdaq",
    "nyse",
    "tsx",
)


def _headline_looks_relevant(title: str, ticker_base: str) -> bool:
    """
    Filter out irrelevant fluff headlines (athlete bankruptcies, lifestyle articles).
    A headline is relevant if it mentions the ticker/company or contains finance language.
    """
    text = f" {str(title or '').lower()} "
    # Direct ticker mention
    if ticker_base and ticker_base.lower() in text:
        return True
    # Contains finance-related language
    if any(key in text for key in _FINANCE_HEADLINE_KEYS):
        return True
    return False


def grade_metrics(metrics: dict[str, Any], news_flags: list[Any] | None = None) -> dict[str, Any]:
    """
    Score 0–5 with asset-class weighting.
    Returns grade label, score, notes, verdict line for email/UI.
    """
    asset = metrics.get("assetClass") or "standard"
    de_ratio = metrics.get("deRatio")
    peg_ratio = metrics.get("pegRatio")
    above_sma = metrics.get("aboveSma200")
    rsi = metrics.get("rsi")
    sma_window = metrics.get("smaWindow")
    roes = _parse_roe_pct(metrics.get("roeTrend") or [])
    news_items = _normalize_news_items(news_flags)

    score = 0
    notes: list[str] = []
    missing_data: list[str] = []
    ticker_name = str(metrics.get("ticker") or "").upper().split(".")[0]

    try:
        window = int(sma_window) if sma_window is not None else 200
    except (TypeError, ValueError):
        window = 200
    sma_label = "200-day SMA" if window >= 200 else f"{window}-day SMA"

    # --- Index / ETF: corporate ratios do not apply; start neutral (HOLD) ---
    if asset == "index_etf":
        score = 3
        notes.append(
            "INDEX/ETF: tracked against trend (SMA) and RSI momentum — "
            "single-company D/E, PEG, and ROE do not apply."
        )
        if above_sma is True:
            score += 1
            if window < 200:
                notes.append(
                    f"Trend uses a {window}-day SMA (listing history under 200 sessions)."
                )
        elif above_sma is False:
            score -= 1
            notes.append(f"Price is below the {sma_label} (macro downtrend).")
            if window < 200:
                notes.append(
                    f"Trend uses a {window}-day SMA (listing history under 200 sessions)."
                )
        else:
            missing_data.append("200-SMA")

        if isinstance(rsi, (int, float)):
            if rsi < 35:
                score += 1
                notes.append("RSI shows selling fatigue - possible mean-reversion zone.")
            elif rsi > 70:
                score -= 1
                notes.append("RSI is overbought (>70) - avoid chasing; risk of pullbacks.")
        else:
            missing_data.append("RSI")

        risky_news = [
            item for item in news_items
            if _is_risky_headline(item.get("title", ""))
            and _headline_looks_relevant(item.get("title", ""), ticker_name)
        ]
        if risky_news:
            penalty = min(2, len(risky_news))
            score = max(0, score - penalty)
            for item in risky_news[:3]:
                notes.append(f"News risk: {item['title']}")
        elif news_items:
            relevant = [
                item for item in news_items
                if _headline_looks_relevant(item.get("title", ""), ticker_name)
            ]
            for item in relevant[:2]:
                notes.append(f"Headline: {item['title']}")

        if missing_data:
            notes.append(f"Missing data: {', '.join(missing_data)}.")

        score = max(0, min(5, score))
        return _finalize_grade(score, notes, asset, risky_news or news_items)

    # --- Debt-to-Equity ---
    # All D/E values from yfinance are ratios (Total Debt / Total Equity).
    # A value of 1.5 means $1.50 debt per $1 equity, NOT a percentage.
    # Thresholds are sector-aware because structural leverage norms differ.
    if isinstance(de_ratio, (int, float)):
        if asset == "banking":
            # Banks use leverage structurally (deposits are liabilities).
            # D/E 5–15 is normal; only flag extreme outliers.
            if de_ratio < 15.0:
                score += 1
            else:
                notes.append("Leverage is unusually high even for a financial institution.")
        elif asset == "capital_intensive":
            # Utilities/telecom carry infrastructure debt at regulated rates.
            if de_ratio < 3.0:
                score += 1
            elif de_ratio > 4.0:
                notes.append("Debt load is elevated even for a capital-intensive name.")
        elif asset == "crypto_proxy":
            # Crypto treasuries use convertible notes; D/E swings with BTC price.
            # Soft signal only — don't hard-penalize volatility-driven D/E changes.
            if de_ratio < 3.0:
                score += 1
            # No penalty — D/E is unreliable for BTC treasury strategies.
        elif asset == "pharma":
            if de_ratio < 1.0:
                score += 1
            elif de_ratio > 2.0:
                notes.append("Debt is high for a pharma/biotech — may signal acquisition-driven leverage.")
        elif asset == "cyclical":
            # Energy/mining: debt is normal for capital-heavy extraction.
            if de_ratio < 2.0:
                score += 1
            elif de_ratio > 3.5:
                notes.append("Leverage is high for a cyclical — risky if commodity prices drop.")
        elif asset == "conglomerate":
            if de_ratio < 2.0:
                score += 1
            elif de_ratio > 3.5:
                notes.append("Leverage is elevated even for a diversified conglomerate.")
        elif asset == "growth_tech":
            if de_ratio < 1.0:
                score += 1
            elif de_ratio > 2.0:
                notes.append("High debt for a growth name — limits flexibility for R&D investment.")
        else:
            # Standard (consumer, industrials, etc.)
            if de_ratio < 1.5:
                score += 1
            elif de_ratio > 2.5:
                notes.append("High debt burden limits financial flexibility.")
    else:
        if asset not in {"banking", "crypto_proxy"}:
            missing_data.append("D/E")

    # --- PEG ---
    # Guard against NaN/Inf: yfinance can return extreme values for low-growth stocks.
    peg_valid = (
        isinstance(peg_ratio, (int, float))
        and peg_ratio > 0
        and peg_ratio < 100  # Sanity cap — PEG > 100 is meaningless noise.
    )
    if peg_valid:
        if asset == "growth_tech":
            if peg_ratio < 1.5:
                score += 1
            elif peg_ratio > 3.0:
                notes.append("Growth multiple looks stretched vs expected earnings growth.")
        elif asset == "crypto_proxy":
            # PEG is largely meaningless for BTC treasuries — soft credit only.
            if peg_ratio < 2.0:
                score += 1
        elif asset == "pharma":
            # Pipeline optionality isn't captured in near-term EPS growth.
            if peg_ratio < 1.5:
                score += 1
            elif peg_ratio > 3.0:
                notes.append("Valuation stretched relative to near-term earnings growth (pipeline upside not captured in PEG).")
        elif asset == "banking":
            # Banks are valued on P/B and NIM, not growth multiples.
            # PEG is a weak signal — only reward very cheap, never penalize.
            if peg_ratio < 1.0:
                score += 1
            # No penalty for high PEG — banks naturally have high PEG due to
            # low single-digit growth and moderate PE (PE 12 / Growth 5% = PEG 2.4).
        elif asset == "cyclical":
            # Cyclicals have volatile earnings — PEG is unreliable mid-cycle.
            # Only reward clearly cheap; don't penalize during trough years.
            if peg_ratio < 1.0:
                score += 1
            elif peg_ratio > 4.0:
                notes.append("Valuation looks expensive even accounting for cyclical recovery.")
        elif asset == "conglomerate":
            if peg_ratio < 1.5:
                score += 1
            elif peg_ratio > 2.5:
                notes.append("Valuation premium may not be justified by segment growth.")
        else:
            # Standard sectors
            if peg_ratio < 1.0:
                score += 1
            elif peg_ratio > 2.0:
                notes.append("The stock is expensive relative to expected growth (PEG).")
    else:
        if asset not in {"banking", "crypto_proxy", "cyclical"}:
            # PEG unavailable is common for negative-earnings biotech, pre-profit tech.
            # Don't count as "missing" for sectors where PEG is structurally unreliable.
            missing_data.append("PEG")

    # --- ROE trend ---
    if asset == "crypto_proxy":
        notes.append("ROE is a weak signal for crypto-proxy / treasury strategies - discounted.")
        if roes and roes[0] > 10:
            score += 1
        elif not roes:
            missing_data.append("ROE")
    elif asset == "banking":
        # Banks: ROE 10–15% is solid; >15% is excellent; <5% is a red flag.
        if roes:
            if roes[0] > 12:
                score += 1
            if roes[0] < 5:
                notes.append("ROE below 5% is weak for a financial institution.")
            elif len(roes) >= 2 and roes[0] < roes[1]:
                notes.append("Warning: Return on equity is trending downward.")
        else:
            missing_data.append("ROE")
    elif asset == "pharma":
        # Pharma: ROE varies wildly. Pre-revenue biotech will be negative — that's normal.
        if roes:
            if roes[0] > 12:
                score += 1
            if roes[0] < 0:
                notes.append("Negative ROE — common for early-stage biotech, but monitor cash runway.")
            elif len(roes) >= 2 and roes[0] < roes[1]:
                notes.append("Warning: Profit efficiency (ROE) is trending downward.")
        else:
            missing_data.append("ROE")
    elif asset == "capital_intensive":
        # Utilities/telecom: ROE 8–12% is acceptable due to regulated returns.
        if roes:
            if roes[0] > 8:
                score += 1
            if roes[0] < 0:
                notes.append("Company is posting negative ROE (net losses).")
            elif len(roes) >= 2 and roes[0] < roes[1]:
                notes.append("Warning: Profit efficiency (ROE) is trending downward.")
        else:
            missing_data.append("ROE")
    elif asset == "cyclical":
        # Energy/mining: ROE swings with commodity prices.
        # During trough years, ROE = 2–5% is normal, not a red flag.
        # Only flag genuinely negative ROE or clear deterioration.
        if roes:
            if roes[0] > 8:
                score += 1
            if roes[0] < -5:
                notes.append("Deep negative ROE — may signal structural issues beyond the cycle.")
            elif roes[0] < 0:
                notes.append("Negative ROE — may reflect commodity cycle trough rather than mismanagement.")
            elif len(roes) >= 2 and roes[0] < roes[1] * 0.5:
                # Only flag if ROE dropped by more than half (not normal volatility)
                notes.append("Warning: ROE declined significantly — check if cyclical or structural.")
        else:
            missing_data.append("ROE")
    else:
        # Growth tech, conglomerate, standard — 15%+ is the bar.
        if roes:
            if roes[0] > 15:
                score += 1
            if roes[0] < 0:
                notes.append("Company is posting negative ROE (net losses).")
            elif len(roes) >= 2 and roes[0] < roes[1]:
                notes.append("Warning: Profit efficiency (ROE) is trending downward.")
        else:
            missing_data.append("ROE")

    # --- Free Cash Flow warning (informational, no score impact) ---
    # High ROE + negative FCF can signal financial engineering or aggressive accounting.
    # Only note this when both data points are available — never penalize missing FCF.
    fcf = metrics.get("freeCashflow")
    if isinstance(fcf, (int, float)) and fcf < 0 and roes and roes[0] > 12:
        if asset not in {"banking", "pharma", "capital_intensive", "cyclical"}:
            # Banking: FCF is meaningless (deposits/lending distort cash flow statement).
            # Pharma: Negative FCF common during heavy R&D investment phases.
            # Capital-intensive: Utilities/REITs run negative FCF during infrastructure build-outs.
            # Cyclical: Energy/mining capex cycles cause temporary negative FCF.
            notes.append(
                "Caution: strong ROE but negative free cash flow — verify earnings quality."
            )

    # --- 200-day SMA (or shorter window for new listings / CDRs) ---
    if above_sma is True:
        score += 1
        if window < 200:
            notes.append(
                f"Trend uses a {window}-day SMA (listing history under 200 sessions)."
            )
    elif above_sma is False:
        notes.append(f"Price is below the {sma_label} (macro downtrend).")
        if window < 200:
            notes.append(
                f"Trend uses a {window}-day SMA (listing history under 200 sessions)."
            )
    else:
        missing_data.append("200-SMA")

    # --- RSI ---
    if isinstance(rsi, (int, float)):
        if rsi < 35:
            score += 1
            notes.append("RSI shows selling fatigue - possible mean-reversion zone.")
        elif rsi > 70:
            score = max(0, score - 1)
            notes.append("RSI is overbought (>70) - avoid chasing; risk of pullbacks.")
    else:
        missing_data.append("RSI")

    # --- News: only penalize clearly risky headlines ---
    # IMPORTANT: Risk headlines must also be relevant to the ticker.
    # A generic article about "bankruptcy" that doesn't mention the stock
    # should not penalize the score (e.g. Spotify getting an athlete bankruptcy article).
    risky_news = [
        item for item in news_items
        if _is_risky_headline(item.get("title", ""))
        and _headline_looks_relevant(item.get("title", ""), ticker_name)
    ]
    if risky_news:
        # Cap at -2 so one probe does not erase an otherwise strong card.
        penalty = min(2, len(risky_news))
        score = max(0, score - penalty)
        for item in risky_news[:3]:
            notes.append(f"News risk: {item['title']}")
    elif news_items:
        # Only show neutral headlines that look relevant to the ticker/company.
        # Skip generic lifestyle/human-interest articles that Yahoo sometimes bundles.
        relevant = [
            item for item in news_items
            if _headline_looks_relevant(item.get("title", ""), ticker_name)
        ]
        for item in relevant[:2]:
            notes.append(f"Headline: {item['title']}")

    if missing_data:
        notes.append(f"Missing data: {', '.join(missing_data)}.")

    return _finalize_grade(score, notes, asset, risky_news or news_items)


def _finalize_grade(
    score: int,
    notes: list[str],
    asset: str,
    news_risks: list[dict[str, str]],
) -> dict[str, Any]:
    score = max(0, min(5, int(score)))

    # If all scoring inputs are missing, don't assign a failing grade.
    # "INSUFFICIENT DATA" is more honest than "AVOID" when we couldn't analyze.
    missing_note = next((n for n in notes if n.startswith("Missing data:")), "")
    all_missing = all(
        k in missing_note
        for k in ("D/E", "PEG", "ROE", "200-SMA", "RSI")
    ) if missing_note else False

    if all_missing and score == 0:
        return {
            "grade": "INSUFFICIENT_DATA",
            "score": 0,
            "verdict": "INSUFFICIENT DATA",
            "notes": ["All core metrics unavailable — cannot grade this ticker reliably."] + notes,
            "assetClass": asset,
            "newsRisks": (news_risks or [])[:3],
        }

    if score >= 4:
        grade = "STRONG_BUY"
        verdict = f"STRONG BUY ({score}/5)"
        if asset == "index_etf":
            notes.insert(0, "Trend and momentum look constructive for this index/ETF.")
        else:
            notes.insert(0, "Fundamentals align with momentum on the rule scorecard.")
    elif score == 3:
        grade = "HOLD"
        verdict = f"HOLD ({score}/5)"
        if asset == "index_etf":
            notes.insert(0, "Neutral index/ETF posture on SMA and RSI.")
        else:
            hold_msg = _contextual_hold_message(notes, asset)
            notes.insert(0, hold_msg)
    else:
        grade = "AVOID"
        verdict = f"AVOID ({score}/5)"
        if not notes:
            notes.insert(0, "Weak scores across valuation, quality, or trend.")

    return {
        "grade": grade,
        "score": score,
        "verdict": verdict,
        "notes": notes,
        "assetClass": asset,
        "newsRisks": (news_risks or [])[:3],
    }


def _contextual_hold_message(notes: list[str], asset: str) -> str:
    """
    Generate a HOLD summary that explains why the stock is neutral (3/5)
    instead of repeating generic 'Decent health' for every HOLD ticker.
    """
    notes_blob = " ".join(notes).lower()

    has_sma_negative = "below" in notes_blob and "sma" in notes_blob
    has_rsi_overbought = "overbought" in notes_blob
    has_roe_declining = "roe" in notes_blob and ("trending downward" in notes_blob or "negative" in notes_blob)
    has_peg_stretched = "stretched" in notes_blob or "expensive" in notes_blob
    has_debt = "debt" in notes_blob and ("elevated" in notes_blob or "high" in notes_blob)

    # Build a specific reason for the neutral score
    if has_sma_negative and not has_roe_declining:
        return "Solid fundamentals held back by a weak technical trend."
    if has_roe_declining and not has_sma_negative:
        return "Good momentum, but profitability is softening."
    if has_rsi_overbought:
        return "Strong setup, but RSI overbought — watch for pullbacks before adding."
    if has_peg_stretched:
        return "Quality name, but current valuation is a stretch for this growth rate."
    if has_debt:
        return "Growth present, but leverage warrants caution on risk sizing."
    if has_sma_negative and has_roe_declining:
        return "Mixed signals: trend and profitability both under pressure."

    # Fallback — still better than the old generic message
    if asset == "growth_tech":
        return "Growth profile intact but no standout signal to push the grade higher."
    if asset == "capital_intensive":
        return "Stable cash flows, but no clear catalyst for re-rating right now."
    if asset == "crypto_proxy":
        return "Neutral on metrics — crypto proxy pricing remains sentiment-driven."
    if asset == "banking":
        return "Financials are adequate but no clear edge vs peers at this valuation."
    if asset == "pharma":
        return "Pipeline and margins are acceptable, but no breakout catalyst visible."
    if asset == "conglomerate":
        return "Diversified business is stable, but segment mix offers no strong signal."
    if asset == "cyclical":
        return "Commodity-exposed name in a neutral cycle position — wait for clearer trend."
    return "Balanced scorecard — no strong buy or sell signal at this time."


def attach_grades(
    metrics_list: list[dict[str, Any]],
    news_by_ticker: dict[str, list[Any]] | None = None,
) -> list[dict[str, Any]]:
    news_by_ticker = news_by_ticker or {}
    graded = []
    for metrics in metrics_list:
        ticker = metrics.get("ticker", "")
        grade_block = grade_metrics(metrics, news_by_ticker.get(ticker))
        graded.append({**metrics, **grade_block})
    return graded
