# Grading Engine — Sector-Aware Scoring

Rule-based grading system for the Stock Agent Chrome extension.  
Grades are deterministic (no AI/LLM) and update on every cron tick or popup refresh.

## Score: 0–5

| Score | Grade | Meaning |
|-------|-------|---------|
| 4–5 | **STRONG BUY** | Fundamentals + momentum align on the rule scorecard |
| 3 | **HOLD** | Balanced — no strong signal either way |
| 0–2 | **AVOID** | Weak scores across valuation, quality, or trend |

## Scoring Inputs (5 factors)

Each factor contributes +1 when healthy, 0 when missing/neutral, and may subtract when problematic.

| # | Factor | What it measures | Source |
|---|--------|-----------------|--------|
| 1 | **D/E ratio** | Balance sheet leverage | yfinance (Screener.in fallback for India) |
| 2 | **PEG ratio** | Valuation vs growth | yfinance (ADR peer fallback for India) |
| 3 | **ROE trend** | Profitability and direction | yfinance (Screener.in fallback for India) |
| 4 | **200-day SMA** | Long-term price trend | yfinance history |
| 5 | **RSI (14-day)** | Momentum / overbought/oversold | yfinance history |

**Bonus/penalty:** News headlines with risk keywords (lawsuit, probe, fraud, SEBI, etc.) can subtract up to 2 points.

## Sector-Aware Thresholds

Different industries have structurally different leverage, profitability, and valuation norms.  
The grading engine adjusts thresholds by **asset class** so a bank isn't penalized for being leveraged the same way a tech stock would be.

### Asset Class Detection

Classification is automatic from yfinance metadata (`sector`, `industry`, `quoteType`):

| Asset Class | Detected by | Examples |
|---|---|---|
| `index_etf` | quoteType = ETF/MUTUALFUND/INDEX (excluding CDRs) | VFV.TO, SPY, NIFTY50 |
| `banking` | sector = Financial Services, or industry contains bank/insurance/credit | HDFCBANK.NS, JPM, TD.TO |
| `pharma` | sector = Healthcare, or industry contains drug/biotech/medical | SUNPHARMA.NS, JNJ, PFE |
| `capital_intensive` | sector = Utilities/Real Estate, or telecom/tower industry | NTPC.NS, BCE.TO, NEE |
| `cyclical` | sector = Energy/Basic Materials, or oil/gas/mining/metals industry | ONGC.NS, XOM, VALE |
| `conglomerate` | industry contains conglomerate/diversified industrials | Berkshire, Tata group |
| `growth_tech` | sector = Technology, or semiconductor/software/internet/auto industry | TCS.NS, NVDA, SHOP.TO |
| `crypto_proxy` | quoteType = CRYPTOCURRENCY, or crypto keywords in description | MSTR, BTC-USD |
| `standard` | Everything else (consumer staples/cyclical, industrials not classified above) | ITC.NS, WMT, HD |

**CDR override:** Canadian Depositary Receipts (quoteType=ETF but name contains "CDR" or "CAD Hedged") are graded as their underlying equity, not as ETFs.

### D/E (Debt-to-Equity) Thresholds

All D/E values are **ratios** (Total Debt / Total Equity), not percentages.  
A value of 1.5 means $1.50 debt per $1 equity. yfinance always returns this as a decimal ratio.

| Asset Class | +1 if D/E < | Warning if D/E > | Rationale |
|---|---|---|---|
| **banking** | 15.0 | 15.0 | Deposits are liabilities — leverage IS the business model |
| **capital_intensive** | 3.0 | 4.0 | Infrastructure debt at regulated rates is structural |
| **crypto_proxy** | 3.0 | — (no penalty) | D/E swings with BTC price via convertible notes; unreliable signal |
| **cyclical** | 2.0 | 3.5 | Energy/mining carry extraction capex debt |
| **conglomerate** | 2.0 | 3.5 | Diversified segments spread debt risk |
| **pharma** | 1.0 | 2.0 | R&D-funded, should be relatively lean |
| **growth_tech** | 1.0 | 2.0 | Tech should be lean; debt limits R&D flexibility |
| **standard** | 1.5 | 2.5 | General threshold for consumer/industrials |

### PEG (Price/Earnings-to-Growth) Thresholds

**Important:** PEG is only scored when `0 < PEG < 100`. Values above 100 are treated as noise (undefined earnings growth or rounding artifacts). Negative PEG values (from negative growth) are also excluded.

| Asset Class | +1 if PEG < | Warning if PEG > | Penalty for missing? | Notes |
|---|---|---|---|---|
| **growth_tech** | 1.5 | 3.0 | Yes | High PE expected if growth backs it up |
| **pharma** | 1.5 | 3.0 | Yes | Pipeline optionality not captured in EPS |
| **banking** | 1.0 | — (no penalty) | No | Banks valued on P/B and NIM, not growth multiple. PEG is a weak signal. |
| **cyclical** | 1.0 | 4.0 | No | Earnings swing with commodity cycle — PEG is unreliable mid-cycle |
| **conglomerate** | 1.5 | 2.5 | Yes | Segment diversity earns a modest premium |
| **crypto_proxy** | 2.0 | — (no penalty) | No | PEG is largely meaningless for BTC treasuries |
| **standard** | 1.0 | 2.0 | Yes | Traditional value threshold |

### ROE (Return on Equity) Thresholds

