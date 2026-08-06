# Ticker autocomplete catalog

Static suggestion list used by the Chrome extension **Add Ticker** dropdown  
(`extension/data/tickers.json`). Prefix match, alphabetical, capped in the UI.

Manual entry still works for any valid symbol even if it is missing here.

## Current coverage (best effort)

| Exchange | Count | Notes |
|----------|------:|-------|
| **NASDAQ** | ~4,119 | Full public dump |
| **NYSE** | ~2,353 | Full public dump |
| **TSX** | ~3,235 | Pulled from TSX company directory |
| **TSXV** | ~1,703 | Venture listings kept |
| **AMEX** | ~285 | Kept with US dumps |
| **NEO** | ~14 | CDRs (e.g. `NVDA.NE`) |
| **NSE** | ~2,070 | NSE equity board (`RELIANCE.NS`, `TCS.NS` …) |
| **BSE** | ~2,315 | BSE main board A/B groups (`RELIANCE.BO`, `ABB.BO` …) |
| **Total** | ~16,094 | Rebuild periodically; IPOs/delists go stale |

## Focus

Primary user coverage: **NASDAQ + NYSE + TSX + NSE + BSE**. AMEX, TSXV, and NEO are kept as extras.

## Indian exchange notes

- NSE tickers use the `.NS` Yahoo Finance suffix (e.g. `RELIANCE.NS`).
- BSE tickers use the `.BO` suffix with the company's alpha scrip ID (e.g. `RELIANCE.BO`).  
  For a small number of BSE-only listings without an alpha ID, the numeric BSE code is used (e.g. `500325.BO`).
- Only main-board A/B group BSE scrips are included; SME, suspended, and odd-lot boards are excluded.
- Currency for `.NS` and `.BO` tickers is automatically resolved to `INR`.
- Timezone `Asia/Kolkata` (IST, UTC+5:30) is supported — use `Asia/Kolkata` in the extension scheduler.

## Rebuild note

Regenerate from public US symbol dumps + TSX/TSXV company-directory search when the list needs refreshing. Example CDRs included: `NVDA.TO`, `NVDA.NE`.
