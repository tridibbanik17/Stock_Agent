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
| **Total** | ~11,709 | Rebuild periodically; IPOs/delists go stale |

## Focus

Primary user coverage: **NASDAQ + NYSE + TSX**. AMEX, TSXV, and NEO are kept as extras.

## Rebuild note

Regenerate from public US symbol dumps + TSX/TSXV company-directory search when the list needs refreshing. Example CDRs included: `NVDA.TO`, `NVDA.NE`.
