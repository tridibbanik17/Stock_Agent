# Stock Agent

Privacy-first Chrome extension for retail traders: keep a watchlist, see live grades, and get scheduled email reports. Holdings and API keys stay on your device. The cloud stores delivery preferences only.

## What it does

- Watchlist dashboard in a Chrome popup (up to 25 tickers)
- Private lots (shares, average buy) stored only in `chrome.storage.local`
- Live quotes and grades via yfinance (`STRONG BUY` / `HOLD` / `AVOID`)
- Optional Gemini BYOK for AI checks (key never uploaded to our servers)
- Email digests on your schedule (days, times, timezone)
- Cloud sync for email + tickers + schedule only (Supabase)

## Privacy boundary

| Data | Where it lives | Network |
|------|----------------|---------|
| Shares, buy prices | Extension local storage | Never leaves the device |
| Gemini API key | Extension local storage | Client → Google only |
| Email, watchlist, schedule | Local cache + Supabase | Extension → FastAPI → Supabase |

## Stack

| Layer | Tech |
|-------|------|
| Extension | Manifest V3, HTML / CSS / JavaScript |
| API | Python, FastAPI, Pydantic |
| Database | Supabase (PostgreSQL), RLS recommended |
| Market data | yfinance |
| Email | Resend (dry-run if no API key) |
| Cron | GitHub Actions every ~15 minutes |
| Hosted API | Render (Docker) — see [docs/DEPLOY.md](docs/DEPLOY.md) |

## Data flow and field reference

How quotes, grades, and email fields are produced (yfinance vs our grader vs Supabase): see **[docs/DATA_FLOW.md](docs/DATA_FLOW.md)**.

Ticker autocomplete coverage (NASDAQ / NYSE / TSX counts): see **[docs/TICKER_CATALOG.md](docs/TICKER_CATALOG.md)**.

## Repository layout

```
extension/          Chrome extension (popup + storage + API client)
backend/            FastAPI app, services, cron worker
.github/workflows/  Scheduled reports + extension version bump
docs/               Extra docs (data flow, deploy, ticker catalog)
scripts/            Optional local version bump (PowerShell)
render.yaml         Render Blueprint for hosted FastAPI
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

Existing databases: also run migrations `001`–`004` under `backend/migrations/` (`last_sent_at`, `unsubscribe_token`, legacy `manage_token` columns unused by the app, `delivery_logs`).

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

## GitHub Actions secrets

For scheduled email dispatch, add under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|--------|---------|
| `SUPABASE_URL` | e.g. `https://YOUR_REF.supabase.co` |
| `SUPABASE_SECRET_KEY` | Supabase secret / service_role key |
| `RESEND_API_KEY` | Resend sending key (`re_…`) |
| `REPORT_FROM_EMAIL` | e.g. `Stock Agent <onboarding@resend.dev>` |
| `PUBLIC_API_BASE_URL` | Public API origin for email unsubscribe links (optional locally) |

Workflow: `.github/workflows/cron-dispatch.yml`  
Manual run: **Actions → Scheduled report dispatch → Run workflow**

## Extension versioning

Pushes to `main` that change `extension/**` auto-bump the patch version via GitHub Actions.

For a feature or store release: **Actions → Bump extension version → Run workflow** → choose `minor` or `major`.

Always `git pull` before new work so you pick up the bot bump commit.

## API (high level)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/subscribe` | Upsert email / tickers / schedule (keyed by email) |
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
