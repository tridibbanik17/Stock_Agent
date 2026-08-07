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
| `conglomerate` | industry contains conglomerate/diversified industrials | Berkshire, Tata group |
| `growth_tech` | sector = Technology, or semiconductor/software/internet/auto industry | TCS.NS, NVDA, SHOP.TO |
| `crypto_proxy` | quoteType = CRYPTOCURRENCY, or crypto keywords in description | MSTR, BTC-USD |
| `standard` | Everything else (consumer, energy, materials, industrials) | ITC.NS, ONGC.NS, WMT |

**CDR override:** Canadian Depositary Receipts (quoteType=ETF but name contains "CDR" or "CAD Hedged") are graded as their underlying equity, not as ETFs.

### D/E (Debt-to-Equity) Thresholds

| Asset Class | +1 if D/E < | Warning if D/E > | Rationale |
|---|---|---|---|
| **banking** | 15.0 | 15.0 | Leverage IS the business model for financials |
| **capital_intensive** | 3.0 | 3.0 | Infrastructure debt is normal for utilities/telecom |
| **crypto_proxy** | 2.5 | 2.5 | Treasury strategies tolerate moderate leverage |
| **conglomerate** | 2.0 | 3.5 | Diversified segments spread debt risk |
| **pharma** | 1.0 | 2.0 | R&D-funded, should be relatively lean |
| **growth_tech** | 1.0 | 2.0 | Tech should be lean; debt limits R&D flexibility |
| **standard** | 1.5 | 2.5 | General threshold for consumer/energy/industrials |

### PEG (Price/Earnings-to-Growth) Thresholds

| Asset Class | +1 if PEG < | Warning if PEG > | Rationale |
|---|---|---|---|
| **growth_tech** | 1.5 | 3.0 | High PE expected if growth backs it up |
| **pharma** | 1.5 | 3.0 | Pipeline optionality not fully captured in EPS |
| **banking** | 1.2 | 2.0 | Banks valued on P/B and NIM, not growth multiple |
| **conglomerate** | 1.5 | 2.5 | Segment diversity should earn a modest premium |
| **crypto_proxy** | 2.0 | — | PEG often meaningless; soft credit only |
| **standard** | 1.0 | 2.0 | Traditional value threshold |

### ROE (Return on Equity) Thresholds

| Asset Class | +1 if ROE > | Notes |
|---|---|---|
| **banking** | 12% | ROE 10–15% is solid for banks; <5% is a red flag |
| **capital_intensive** | 8% | Regulated returns cap ROE for utilities |
| **pharma** | 12% | Negative ROE OK for pre-revenue biotech (noted) |
| **crypto_proxy** | 10% | Weak signal — discounted weight |
| **growth_tech, conglomerate, standard** | 15% | Standard profitability bar |

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
