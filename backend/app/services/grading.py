"""Context-aware grading over yfinance metrics (+ optional news flags)."""

from __future__ import annotations

from typing import Any


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


def grade_metrics(metrics: dict[str, Any], news_flags: list[str] | None = None) -> dict[str, Any]:
    """
    Score 0–5 with asset-class weighting.
    Returns grade label, score, notes, verdict line for email/UI.
    """
    asset = metrics.get("assetClass") or "standard"
    de_ratio = metrics.get("deRatio")
    peg_ratio = metrics.get("pegRatio")
    above_sma = metrics.get("aboveSma200")
    rsi = metrics.get("rsi")
    roes = _parse_roe_pct(metrics.get("roeTrend") or [])
    news_flags = news_flags or []

    score = 0
    notes: list[str] = []

    # --- Debt-to-Equity ---
    if isinstance(de_ratio, (int, float)):
        if asset == "capital_intensive":
            # Telecom / towers: higher leverage is normal.
            if de_ratio < 3.0:
                score += 1
            else:
                notes.append("Debt load is elevated even for a capital-intensive name.")
        elif asset == "crypto_proxy":
            # Corporate ROE/D-E is noisy for BTC proxies — soft weight.
            if de_ratio < 2.5:
                score += 1
            else:
                notes.append("Balance-sheet leverage is high; treat crypto-proxy debt carefully.")
        else:
            if de_ratio < 1.5:
                score += 1
            else:
                notes.append("High debt burden limits financial flexibility.")

    # --- PEG ---
    if isinstance(peg_ratio, (int, float)):
        if asset == "growth_tech":
            if peg_ratio < 1.5:
                score += 1
            elif peg_ratio > 3.0:
                notes.append("Growth multiple looks stretched vs expected earnings growth.")
        elif asset == "crypto_proxy":
            # PEG often meaningless — skip hard fail, soft credit only.
            if peg_ratio < 2.0:
                score += 1
        else:
            if peg_ratio < 1.0:
                score += 1
            elif peg_ratio > 2.0:
                notes.append("The stock is expensive relative to expected growth (PEG).")

    # --- ROE trend ---
    if asset == "crypto_proxy":
        notes.append("ROE is a weak signal for crypto-proxy / treasury strategies - discounted.")
        if roes and roes[0] > 10:
            score += 1
    else:
        if roes and roes[0] > 15:
            score += 1
        if len(roes) >= 2 and roes[0] < roes[1]:
            notes.append("Warning: Profit efficiency (ROE) is trending downward.")

    # --- 200-day SMA ---
    if above_sma is True:
        score += 1
    elif above_sma is False:
        notes.append("Price is below the 200-day SMA (macro downtrend).")

    # --- RSI ---
    if isinstance(rsi, (int, float)):
        if rsi < 35:
            score += 1
            notes.append("RSI shows selling fatigue - possible mean-reversion zone.")
        elif rsi > 70:
            notes.append("RSI is overbought - avoid chasing; consider trimming.")

    # --- Qualitative news risk (existential headlines) ---
    critical = [f for f in news_flags if f]
    if critical:
        score = max(0, score - 2)
        for flag in critical[:3]:
            notes.append(f"News risk: {flag}")

    if score >= 4:
        grade = "STRONG_BUY"
        verdict = f"STRONG BUY ({score}/5)"
        notes.insert(0, "Fundamentals align with momentum - look to add exposure.")
    elif score == 3:
        grade = "HOLD"
        verdict = f"HOLD ({score}/5)"
        notes.insert(0, "Decent health, but lacks a strong trigger right now.")
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
    }


def attach_grades(
    metrics_list: list[dict[str, Any]],
    news_by_ticker: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    news_by_ticker = news_by_ticker or {}
    graded = []
    for metrics in metrics_list:
        ticker = metrics.get("ticker", "")
        grade_block = grade_metrics(metrics, news_by_ticker.get(ticker))
        graded.append({**metrics, **grade_block})
    return graded
