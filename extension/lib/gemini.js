/**
 * Local Gemini helpers (browser → Google only; never Stock Agent servers).
 */

export const GEMINI_MODELS = Object.freeze([
  "gemini-2.0-flash",
  "gemini-flash-latest",
  "gemini-2.0-flash-lite",
]);

/**
 * @param {unknown} body
 * @returns {string}
 */
function extractText(body) {
  const parts = body?.candidates?.[0]?.content?.parts;
  if (!Array.isArray(parts)) return "";
  return parts
    .map((/** @type {{ text?: string }} */ part) => part.text || "")
    .join("")
    .trim();
}

/**
 * @param {string} key
 * @param {string} prompt
 * @param {{ maxOutputTokens?: number, temperature?: number }} [config]
 * @returns {Promise<{ model: string, text: string }>}
 */
export async function generateGeminiText(key, prompt, config = {}) {
  const maxOutputTokens = config.maxOutputTokens ?? 120;
  const temperature = config.temperature ?? 0.3;
  let lastError = /** @type {Error|null} */ (null);

  for (const model of GEMINI_MODELS) {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: prompt }] }],
          generationConfig: { maxOutputTokens, temperature },
        }),
      });

      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail =
          body?.error?.message || `HTTP ${response.status} from Gemini`;
        const err = new Error(detail);
        // @ts-expect-error status for callers
        err.status = response.status;
        if (response.status === 404 || response.status === 429) {
          lastError = err;
          continue;
        }
        throw err;
      }

      return { model, text: extractText(body) };
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      const status = /** @type {{ status?: number }} */ (lastError).status;
      if (status === 404 || status === 429) continue;
      throw lastError;
    }
  }

  throw lastError || new Error("Gemini request failed");
}

/**
 * Compact snapshot for the explain prompt (no holdings / buy prices).
 * @param {Record<string, unknown>} quote
 * @returns {string}
 */
export function buildGradeExplainPrompt(quote) {
  const ticker = String(quote.ticker || "?");
  const verdict = String(quote.verdict || quote.grade || "n/a");
  const notes = Array.isArray(quote.notes)
    ? quote.notes.map((n) => String(n)).slice(0, 6)
    : [];
  const risks = Array.isArray(quote.newsRisks)
    ? quote.newsRisks
        .map((item) =>
          typeof item === "string"
            ? item
            : String(item?.title || item?.headline || "")
        )
        .filter(Boolean)
        .slice(0, 4)
    : [];

  const metrics = {
    price: quote.price ?? null,
    currency: quote.currency || null,
    deRatio: quote.deRatio ?? null,
    pegRatio: quote.pegRatio ?? null,
    rsi: quote.rsi ?? null,
    aboveSma200: quote.aboveSma200 ?? null,
    sma200: quote.sma200 ?? null,
    assetClass: quote.assetClass || null,
    score: quote.score ?? null,
  };

  return [
    "You are a concise equity desk assistant for retail swing traders.",
    "Explain in 1-2 short sentences why this stock received its rule-based grade.",
    "Use only the data given. Do not invent prices, news, or fundamentals.",
    "Do not give personalized financial advice or say buy/sell as a command.",
    "No markdown, no bullet list — plain prose only.",
    "",
    `Ticker: ${ticker}`,
    `Grade: ${verdict}`,
    `Metrics JSON: ${JSON.stringify(metrics)}`,
    `Rule notes: ${notes.length ? notes.join(" | ") : "(none)"}`,
    `Headlines: ${risks.length ? risks.join(" | ") : "(none)"}`,
  ].join("\n");
}

/**
 * Build one Gemini prompt for the whole watchlist (single free-tier request).
 * Expect JSON: { "TICKER": "one sentence", ... }
 * @param {Array<Record<string, unknown>>} quotes
 * @returns {string}
 */
export function buildBatchGradeExplainPrompt(quotes) {
  const rows = (quotes || []).map((quote) => {
    const notes = Array.isArray(quote.notes)
      ? quote.notes.map((n) => String(n)).slice(0, 4)
      : [];
    const risks = Array.isArray(quote.newsRisks)
      ? quote.newsRisks
          .map((item) =>
            typeof item === "string"
              ? item
              : String(item?.title || item?.headline || "")
          )
          .filter(Boolean)
          .slice(0, 3)
      : [];
    return {
      ticker: String(quote.ticker || "?"),
      verdict: String(quote.verdict || quote.grade || "n/a"),
      price: quote.price ?? null,
      currency: quote.currency || null,
      deRatio: quote.deRatio ?? null,
      pegRatio: quote.pegRatio ?? null,
      rsi: quote.rsi ?? null,
      aboveSma200: quote.aboveSma200 ?? null,
      assetClass: quote.assetClass || null,
      notes,
      headlines: risks,
    };
  });

  return [
    "You are a concise equity desk assistant for retail swing traders.",
    "For EACH ticker, write one short sentence explaining the rule-based grade.",
    "Use only the data given. Do not invent facts.",
    "Do not give personalized financial advice.",
    'Respond with ONLY a JSON object mapping ticker -> explanation string.',
    'Example: {"AMZN":"HOLD because …","TSLA":"AVOID because …"}',
    "",
    `Watchlist JSON: ${JSON.stringify(rows)}`,
  ].join("\n");
}

/**
 * @param {string} key
 * @param {Array<Record<string, unknown>>} quotes
 * @returns {Promise<Record<string, string>>}
 */
export async function explainQuotesBatch(key, quotes) {
  const { text } = await generateGeminiText(
    key,
    buildBatchGradeExplainPrompt(quotes),
    { maxOutputTokens: 500, temperature: 0.2 }
  );
  const cleaned = String(text || "").trim();
  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  if (start < 0 || end <= start) {
    throw new Error("Gemini did not return JSON explanations");
  }
  const parsed = JSON.parse(cleaned.slice(start, end + 1));
  /** @type {Record<string, string>} */
  const out = {};
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Invalid Gemini JSON");
  }
  for (const [rawTicker, value] of Object.entries(parsed)) {
    const ticker = String(rawTicker || "").trim().toUpperCase();
    const blurb = String(value || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!ticker || !blurb) continue;
    out[ticker] = blurb.length > 280 ? `${blurb.slice(0, 277)}…` : blurb;
  }
  if (!Object.keys(out).length) {
    throw new Error("Empty Gemini explanations");
  }
  return out;
}

/**
 * @param {string} key
 * @param {Record<string, unknown>} quote
 * @returns {Promise<string>}
 */
export async function explainQuoteGrade(key, quote) {
  const map = await explainQuotesBatch(key, [quote]);
  const ticker = String(quote.ticker || "").toUpperCase();
  const text = map[ticker];
  if (!text) throw new Error("Empty Gemini explanation");
  return text;
}
