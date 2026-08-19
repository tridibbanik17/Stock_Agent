# StockAgent Privacy Policy

**Last updated:** August 18, 2026

StockAgent is a Chrome extension that grades stocks using rule-based scoring and delivers scheduled email reports. This policy explains what data we collect, how we use, store, and share it.

---

## 1. Data We Collect

When you enable scheduled email reports, we collect and store the following on our servers:

| Data | Purpose |
|------|---------|
| Email address | Send your stock reports |
| Ticker symbols (watchlist, max 25) | Grade and include in reports |
| Schedule preferences (frequency, days, times, timezone) | Deliver reports at your chosen times |
| Enabled/disabled status | Respect your delivery preference |

We also maintain delivery logs that record: your email address, send status (success/failure), email subject line, number of tickers, and timestamps. These logs are used for debugging delivery issues and are never shared externally.

---

## 2. Data We NEVER Collect

StockAgent does **not** collect, transmit, or store on our servers:

- Number of shares you own
- Buy prices or portfolio values
- Gemini API keys
- Browsing history or activity outside the extension
- Cookies or tracking identifiers
- Any personal information beyond your email address

These guarantees are enforced at multiple layers in our code — the extension blocks private fields from leaving your browser, and the server rejects them if they somehow arrive.

---

## 3. Data Stored Locally on Your Device

The following data is stored exclusively in Chrome's local storage (`chrome.storage.local`) on your device and is **never transmitted** to any server:

- Portfolio holdings (share counts, buy prices)
- Gemini API key
- Auto-analyze preference
- Cached price snapshots and AI explanations

You can clear all local data at any time via the extension's "Reset everything" option.

---

## 4. How We Use Your Data

We use your data solely to:

- Generate and deliver scheduled stock grade reports to your email
- Determine when to send reports based on your schedule preferences
- Prevent duplicate sends within the same time window
- Enforce rate limits to protect service quality

We do **not** use your data for advertising, profiling, analytics, or any purpose unrelated to delivering your reports.

---

## 5. How We Store Your Data

- **Server-side data** is stored in a Supabase-hosted PostgreSQL database with encryption at rest and TLS-encrypted connections.
- **Access** to the database is restricted to the application service using secret credentials that are never exposed to end users.
- **Sensitive tokens** (unsubscribe, manage, recover) are cryptographically random UUIDs that cannot be guessed from your email address.
- **Local data** is stored in Chrome's built-in `chrome.storage.local`, which is sandboxed to the extension and inaccessible to other extensions or websites.

---

## 6. How We Share Your Data

We share the minimum data necessary with the following third-party services to operate StockAgent:

| Service | Data shared | Purpose |
|---------|-------------|---------|
| **Supabase** (database hosting) | Email, tickers, schedule | Persistent storage for delivery profiles |
| **Resend** (email provider) | Email address, report content (tickers, grades, public market metrics) | Delivering your email reports |
| **Google Gemini** (optional, user-initiated) | Your API key + ticker data with public metrics | AI-powered grade explanations (sent directly from your browser to Google — never through our servers) |
| **Yahoo Finance** (market data) | Ticker symbols only | Fetching public stock prices and news (no user PII is sent) |

We do **not** sell, rent, or disclose your personal data to any other third parties. We do not share data with advertisers or data brokers.

---

## 7. Data Retention

- **Delivery profiles** are retained as long as your subscription is active. Unsubscribing sets your profile to disabled but retains the record so you can re-subscribe.
- **Delivery logs** (send audit records) are retained for operational debugging and may be purged periodically.
- **Local data** persists until you uninstall the extension or manually reset it.

---

## 8. Data Deletion

You can delete your data at any time:

1. **Local data:** Click "Manage data → Reset everything" in the extension to wipe all locally stored data.
2. **Server data:** Click the unsubscribe link in any email report to disable your subscription. To request complete deletion of your server-side data, contact us at the email below.

---

## 9. Children's Privacy

StockAgent is not directed at children under 13. We do not knowingly collect data from children.

---

## 10. Changes to This Policy

We may update this policy from time to time. Changes will be reflected in the "Last updated" date at the top. Continued use of the extension after changes constitutes acceptance of the updated policy.

---

## 11. Contact

For privacy questions, data access requests, or deletion requests:

**Email:** tridib.perfect@gmail.com

---

## 12. Permissions Used

The extension requests only these Chrome permissions:

- `storage` — to save your local settings and portfolio data on-device
- `sidePanel` — to display the extension interface

Host permissions are limited to our API server (`api.stockagent.app`) and Google's Generative Language API (for optional AI features, called directly from your browser).
