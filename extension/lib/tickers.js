/**
 * Static ticker catalog (US + major TSX) for Add Ticker autocomplete.
 * Data: extension/data/tickers.json (sorted A–Z).
 */

/** @typedef {{ symbol: string, name: string, exchange: string }} TickerRow */

/** @type {TickerRow[] | null} */
let catalog = null;

/** @type {Promise<TickerRow[]> | null} */
let loadPromise = null;

const MAX_SUGGESTIONS = 12;

export async function loadTickerCatalog() {
  if (catalog) return catalog;
  if (loadPromise) return loadPromise;

  loadPromise = (async () => {
    const url = chrome.runtime.getURL("data/tickers.json");
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Could not load tickers.json (${response.status})`);
    }
    const data = await response.json();
    catalog = Array.isArray(data) ? data : [];
    return catalog;
  })();

  try {
    return await loadPromise;
  } catch (error) {
    loadPromise = null;
    throw error;
  }
}

/**
 * Prefix match, already alphabetical from sorted catalog.
 * @param {string} query
 * @param {number} [limit]
 * @returns {Promise<TickerRow[]>}
 */
export async function suggestTickers(query, limit = MAX_SUGGESTIONS) {
  const q = String(query || "").trim().toUpperCase();
  if (!q) return [];

  const rows = await loadTickerCatalog();
  const out = [];
  for (const row of rows) {
    if (!row?.symbol) continue;
    if (row.symbol.startsWith(q)) {
      out.push(row);
      if (out.length >= limit) break;
    }
  }
  return out;
}
