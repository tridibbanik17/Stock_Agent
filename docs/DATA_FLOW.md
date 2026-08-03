# Stock Agent data flow and field reference

## Data flow

```
Extension → FastAPI → yfinance → grading rules → popup / email (Resend)
```

- **Supabase** stores only email, tickers, schedule, and `last_sent_at` (not holdings, not grades).
- **Cron dedupe:** after a successful send, `last_sent_at` is stamped so overlapping/retry ticks skip that preferred-hour slot. A later slot the same day still sends.
- **Gemini** is optional BYOK in the extension; it is **not** used for grades or email today.

## Tags / fields and where they come from

| Tag / field | What it means | Source |
|-------------|----------------|--------|
| Ticker | Symbol (e.g. NVDA, BCE.TO) | User watchlist |
| Price | Live / last market price | yfinance |
| Currency | USD, CAD, … | yfinance |
| Debt-to-Equity | Debt ÷ equity | yfinance balance sheet |
| PEG | Price/earnings vs growth | yfinance `info` |
| ROE trend | Return on equity (~3 years) | yfinance financials |
| Above 200-SMA | Price vs 200-day average | yfinance history |
| SMA | That 200-day average value | yfinance history |
| RSI | Momentum 0–100 | Computed from yfinance closes |
| Sector / Industry | Company classification | yfinance `info` |
| Asset class | `growth_tech` / `crypto_proxy` / `capital_intensive` / `standard` | Derived from yfinance sector / industry / quoteType (+ crypto keywords) |
| Grade / verdict | STRONG BUY / HOLD / AVOID (n/5) | Our grader (rules, not AI) |
| Notes | Short why text | Our grader |
| News risk (optional) | Headline risk line | GoogleNews (cron; often skipped on GitHub Actions) |

## FastAPI

| Endpoint | Job |
|----------|-----|
| `POST /api/quotes/snapshot` | yfinance + grade for popup **Refresh** |
| `POST /api/subscribe` | Save email / tickers / schedule to Supabase (no market data) |

## Grade bands (our rules)

| Score | Tag |
|------:|-----|
| 4–5 | STRONG BUY |
| 3 | HOLD |
| 0–2 | AVOID |

Point checks include low debt, attractive PEG, strong ROE, price above 200-SMA, and oversold RSI. Trusted bad-news headlines can subtract points when news is enabled.
