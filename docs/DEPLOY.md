# Hosted API (production FastAPI)

The Chrome extension talks to FastAPI for quotes, subscribe, and unsubscribe.
Cron (GitHub Actions) talks to Supabase + Resend directly; it needs `PUBLIC_API_BASE_URL`
so email unsubscribe links point at the **same** public API.

## Option A — Heroku (recommended)

1. Push this repo to GitHub (if it isn't already).
2. Create a new Heroku app (Docker deploy via `heroku.yml` or Container Registry).
3. Set env vars (same values as `backend/.env`):

| Env var | Notes |
|---------|--------|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SECRET_KEY` | Server / service_role key |
| `RESEND_API_KEY` | Optional; dry-run without it |
| `REPORT_FROM_EMAIL` | Verified Resend from-address |
| `PUBLIC_API_BASE_URL` | Your Heroku HTTPS URL, e.g. `https://stock-agent-api-2aee861fcc19.herokuapp.com` |
| `DISPATCH_SECRET` | Shared secret for `POST /api/internal/dispatch-due` |
| `TRUST_PROXY` | `true` |

4. Deploy. Open `https://YOUR-APP.herokuapp.com/health` — expect `{"status":"ok",…}`.
5. Wire the extension:
   - Edit `extension/lib/config.js`
   - Set `PROD_API_BASE` to that HTTPS URL
   - Set `USE_LOCAL_API = false`
   - Reload the unpacked extension at `chrome://extensions`
6. GitHub Actions → secrets:
   - `PUBLIC_API_BASE_URL` = same HTTPS URL (no trailing slash)
   - `DISPATCH_SECRET` = same value as Heroku env `DISPATCH_SECRET`
   The workflow pings `POST /api/internal/dispatch-due` every 5 minutes (hybrid B).

## Option B — Docker locally (smoke-test the image)

```powershell
cd backend
docker build -t stock-agent-api .
docker run --rm -p 8000:8000 --env-file .env stock-agent-api
```

## Option C — Other hosts (Fly, Railway, …)

Use `backend/Dockerfile`. Start command equivalent:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set the same env vars as above, with `TRUST_PROXY=true` and `PUBLIC_API_BASE_URL` to your public HTTPS origin.

## Checklist after go-live

- [ ] `/health` returns ok over HTTPS
- [ ] Extension Refresh loads live quotes (not localhost)
- [ ] Save & Subscribe succeeds
- [ ] Unsubscribe links in email use the public host
- [ ] Heroku env: `DISPATCH_SECRET`, Supabase, Resend, `PUBLIC_API_BASE_URL`
- [ ] Actions secrets: `PUBLIC_API_BASE_URL`, `DISPATCH_SECRET` (same secret as Heroku)
- [ ] Manual **Scheduled report dispatch** run succeeds (idle `matched: 0` is OK)
- [ ] Supabase migrations `004_add_delivery_logs.sql` and `006_add_daily_send_cap.sql` applied
