# StockAgent Privacy Policy

**Last updated:** August 16, 2026

## What we collect

StockAgent stores the minimum data needed to deliver scheduled email reports:

- **Email address** — to send your reports
- **Ticker symbols** (watchlist) — to grade and include in reports
- **Delivery schedule** (days, times, timezone) — to send at the right time

This data is stored in a Supabase PostgreSQL database operated by the developer.

## What we NEVER collect

StockAgent does NOT collect, transmit, or store:

- Number of shares owned
- Buy prices or portfolio values
- Gemini API keys (sent directly from your browser to Google, never our servers)
- Browsing history
- Personal information beyond your email address

## Local-only data

Private portfolio data (shares, average buy prices) is stored exclusively in `chrome.storage.local` on your device. This data is never transmitted to any server under any circumstance.

## Optional AI feature

If you choose to use the "Explain grades" feature, your Gemini API key is sent directly from your browser to Google's Generative Language API. The key is stored only in your browser's local storage and never passes through our servers.

## Data deletion

You can delete your data at any time by:

1. Clicking "Manage data → Reset everything" in the extension, or
2. Clicking the unsubscribe link in any email (removes your delivery subscription)

## Third-party services

- **Supabase** (database) — stores email, tickers, schedule
- **Resend** (email delivery) — receives your email address to send reports
- **Google Gemini** (optional, user-initiated) — receives your API key directly from the browser

## Contact

For questions or data deletion requests: tbanik@magentacapital.ca

## Changes

We may update this policy. Changes will be reflected in the "Last updated" date above.
