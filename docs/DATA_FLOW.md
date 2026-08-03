# Stock Agent data flow and field reference

## Data flow

```
Extension → FastAPI → yfinance → grading rules → popup / email (Resend)
```

- **Supabase** stores only email, tickers, schedule, `last_sent_at`, and `unsubscribe_token` (not holdings, not grades).
- **Subscribe** is email-keyed upsert: Save & Subscribe overwrites that address’s watchlist and schedule.
- **Delivery audit:** each cron send writes a `delivery_logs` row (`success` / `failure` / `dry_run`, Resend id, truncated error) — query in Supabase instead of digging through Actions logs.
- **HTML email:** cron always sends a full HTML report (plus plain-text fallback) on schedule.
- **Hybrid B dispatch:** GitHub Actions every 5 minutes `POST`s `/api/internal/dispatch-due` (header `X-Dispatch-Secret`). The API sends users in the early window (`preferred − DISPATCH_EARLY_MINUTES` … `preferred`) or overdue catch-up (up to `DISPATCH_LATE_MINUTES`). Preferred time is a deadline; `last_sent_at` is stamped at the slot instant for dedupe.
- **Hosted API:** production FastAPI via Docker / Render (`docs/DEPLOY.md`). Extension switches with `USE_LOCAL_API` in `extension/lib/config.js`.
- **Cron shared quote cache:** each tick unions matched users’ tickers, fetches/grades each symbol once, then reuses those quotes for every email (avoids N× yfinance hits when watchlists overlap).
- **Cron fan-out:** matched users are emailed in parallel (`CRON_DISPATCH_WORKERS`, default 8) so one slow Resend call does not block the rest of the tick. News uses Yahoo RSS (CI-safe) with a short disk cache; set `STOCK_AGENT_SKIP_NEWS=1` to disable.
- **Unsubscribe:** report emails include `GET /api/unsubscribe?token=…` (also `POST` / `DELETE`). That flips `enabled=false` without the extension.
- **Gemini** is optional BYOK in the Chrome extension only (Test AI / auto-analyze on-device). It is **not** used for grades, news risk, or scheduled email.

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
| News risk (optional) | Headline risk line | Yahoo Finance RSS (primary, CI-safe) → yfinance news → optional GoogleNews; disk TTL cache |

## FastAPI

| Endpoint | Job |
|----------|-----|
| `POST /api/quotes/snapshot` | yfinance + grade for popup **Refresh** |
| `POST /api/subscribe` | Save email / tickers / schedule (upsert by email) |
| `POST /api/internal/dispatch-due` | Hybrid B wake-up: send due/overdue reports (`X-Dispatch-Secret`) |
| `GET` / `POST` / `DELETE /api/unsubscribe` | Soft-disable emails via opaque `unsubscribe_token` |

Rate limits (per IP, in-process): subscribe ~10/min, snapshot ~30/min, unsubscribe ~30/min. Also rejects non-JSON POST bodies and payloads over `MAX_REQUEST_BODY_BYTES`.

## Grade bands (our rules)

| Score | Tag |
|------:|-----|
| 4–5 | STRONG BUY |
| 3 | HOLD |
| 0–2 | AVOID |

Point checks include low debt, attractive PEG, strong ROE, price above 200-SMA, and oversold RSI. Trusted bad-news headlines can subtract points when news is enabled.
