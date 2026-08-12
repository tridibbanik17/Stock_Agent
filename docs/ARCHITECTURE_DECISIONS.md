# Stock Agent — Complete System Reference

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  CHROME EXTENSION (Manifest V3)                                      │
│  ├── popup.js — dashboard controller (watchlist, grades, schedule)   │
│  ├── storage.js — two-tier Chrome storage (private vs cloud-eligible)│
│  ├── api.js — HTTPS client for backend (quotes + subscribe)          │
│  ├── gemini.js — BYOK AI explanations (browser → Google directly)    │
│  ├── tickers.js — local autocomplete from static tickers.json        │
│  └── background.js — lightweight MV3 service worker (message router) │
├─────────────────────────────────────────────────────────────────────┤
│  BACKEND API (FastAPI on Heroku)                                     │
│  ├── /api/subscribe — upsert user delivery preferences               │
│  ├── /api/quotes/snapshot — fetch + grade tickers on demand           │
│  ├── /api/internal/dispatch-due — cron endpoint for scheduled emails │
│  ├── /api/unsubscribe — one-click email unsubscribe                  │
│  └── services/ — grading, market_data, news, email_report, dispatch  │
├─────────────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                                                      │
│  ├── Heroku — always-on FastAPI server (no cold starts)              │
│  ├── Supabase — PostgreSQL database (users, delivery_logs)           │
│  ├── AWS EventBridge + Lambda — cron every 5 min (dispatch trigger)  │
│  ├── Resend — transactional email delivery (DKIM-signed domain)      │
│  └── GitHub Actions — CI only (version bumps), cron disabled         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Chrome Extension — What Happens When a User Opens It

1. **Immediate theme load** — reads `chrome.storage.local` to apply dark/light mode before paint (no flash)
2. **Hydrate from storage** — loads watchlist, holdings, delivery prefs, Gemini key, cached quotes
3. **Render watchlist** — shows cached grades instantly (no network needed for quick reopens)
4. **Smart auto-refresh** — only fetches from API if cache is stale (> 5 min) or incomplete
5. **Progressive quote loading** — chunks tickers into groups of 4, fires all in parallel, renders each chunk as it arrives (streaming UX — first results in ~4s, not 30s)

### Two-Tier Storage Model (Privacy Architecture)

| Tier | What's stored | Where | Leaves device? |
|------|--------------|-------|----------------|
| **Private** | Holdings (shares, avg buy price), Gemini API key | `chrome.storage.local` | **Never** |
| **Cloud-eligible** | Watchlist (ticker symbols only), email, schedule, timezone | `chrome.storage.local` + backend | Yes, on Subscribe |

**Privacy guard:** `assertNoPrivateLeak()` walks every outbound payload and throws a hard error if any private field (holdings, shares, buyPrice, geminiApiKey) is found. This is a runtime safety net — not just a convention.

### Ticker Autocomplete

