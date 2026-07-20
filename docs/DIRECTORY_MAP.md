# Stock Agent — Directory Map

```
Stock_Agent/
├── legacy/
│   └── stock_agent.py          # Original Task Scheduler monolith (reference)
├── extension/                  # Chrome Extension (Manifest V3)
│   ├── manifest.json           # Minimized permissions
│   ├── background.js           # Service worker: privacy gate + message router
│   ├── icons/
│   ├── lib/
│   │   ├── storage.js          # Two-tier storage contract
│   │   └── api.js              # Cloud HTTPS client (delivery data only)
│   └── popup/
│       ├── popup.html          # Mini-dashboard shell + privacy disclaimer
│       ├── popup.css
│       └── popup.js            # UI controller (local vs cloud paths)
├── backend/                    # FastAPI + cron worker
│   ├── requirements.txt
│   ├── database_schema.sql     # Supabase users table (privacy-safe)
│   ├── .env.example
│   ├── app/
│   │   ├── main.py             # FastAPI app
│   │   ├── config.py
│   │   ├── api/routes.py       # POST /api/subscribe, /api/quotes/snapshot
│   │   ├── models/schemas.py   # Pydantic (extra=forbid)
│   │   └── services/supabase_client.py
│   └── worker/
│       └── cron_dispatch.py    # 15-min GitHub Actions poller
├── .github/workflows/
│   └── cron-dispatch.yml       # cron: 4,19,34,49 * * * *
└── docs/
    └── DIRECTORY_MAP.md
```

## Data boundary

| Data | Storage | Network |
|------|---------|---------|
| Shares, buy prices | `chrome.storage.local` | Never |
| Gemini API key (BYOK) | `chrome.storage.local` | Client → Google only |
| Email, watchlist, schedule | Local cache + Supabase | Extension → FastAPI |