| Asset Class | +1 if ROE > | Special handling | Notes |
|---|---|---|---|
| **banking** | 12% | < 5% is a red flag | ROE 10–15% is solid for banks |
| **capital_intensive** | 8% | Negative = flag | Regulated returns cap ROE |
| **cyclical** | 8% | Only flag ROE < -5% (deep negative). Mild negative = normal trough. | Commodity cycles cause multi-year ROE swings |
| **pharma** | 12% | Negative ROE noted as "common for early-stage biotech" | Pre-revenue biotech is expected |
| **crypto_proxy** | 10% | ROE discounted as weak signal | Treasury strategies distort ROE |
| **growth_tech, conglomerate, standard** | 15% | Negative = flag, declining = warn | Standard profitability bar |

### Cyclical / Commodity (NEW)

Energy and materials stocks (oil, gas, mining, metals, steel) are now classified as `cyclical`:
- **ROE:** Lower bar (8%) and lenient on negative ROE during commodity troughs. Only flags deep negative (< -5%) as potentially structural.
- **PEG:** Only rewards very cheap (< 1.0); doesn't penalize high PEG during trough earnings (earnings denominator is suppressed during down-cycles).
- **D/E:** Moderate threshold (< 2.0) with warning at 3.5.

### SMA & RSI (Technical Trend)

Same for all asset classes (except index_etf which uses only these):

| Factor | +1 | -1 | Notes |
|---|---|---|---|
| Price > 200-day SMA | Yes | No penalty, just a note | Shorter SMA used when listing history < 200 days |
| RSI < 35 | Yes (selling fatigue) | — | Mean-reversion opportunity |
| RSI > 70 | — | Yes (overbought) | Risk of pullback |

### Index / ETF Scoring

ETFs start at score 3 (neutral) and only move on SMA and RSI:
- Above SMA: +1
- Below SMA: -1
- RSI < 35: +1
- RSI > 70: -1

Corporate fundamentals (D/E, PEG, ROE) are not applicable to passive funds.

## Informational Warnings (no score impact)

These notes surface risk signals that don't fit cleanly into the 5-factor score but are important for the user to see.

| Condition | Note shown | Excluded for |
|---|---|---|
| ROE > 12% AND freeCashflow < 0 | "Caution: strong ROE but negative free cash flow — verify earnings quality." | Banking (FCF meaningless for deposit-takers), Pharma (negative FCF during R&D is structural) |
| ROE trending downward (current < previous year) | "Warning: Profit efficiency (ROE) is trending downward." | — |
| SMA window < 200 days | "Trend uses a {N}-day SMA (listing history under 200 sessions)." | — |
| Cyclical ROE mildly negative (0 to -5%) | "Negative ROE — may reflect commodity cycle trough rather than mismanagement." | Non-cyclical sectors |

**Design choice:** These are informational only. Adding them to the score would require a 6th factor, breaking the clean 0–5 scale. They help users make informed decisions without the system overstepping into "advice."

## Grade Naming Rationale

| Grade | Why this name? |
|---|---|
| **STRONG BUY** | Standard analyst language. Implies quality assessment, not a direct instruction. |
| **HOLD** | Neutral — no action implied. Familiar to any retail investor. |
| **AVOID** | Deliberately NOT "SELL." "Sell" is a direct financial instruction that implies the user should liquidate a position — creating liability. "Avoid" means "our rules flagged problems" without prescribing action. It protects both the user and the developer. |

## News Risk Penalty

Headlines from Yahoo Finance RSS (+ Indian media outlets) are scanned for risk keywords:

**US/Global:** lawsuit, SEC, fraud, investigation, probe, bankrupt, downgrade, recall, layoff, class action, whistleblower, accounting irregular

**India-specific:** SEBI, enforcement directorate, ED probe/raid, CBI, income tax raid, NSE/BSE notice/penalty, RBI penalty, NCLT, PMLA, money laundering, promoter pledge

**Scoring:** Up to -2 points for risky headlines (capped so one probe doesn't destroy an otherwise strong card).

**Relevance filter:** Neutral (non-risk) headlines are only shown if they mention the ticker name or contain finance-related language. Generic lifestyle articles are filtered out.

## Data Source Priority

```
┌─────────────────────────────────────────────────────┐
│ Primary: yfinance (price + fundamentals + history)  │
│   ↓ (if price missing for .NS/.BO)                 │
│ Fallback 1: Google Finance (price only)             │
│   ↓ (if D/E or ROE missing for .NS/.BO)            │
│ Fallback 2: Screener.in (PE, ROE, ROCE)            │
│   ↓ (if PEG/sector missing)                        │
│ Fallback 3: US ADR peer (INFY→INFY NYSE, etc.)     │
└─────────────────────────────────────────────────────┘
```

## Supported Regions

| Region | Exchanges | Currency | Timezone aliases |
|---|---|---|---|
| **USA** | NASDAQ, NYSE, AMEX | USD | Eastern, Central, Mountain, Pacific, Alaska, Hawaii |
| **Canada** | TSX, TSXV, NEO | CAD | Toronto, Vancouver, Calgary, Edmonton, Winnipeg, Halifax |
| **India** | NSE (.NS), BSE (.BO) | INR | Asia/Kolkata, Mumbai, Delhi, Bangalore, Chennai, Hyderabad |

## Disclaimer

Grades are calculated via rule-based technical/fundamental heuristics.  
They do not constitute financial advice. Past performance does not guarantee future results.  
The system does not know your portfolio size, risk tolerance, or investment horizon.

Privacy: tickers only — never share counts, buy prices, or API keys.
