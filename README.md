# Stock Agent

**Privacy-first Chrome extension that grades your watchlist with deterministic rules — no AI, no black box.** Keep holdings on-device, see live STRONG BUY / HOLD / AVOID grades, and get scheduled email digests. The cloud stores only your email, tickers, and schedule.

[![Chrome MV3](https://img.shields.io/badge/Chrome-Manifest_V3-4285F4?logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions/mv3/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)

---

## The Problem

Retail traders get flooded with noisy stock tips, but sharing a portfolio with a random AI SaaS feels unsafe. Most tools want your holdings, your broker login, or opaque "AI picks" you can't verify.

## The Solution

Stock Agent delivers **research-grade watchlist grades without uploading holdings or API keys**:

- **Privacy-first architecture** — share counts and buy prices never leave `chrome.storage.local`
- **Transparent grading** — every score is fully explainable from 5 financial metrics (D/E, PEG, ROE, 200-SMA, RSI) + news risk flags
- **No AI in the scoring loop** — deterministic rules mean same data = same grade, every time
- **Scheduled email digests** — automated reports on your days/times with one-click unsubscribe
- **Multi-region** — supports US (NASDAQ/NYSE), Canada (TSX), and India (NSE/BSE) with sector-aware thresholds

## Features

| Feature | Details |
|---------|---------|
| Watchlist dashboard | Up to 25 tickers with live prices and grades in a Chrome popup |
| Private portfolio tracking | Shares + avg buy stored locally, P&L calculated on-device |
| Context-aware grading | Sector-specific thresholds (banks ≠ tech ≠ crypto proxies) |
| News risk scoring | Tiered severity (fraud = -2, lawsuit = -1, guidance cut = informational) |
| Email scheduler | Pick days + times, auto-detect timezone, daily cap prevents abuse |
| Optional AI explain | BYOK Gemini key explains grades in plain English (browser → Google only) |
| Light/dark theme | Toggle with smooth transitions |

## Privacy Boundary

| Data | Where it lives | Leaves device? |
|------|----------------|:---:|
| Shares, buy prices | Extension local storage | ❌ Never |
| Gemini API key | Extension local storage | ❌ (goes to Google directly) |
| Email, watchlist, schedule | Local cache + Supabase | ✅ Required for delivery |

## How Grades Work (No AI)

Each stock starts at 0 and earns up to 5 points:

| Factor | +1 if | −1 if |
|--------|-------|-------|
| **D/E** (Debt-to-Equity) | Below sector threshold | — |
| **PEG** (Price/Earnings-to-Growth) | Below threshold (1.0 standard, 1.5 tech) | — |
| **ROE** (Return on Equity) | Above 15% (or 8–12% for utilities/banks) | — |
| **200-SMA** (Simple Moving Average) | Price above 200-day SMA | — |
| **RSI** (Relative Strength Index) | Below 35 (oversold) | ≥ 70 (overbought) |
| **News risk** | — | Severe: −2, Moderate: −1 |

**4–5 = STRONG BUY · 3 = HOLD · 0–2 = AVOID**

Thresholds adjust by sector (banks aren't penalized for structural leverage, ETFs skip corporate ratios, cyclicals have lenient ROE during commodity troughs).

Full methodology: [docs/GRADING_ENGINE.md](docs/GRADING_ENGINE.md)

## Stack

| Layer | Tech |
|-------|------|
| Extension | Manifest V3, HTML / CSS / JavaScript |
| API | Python, FastAPI, Pydantic |
| Database | Supabase (PostgreSQL), RLS recommended |
| Market data | yfinance |
| Email | Resend (dry-run if no API key) |
| Cron | AWS EventBridge + Lambda (every 5 minutes) → hosted `/api/internal/dispatch-due` |
| Hosted API | Heroku (Docker) — see [docs/DEPLOY.md](docs/DEPLOY.md) |

## Data flow and field reference

How quotes, grades, and email fields are produced (yfinance vs our grader vs Supabase): see **[docs/DATA_FLOW.md](docs/DATA_FLOW.md)**.

Ticker autocomplete coverage (NASDAQ / NYSE / TSX counts): see **[docs/TICKER_CATALOG.md](docs/TICKER_CATALOG.md)**.

## Repository layout

```
extension/          Chrome extension (popup + storage + API client)
backend/            FastAPI app, services, cron worker
.github/workflows/  CI tests + extension version bump
docs/               Extra docs (data flow, deploy, ticker catalog)
scripts/            Optional local version bump (PowerShell)
```

## Quick start

### 1. Backend

```powershell
cd backend
copy .env.example .env
# Fill SUPABASE_URL, SUPABASE_SECRET_KEY, optional RESEND_* keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs: http://127.0.0.1:8000/docs

Apply schema once in the Supabase SQL Editor using `backend/database_schema.sql`, then enable RLS and lock grants for `anon` / `authenticated` (service role only for the API).

Existing databases: also run migrations `001`–`006` under `backend/migrations/` (`last_sent_at`, `unsubscribe_token`, legacy `manage_token` columns unused by the app, `delivery_logs`, daily send cap).

### 2. Chrome extension

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → select the `extension/` folder
4. Open the popup, add tickers, click **Refresh** for live grades
5. Set email + schedule, click **Save & Subscribe** (backend must be running)

Default API base is local (`extension/lib/config.js` → `USE_LOCAL_API = true`).  
For real users, host FastAPI and flip to production — see **[docs/DEPLOY.md](docs/DEPLOY.md)**.

### 3. Local cron (optional)

```powershell
# from repo root, with backend/.env loaded
python backend/worker/cron_dispatch.py
```

## Cron dispatch

Email dispatch is handled by **AWS EventBridge + Lambda** (every 5 minutes, punctual). The Lambda `POST`s `/api/internal/dispatch-due` with `X-Dispatch-Secret`.

Required env vars on the Lambda / Heroku:

| Variable | Purpose |
|----------|---------|
| `PUBLIC_API_BASE_URL` | Heroku HTTPS origin, e.g. `https://stock-agent-api-2aee861fcc19.herokuapp.com` (no trailing slash) |
| `DISPATCH_SECRET` | Shared secret between Lambda and Heroku |

Supabase / Resend stay on **Heroku** only (the API does the real send).

The GitHub Actions workflow (`.github/workflows/cron-dispatch.yml`) is **disabled** — kept for reference / fallback only.

## Extension versioning

Pushes to `main` that change `extension/**` auto-bump the patch version via GitHub Actions.

For a feature or store release: **Actions → Bump extension version → Run workflow** → choose `minor` or `major`.

Always `git pull` before new work so you pick up the bot bump commit.

## API (high level)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/subscribe` | Upsert email / tickers / schedule (keyed by email) |
| `POST /api/internal/dispatch-due` | Send due/overdue reports (header `X-Dispatch-Secret`) |
| `GET /api/unsubscribe?token=…` | One-click unsubscribe (email link; sets `enabled=false`) |
| `POST /api/unsubscribe` | Same via JSON `{ "token" }` or `?token=` |
| `DELETE /api/unsubscribe/{token}` | Same via path token |
| `POST /api/quotes/snapshot` | Live metrics + grades for a watchlist |

Both subscribe and snapshot accept tickers only. No holdings. No Gemini keys.

Public routes are rate-limited per client IP (defaults: subscribe 10/min, snapshot 30/min, unsubscribe 30/min). Oversized bodies and non-JSON POST bodies are rejected. Tunables: `RATE_LIMIT_*`, `MAX_REQUEST_BODY_BYTES`, `TRUST_PROXY` in `backend/.env`.

Set `PUBLIC_API_BASE_URL` in `backend/.env` (and GitHub Actions if needed) so report emails include a working unsubscribe link.

## Collaborators

1. Get invited to the GitHub repo (and optionally Supabase / Resend teams)
2. Clone the repo
3. Copy `backend/.env.example` → `backend/.env` (get real values privately)
4. Run backend + load the extension as above

Never commit `.env`. Never put secret keys in the extension.

## License

Private / unpublished unless you add a license file.
