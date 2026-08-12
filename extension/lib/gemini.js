/**
 * Local Gemini helpers (browser → Google only; never Stock Agent servers).
 */

export const GEMINI_MODELS = Object.freeze([
  // Prefer free-tier-friendly Flash / Flash-Lite (try newest first; 404 → next).
  "gemini-3.1-flash-lite",
  "gemini-2.5-flash-lite",
  "gemini-2.5-flash",
  "gemini-flash-latest",
  "gemini-2.0-flash-lite",
]);

/** Max tickers per Gemini request (keeps free models from skipping rows). */
export const EXPLAIN_CHUNK_SIZE = 2;

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
 * @param {{
 *   maxOutputTokens?: number,
 *   temperature?: number,
 *   responseMimeType?: string,
 * }} [config]
 * @returns {Promise<{ model: string, text: string }>}
 */
export async function generateGeminiText(key, prompt, config = {}) {
  const maxOutputTokens = config.maxOutputTokens ?? 120;
  const temperature = config.temperature ?? 0.3;
  let lastError = /** @type {Error|null} */ (null);

  for (const model of GEMINI_MODELS) {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`;
    try {
      /** @type {Record<string, unknown>} */
      const generationConfig = { maxOutputTokens, temperature };
      if (config.responseMimeType) {
        generationConfig.responseMimeType = config.responseMimeType;
      }

      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: prompt }] }],
          generationConfig,
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
 * @param {Record<string, unknown>} quote
 * @returns {Record<string, unknown>}
 */
function compactQuote(quote) {
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
    ticker: String(quote.ticker || "?").toUpperCase(),
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
}

/**
 * Line format is truncation-safe: TICKER|one sentence
 * @param {Array<Record<string, unknown>>} quotes
 * @returns {string}
 */
export function buildBatchGradeExplainPrompt(quotes) {
  const rows = (quotes || []).map((q) => compactQuote(q));
  const tickers = rows.map((r) => r.ticker).join(", ");

  return [
    "Explain each stock's rule-based grade in one short sentence (max 22 words).",
    "Use only the data given. Do not invent facts. No financial advice.",
    "Output format (strict): one line per ticker as TICKER|sentence",
    "You MUST include every ticker listed below — no skipping.",
    "No markdown, no bullets, no extra commentary.",
    `Example:\nAMZN|HOLD because leverage is fine but momentum is mixed.\nTSLA|AVOID because PEG is stretched and price is below the trend SMA.`,
    `Tickers to cover (all required): ${tickers}`,
    "",
    `Data: ${JSON.stringify(rows)}`,
  ].join("\n");
}

/**
 * @param {string} raw
 * @param {string[]} expectedTickers
 * @returns {Record<string, string>}
 */
export function parseGradeExplanations(raw, expectedTickers = []) {
  const text = String(raw || "").trim();
  /** @type {Record<string, string>} */
  const out = {};
  const expected = new Set(
    expectedTickers.map((t) => String(t || "").trim().toUpperCase()).filter(Boolean)
  );

  const push = (ticker, blurb) => {
    const key = String(ticker || "").trim().toUpperCase();
    const value = String(blurb || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!key || !value) return;
    if (expected.size && !expected.has(key)) return;
    out[key] = value.length > 280 ? `${value.slice(0, 277)}…` : value;
  };

  // 1) Preferred: TICKER|sentence lines
  for (const line of text.split(/\r?\n/)) {
    const cleaned = line
      .replace(/^[-*•\d.)\s]+/, "")
      .replace(/^`+|`+$/g, "")
      .trim();
    const pipe = cleaned.indexOf("|");
    if (pipe > 0) {
      push(cleaned.slice(0, pipe), cleaned.slice(pipe + 1));
      continue;
    }
    // Fallback: "AMZN: ..." or "AMZN - ..."
    const m = cleaned.match(/^([A-Z0-9][A-Z0-9.\-]{0,11})\s*[:—–-]\s*(.+)$/i);
    if (m) push(m[1], m[2]);
  }

  if (Object.keys(out).length) return out;

  // 2) JSON object fallback (strip ``` fences if present)
  let jsonText = text;
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) jsonText = fence[1].trim();
  const start = jsonText.indexOf("{");
  const end = jsonText.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      const parsed = JSON.parse(jsonText.slice(start, end + 1));
      if (parsed && typeof parsed === "object") {
        for (const [k, v] of Object.entries(parsed)) push(k, v);
      }
    } catch {
      // ignore — throw below if still empty
    }
  }

  if (!Object.keys(out).length) {
    throw new Error("Gemini did not return usable grade explanations");
  }
  return out;
}

