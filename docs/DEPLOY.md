# Hosted API (production FastAPI)

The Chrome extension talks to FastAPI for quotes, subscribe, and unsubscribe.
Cron (GitHub Actions) talks to Supabase + Resend directly; it needs `PUBLIC_API_BASE_URL`
so email unsubscribe links point at the **same** public API.

## Option A — Render (recommended free tier)

1. Push this repo to GitHub (if it isn’t already).
2. Go to [https://render.com](https://render.com) → **New** → **Blueprint**.
3. Connect the repo. Render reads root `render.yaml`.
4. Fill env vars when prompted (same values as `backend/.env`):

| Env var | Notes |
|---------|--------|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SECRET_KEY` | Server / service_role key |
| `RESEND_API_KEY` | Optional; dry-run without it |
| `REPORT_FROM_EMAIL` | Verified Resend from-address |
| `PUBLIC_API_BASE_URL` | Set **after** first deploy to your `https://….onrender.com` URL |
| `DISPATCH_SECRET` | Shared secret for `POST /api/internal/dispatch-due` |
| `TRUST_PROXY` | Already `true` in the blueprint |

5. Deploy. Open `https://YOUR-SERVICE.onrender.com/health` — expect `{"status":"ok",…}`.
6. Wire the extension:
   - Edit `extension/lib/config.js`
   - Set `PROD_API_BASE` to that HTTPS URL
   - Set `USE_LOCAL_API = false`
   - Reload the unpacked extension at `chrome://extensions`
7. GitHub Actions → secrets:
   - `PUBLIC_API_BASE_URL` = same HTTPS URL (no trailing slash)
   - `DISPATCH_SECRET` = same value as Render `DISPATCH_SECRET`
   The workflow pings `POST /api/internal/dispatch-due` every 5 minutes (hybrid B).

**Free plan note:** Render spins down idle free services. The first request after sleep can take ~30–60s; the extension error message mentions this.

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
- [ ] Render env: `DISPATCH_SECRET`, Supabase, Resend, `PUBLIC_API_BASE_URL`
- [ ] Actions secrets: `PUBLIC_API_BASE_URL`, `DISPATCH_SECRET` (same secret as Render)
- [ ] Manual **Scheduled report dispatch** run succeeds (idle `matched: 0` is OK)
- [ ] Supabase migrations `004_add_delivery_logs.sql` and `006_add_daily_send_cap.sql` applied