- Static `tickers.json` file bundled with the extension (~thousands of US/TSX/NSE/BSE symbols)
- Loaded lazily on first keystroke via `chrome.runtime.getURL`
- Prefix-matched client-side — no API call for suggestions
- On "Add Ticker": validates by fetching a live quote — if yfinance returns no price (and it's not a network error), the ticker is auto-removed

### Grade Fetching & Display

```
User clicks Refresh (or auto on popup open)
  → api.js POST /api/quotes/snapshot {watchlist: ["NVDA", "TSLA", ...]}
  → Backend: yfinance fetch + grading engine + news scanning
  → Response: [{ticker, price, grade, verdict, notes, ...}, ...]
  → popup.js merges into quoteCache, re-renders cards
```

**Grade flicker prevention:** If a fresh response is missing SMA data (yfinance hiccup) but the cache has a complete grade, the cached grade is preserved and only the price is updated.

### Save & Subscribe Flow

```
User fills email + schedule + clicks "Save & Subscribe"
  → Validates: email, ≥1 ticker, ≥1 day, ≥1 time
  → buildCloudPayload() → assertNoPrivateLeak()
  → POST /api/subscribe {email, watchlist, schedule, enabled}
  → Backend: upserts user in Supabase
  → If user is in a send window right now → grades + sends email immediately
  → Returns: {id, report_send_status: "sent"|"not_due"|"daily_cap"|...}
  → Extension shows confirmation status
```

### AI Explain Feature (Optional, BYOK)

| Aspect | Detail |
|--------|--------|
| Provider | Google Gemini (user's own API key from AI Studio) |
| Data flow | Browser → `generativelanguage.googleapis.com` directly. **Never touches our servers.** |
| Model fallback | Tries 5 models in order: `gemini-3.1-flash-lite` → `2.5-flash-lite` → `2.5-flash` → `flash-latest` → `2.0-flash-lite` |
| Chunking | 2 tickers per request (free-tier models skip rows in large batches) |
| Output format | `TICKER|one sentence explanation (max 22 words)` |
| Retry logic | First pass → retry missing → last resort one-at-a-time |
| Purpose | Translates rule-based scores into natural language ("HOLD because leverage is fine but momentum is mixed") |

**Key point:** Grades are ALWAYS deterministic rules. AI only generates explanations of those grades — it does not influence the score.

---

## Backend API — FastAPI on Heroku

### Core Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/subscribe` | POST | Upsert user (email, watchlist, schedule). Triggers immediate send if in window. |
| `/api/quotes/snapshot` | POST | Fetch + grade tickers on demand (for extension popup). |
| `/api/internal/dispatch-due` | POST | Cron endpoint — finds users whose send time is now, sends emails. Auth: `X-Dispatch-Secret` header. |
| `/api/unsubscribe?token=...` | GET | One-click unsubscribe from email link. |
| `/api/resubscribe?token=...` | GET | Re-enable after unsubscribe. |
| `/health` | GET | Returns `{"status": "ok"}` — used for monitoring. |

### Rate Limiting (Per Client IP)

| Endpoint | Limit | Window |
|----------|-------|--------|
| Subscribe | 10/min | 60s sliding |
| Snapshot | 30/min | 60s sliding |
| Unsubscribe | 30/min | 60s sliding |

Abuse protection: rejects oversized bodies (>64KB), non-JSON POSTs, and exposes `X-RateLimit-*` headers.

---

## Grading Engine — Deterministic Rules (No AI)

### How Scores Work

Each stock starts at 0 and earns up to 5 points:

| Factor | +1 if | -1 if | Source |
|--------|-------|-------|--------|
| **D/E** (Debt-to-Equity) | Below sector threshold (1.5 standard, 1.0 tech, 15.0 banks) | — | yfinance / Screener.in |
| **PEG** (Price/Earnings-to-Growth) | Below threshold (1.0 standard, 1.5 tech) | — | yfinance / US ADR peer |
| **ROE** (Return on Equity) | Above 15% (or 8% utilities, 12% banks) | — | yfinance / Screener.in |
| **200-SMA** (Simple Moving Average) | Price above 200-day SMA | — | yfinance history |
| **RSI** (Relative Strength Index) | Below 35 (oversold) | Above 70 (overbought) | yfinance history |
| **News risk** (tiered) | — | Severe: -2, Moderate: -1, Mild: 0 | Yahoo RSS keywords |

**Final grade:** 4–5 = STRONG BUY, 3 = HOLD, 0–2 = AVOID.

### News Severity Tiers

| Tier | Penalty | Cap | Examples |
|------|---------|-----|----------|
| Severe | -2 each | -3 total | fraud, bankruptcy, SEC, delisting, money laundering |
| Moderate | -1 each | -2 total | lawsuit, downgrade, layoffs, earnings miss, SEBI probe |
| Mild | 0 | — | guidance cut, analyst concern (informational only) |

### Sector-Aware Asset Classes

The grading engine adjusts thresholds by detected asset class:

| Class | Detection | D/E threshold | PEG handling |
|-------|-----------|---------------|--------------|
| `index_etf` | quoteType = ETF (excluding CDRs) | N/A | N/A |
| `banking` | Financial Services sector | < 15.0 (leverage is structural) | Weak signal, only reward cheap |
| `growth_tech` | Technology sector | < 1.0 | < 1.5 |
| `crypto_proxy` | Crypto keywords in description | < 3.0 (soft) | Largely meaningless |
| `capital_intensive` | Utilities, telecom | < 3.0 | Standard |
| `cyclical` | Energy, mining, materials | < 2.0 | Only reward < 1.0 |
| `pharma` | Healthcare | < 1.0 | < 1.5 |
| `standard` | Everything else | < 1.5 | < 1.0 |

### Data Source Fallback Chain

```
Primary: yfinance (price + fundamentals + history)
  ↓ (if price missing for .NS/.BO)
Fallback 1: Google Finance (price only, scrapes quote page)
  ↓ (if D/E or ROE missing for .NS/.BO)
Fallback 2: Screener.in (PE, ROE, D/E for Indian tickers)
  ↓ (if PEG or sector missing — CDRs / dual-listed)
Fallback 3: US ADR peer (e.g. INFOSYS.NS → INFY on NYSE)
```

---

## Email Dispatch System

### Dispatch Flow (Every 5 Minutes)

```
EventBridge rule (rate: 5 min)
  → Lambda function (POST to Heroku with X-Dispatch-Secret)
  → Backend: load all enabled users from Supabase
  → For each user: is their preferred time in the dispatch window?
      - Early window: preferred - 5min to preferred (configurable)
      - Late window: preferred to preferred + 120min (catch-up)
  → Matched users: union all watchlist tickers, fetch once (shared cache)
  → Grade all tickers → build personalized email per user
  → Send via Resend → log in delivery_logs → stamp last_sent_at
```

### Deduplication & Safety

| Guard | How it works |
|-------|-------------|
| **Slot dedupe** | `last_sent_at` stamped at the preferred UTC instant. Same slot won't send twice. |
| **Daily cap** | Max 2 successful emails per user per local calendar day (prevents abuse via schedule edits). |
| **Shared quote cache** | Overlapping watchlists fetch each ticker only once per cron tick. |
| **Parallel dispatch** | `CRON_DISPATCH_WORKERS=8` — emails sent concurrently via ThreadPoolExecutor. |

### Email Structure

```
Subject: Stock Agent · Aug 10, 9:00 AM · 3 strong buy · 4 hold · 13 avoid · 1 no data

Sections:
  1. Intro (no AI, deterministic rules, link to methodology)
  2. Grade summary + watchlist health
  3. Buy opportunities (4–5)
  4. Watch / Hold (3)
  5. Action / Avoid (0–2)
  6. Unscored / Insufficient data
  7. How grades work (metric table with thresholds)
  8. Unsubscribe + privacy note
  9. Legal disclaimer
```

---

## Infrastructure Trade-off Decisions

### Why Heroku over Render

| Factor | Render (free) | Heroku (Student Pack) |
|--------|--------------|----------------------|
| Always-on | ❌ Sleeps after 15 min | ✅ 24/7 |
| Cold start | ~30s wake-up | None |
| Impact on dispatch | Missed send windows | Reliable delivery |
| Cost | $0 | $0 (Student Pack) |

**Decision:** For a cron-dependent email app, cold starts are unacceptable. Heroku is always on.

### Why Resend over AWS SES

| Factor | AWS SES | Resend |
|--------|---------|--------|
| Setup | Complex (domain verify + production access review) | Domain verify only, instant |
| Approval | Denied for new accounts (opaque review) | No approval needed |
| Free tier | 200 emails/day (sandbox only) | 100 emails/day (production) |
| Cost at scale | $0.10/1000 (cheapest) | $20/month for 50K |
| Code support | ✅ boto3 | ✅ HTTP API |

**Decision:** SES denied production access (new AWS account). Resend works immediately. Code supports both — switching is one env var (`AWS_SES_REGION` present = SES, absent = Resend). Will revisit SES when account matures.

### Why EventBridge + Lambda over GitHub Actions Cron

| Factor | GitHub Actions `*/5 * * * *` | EventBridge + Lambda |
|--------|------------------------------|---------------------|
| Actual interval | 30–90 min (free tier congestion) | Exactly 5 min (±1s) |
| Punctuality | 18–38 min late in practice | On time |
| Cost | Free (but unreliable) | $0 (8,640 invocations/month within free tier) |
| Reliability | Subject to GitHub outages | AWS SLA |

**Decision:** Emails were arriving 38 minutes late. EventBridge solved it immediately.

### Why Supabase for Database

- Managed PostgreSQL with instant REST API
- Row-level security, auth built-in (not currently used but available)
- Free tier: 500MB, 50K API requests
- Avoids managing a database server

---

## Where AI Is Used vs Deterministic Rules

| Component | AI or Rules? | Details |
|-----------|-------------|---------|
| Grading engine (0–5 score) | **Deterministic rules** | Same input always gives same output. Fully explainable. |
| News risk scoring | **Keyword matching** | No NLP/ML. Exact string match against tiered keyword lists. |
| Asset class detection | **Deterministic** | Sector/industry from yfinance metadata + keyword maps. |
| "Explain grades" (extension) | **AI (Gemini, optional)** | User-triggered, BYOK. Summarizes rule outputs in plain English. |
| Email reports | **No AI** | Templates + rule-based grades. |
| Ticker autocomplete | **No AI** | Static prefix match against bundled JSON catalog. |

**Key principle:** The product's core value (grading) is fully deterministic and auditable. AI is a convenience layer on top — never influences the score.

---

## Privacy Model

| Data | Stored where | Transmitted? |
|------|-------------|-------------|
| Ticker symbols | Extension + Supabase | ✅ (needed for grading + email) |
| Email + schedule | Extension + Supabase | ✅ (needed for delivery) |
| Share counts, avg buy prices | Extension only (`chrome.storage.local`) | ❌ Never |
| Gemini API key | Extension only | ❌ Never (goes to Google directly, not our servers) |
| Portfolio value, P&L | Computed client-side only | ❌ Never |

**Runtime enforcement:** `assertNoPrivateLeak()` hard-fails if any forbidden field is present in outbound payloads.

---

## Timezone Handling (Global Users)

### How It Works

1. **Extension auto-detects timezone** — `Intl.DateTimeFormat().resolvedOptions().timeZone` returns the user's OS timezone (e.g., `America/Los_Angeles`, `Asia/Kolkata`). No user input needed.
2. **Stored with schedule** — The IANA timezone string is sent to the backend with `POST /api/subscribe` and stored in Supabase alongside preferred send times.
3. **Backend converts during dispatch** — Every 5 min, the dispatch loop converts the current UTC time to each user's local timezone, then checks if their preferred time (e.g., 9:00 AM) is in the send window.

### Example

| User | OS Timezone | Preferred Time | UTC Equivalent | Dispatched When |
|------|-------------|---------------|----------------|-----------------|
| Toronto | `America/Toronto` | 9:00 AM | 13:00 UTC (EDT) | Lambda tick at 12:55–13:00 UTC |
| California | `America/Los_Angeles` | 9:00 AM | 16:00 UTC (PDT) | Lambda tick at 15:55–16:00 UTC |
| India | `Asia/Kolkata` | 9:00 AM | 03:30 UTC (IST) | Lambda tick at 03:25–03:30 UTC |

### Timezone Alias Resolution

The backend has a `_TZ_ALIASES` map that normalizes common non-IANA strings to proper zones:

```python
"toronto" → "America/Toronto"
"mumbai"  → "Asia/Kolkata"
"pacific" → "America/Los_Angeles"
"est"     → "America/New_York"
"ist"     → "Asia/Kolkata"
```

If the timezone is unrecognized, it falls back to UTC (user may get emails at wrong local time — logged as `invalid_timezone` in dispatch stats).

### Edge Case

If a user travels from Toronto to India but doesn't change their laptop timezone, they'll still receive emails on Toronto time. This is standard behavior for all scheduling apps — the timezone is determined by the device OS setting at the time of subscription.

---

## Key Design Principles

1. **Privacy-first** — Only tickers leave the browser. Holdings and API keys never touch our servers.
2. **Transparency** — Every grade can be explained by exact metric thresholds. No black-box predictions.
3. **Resilience** — Multi-layer data fallbacks (yfinance → Google Finance → Screener.in → US ADR peer).
4. **Determinism** — Same ticker + same market data = same grade, every time.
5. **Progressive UX** — Cached grades show instantly; live data streams in as it arrives.
6. **Abuse prevention** — Rate limits, daily caps, slot dedupe, body size checks.
7. **Switching cost = zero** — Email provider, hosting, cron service all swappable via env vars alone.