/**
 * @param {string} key
 * @param {Array<Record<string, unknown>>} quotes
 * @returns {Promise<Record<string, string>>}
 */
export async function explainQuotesOnce(key, quotes) {
  const list = quotes || [];
  const expected = list.map((q) => String(q.ticker || "").toUpperCase());
  try {
    const { text } = await generateGeminiText(
      key,
      buildBatchGradeExplainPrompt(list),
      { maxOutputTokens: 320, temperature: 0.2 }
    );
    return parseGradeExplanations(text, expected);
  } catch (firstError) {
    const jsonPrompt = [
      buildBatchGradeExplainPrompt(list),
      "",
      'If lines are hard, reply with JSON only: {"AMZN":"…","TSLA":"…"}',
    ].join("\n");
    try {
      const { text } = await generateGeminiText(key, jsonPrompt, {
        maxOutputTokens: 320,
        temperature: 0.1,
        responseMimeType: "application/json",
      });
      return parseGradeExplanations(text, expected);
    } catch {
      throw firstError instanceof Error
        ? firstError
        : new Error(String(firstError));
    }
  }
}

/**
 * Explain grades in pairs so free Gemini is less likely to skip tickers.
 * Caller should pass only tickers that still need explanations.
 * @param {string} key
 * @param {Array<Record<string, unknown>>} quotes
 * @returns {Promise<{ map: Record<string, string>, calls: number }>}
 */
export async function explainQuotesBatch(key, quotes) {
  const list = quotes || [];
  if (!list.length) {
    throw new Error("No quotes to explain");
  }

  /** @type {Record<string, string>} */
  const merged = {};
  let calls = 0;
  const chunkSize = EXPLAIN_CHUNK_SIZE;

  const runChunks = async (/** @type {Array<Record<string, unknown>>} */ items) => {
    for (let i = 0; i < items.length; i += chunkSize) {
      const chunk = items.slice(i, i + chunkSize);
      try {
        const map = await explainQuotesOnce(key, chunk);
        Object.assign(merged, map);
        calls += 1;
      } catch {
        // Skip failed chunk; later pass may recover individuals.
        calls += 1;
      }
    }
  };

  await runChunks(list);

  let missing = list.filter(
    (q) => !merged[String(q.ticker || "").toUpperCase()]
  );
  if (missing.length) {
    await runChunks(missing);
  }

  // Last resort: one ticker at a time for anything still missing.
  missing = list.filter(
    (q) => !merged[String(q.ticker || "").toUpperCase()]
  );
  for (const quote of missing) {
    try {
      const map = await explainQuotesOnce(key, [quote]);
      Object.assign(merged, map);
      calls += 1;
    } catch {
      calls += 1;
    }
  }

  if (!Object.keys(merged).length) {
    throw new Error("Gemini did not return usable grade explanations");
  }
  return { map: merged, calls };
}

/**
 * @param {string} key
 * @param {Record<string, unknown>} quote
 * @returns {Promise<string>}
 */
export async function explainQuoteGrade(key, quote) {
  const { map } = await explainQuotesBatch(key, [quote]);
  const ticker = String(quote.ticker || "").toUpperCase();
  const text = map[ticker];
  if (!text) throw new Error("Empty Gemini explanation");
  return text;
}
