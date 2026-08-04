/**
 * Popup dashboard controller
 * --------------------------
 * Local-only: holdings, Gemini key (via storage.js → chrome.storage.local)
 * Cloud-eligible: email, watchlist, schedule → POST /api/subscribe
 */

import { fetchWatchlistSnapshot, subscribeDelivery } from "../lib/api.js";
import { explainQuoteGrade, generateGeminiText } from "../lib/gemini.js";
import { suggestTickers } from "../lib/tickers.js";
import {
  MAX_SEND_TIMES,
  MAX_WATCHLIST,
  assertNoPrivateLeak,
  buildCloudPayload,
  cacheCloudProfile,
  clearAllLocalSettings,
  defaultSchedule,
  formatDeliveryStatusLine,
  formatNextEmailLabel,
  formatScheduleLabel,
  getDeliveryStatusHint,
  getGeminiKey,
  getHoldings,
  getLocalState,
  joinTimeParts,
  normalizeSchedule,
  setAutoAnalyze,
  setDelivery,
  setDeliveryStatusHint,
  setGeminiKey,
  setHoldings,
  setWatchlist,
  splitTimeParts,
  suggestNextSendTime,
} from "../lib/storage.js";

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

/** @param {string} id */
const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing #${id} in popup.html`);
  return el;
};

const els = {
  watchlist: /** @type {HTMLUListElement} */ ($("watchlist")),
  tickerInput: /** @type {HTMLInputElement} */ ($("ticker-input")),
  tickerSuggest: /** @type {HTMLUListElement} */ ($("ticker-suggest")),
  addTicker: /** @type {HTMLButtonElement} */ ($("add-ticker")),
  email: /** @type {HTMLInputElement} */ ($("email-input")),
  days: /** @type {HTMLElement} */ ($("schedule-days")),
  times: /** @type {HTMLElement} */ ($("schedule-times")),
  addTime: /** @type {HTMLButtonElement} */ ($("add-time")),
  scheduleSummary: /** @type {HTMLElement} */ ($("schedule-summary")),
  scheduleNext: /** @type {HTMLElement} */ ($("schedule-next")),
  subscribe: /** @type {HTMLButtonElement} */ ($("subscribe-btn")),
  geminiKey: /** @type {HTMLInputElement} */ ($("gemini-key")),
  toggleKey: /** @type {HTMLButtonElement} */ ($("toggle-key")),
  autoAnalyze: /** @type {HTMLInputElement} */ ($("auto-analyze")),
  testAi: /** @type {HTMLButtonElement} */ ($("test-ai")),
  clearSettings: /** @type {HTMLButtonElement} */ ($("clear-settings")),
  watchlistCount: /** @type {HTMLElement} */ ($("watchlist-count")),
  listHead: /** @type {HTMLElement} */ ($("list-head")),
  refreshQuotes: /** @type {HTMLButtonElement} */ ($("refresh-quotes")),
  statusWatchlist: /** @type {HTMLElement} */ ($("status-watchlist")),
  statusSubscribe: /** @type {HTMLElement} */ ($("status-subscribe")),
  statusAi: /** @type {HTMLElement} */ ($("status-ai")),
  statusGlobal: /** @type {HTMLElement} */ ($("status-global")),
};

/** @type {Record<string, QuoteSnapshot>} */
let quoteCache = {};
/** True while /quotes/snapshot is in flight (show per-row loading UI). */
let quotesLoading = false;
/**
 * Local Gemini blurbs keyed by ticker.
 * @type {Record<string, { status: 'loading'|'ok'|'error', text: string }>}
 */
let aiExplainCache = {};
/** Bumps when a new explain batch starts so stale responses are ignored. */
let aiExplainGeneration = 0;

/** Highlighted row index in ticker suggestions (-1 = none). */
let suggestActiveIndex = -1;

/**
 * @typedef {{
 *   ticker: string,
 *   price?: number|null,
 *   currency?: string,
 *   grade?: string,
 *   verdict?: string,
 *   score?: number,
 *   deRatio?: number|null,
 *   pegRatio?: number|null,
 *   rsi?: number|null,
 *   aboveSma200?: boolean|null,
 *   sma200?: number|null,
 *   assetClass?: string,
 *   notes?: string[],
 *   newsRisks?: Array<string|{ title?: string, url?: string }>,
 *   error?: string|null,
 * }} QuoteSnapshot
 */

/** @typedef {'watchlist'|'subscribe'|'ai'|'global'} StatusSection */
/**
 * transient  — action logs; auto-hide after 3s with fade
 * persistent — config success; stays until the next user action
 * error      — never auto-hides; cleared when the user edits that section
 * @typedef {'transient'|'persistent'|'error'} StatusLifecycle
 */

/** Debounce timer for private holdings writes while typing. */
let holdingsSaveTimer = 0;

/** Toast hide timers (transient logs). */
let toastHideTimer = 0;
let toastFadeTimer = 0;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

init().catch((error) => {
  console.error("[Stock Agent] popup init failed", error);
  setStatus(error?.message || "Failed to load dashboard", "error", "global", "error");
});

async function init() {
  bindEvents();
  await hydrateFromStorage();
  // Live prices after local hydrate (non-blocking UX if API is down).
  void refreshQuotes({ quiet: true });
}

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function bindEvents() {
  els.addTicker.addEventListener("click", () => {
    void onAddTicker();
  });

  els.tickerInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!els.tickerSuggest.hidden) {
        event.preventDefault();
        moveSuggestHighlight(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
    }
    if (event.key === "Escape") {
      hideTickerSuggest();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const picked = getActiveSuggestSymbol();
      if (picked) {
        els.tickerInput.value = picked;
        hideTickerSuggest();
      }
      void onAddTicker();
    }
  });
  els.tickerInput.addEventListener("input", () => {
    clearStatus("watchlist");
    void updateTickerSuggest();
  });
  els.tickerInput.addEventListener("focus", () => {
    void updateTickerSuggest();
  });
  els.tickerInput.addEventListener("blur", () => {
    // Allow click on a suggestion before hiding.
    window.setTimeout(() => hideTickerSuggest(), 150);
  });

  els.tickerSuggest.addEventListener("mousedown", (event) => {
    const btn = /** @type {HTMLElement} */ (event.target).closest(
      "[data-symbol]"
    );
    if (!(btn instanceof HTMLElement)) return;
    event.preventDefault();
    const symbol = btn.dataset.symbol || "";
    if (!symbol) return;
    els.tickerInput.value = symbol;
    hideTickerSuggest();
    void onAddTicker();
  });

  els.days.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const chip = target.closest(".day-chip");
    if (!(chip instanceof HTMLButtonElement)) return;
    clearStatus("subscribe");
    onDayChipClick(chip);
  });

  document.querySelectorAll(".preset-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!(btn instanceof HTMLButtonElement)) return;
      clearStatus("subscribe");
      onDayPreset(btn.dataset.preset || "");
    });
  });

  els.addTime.addEventListener("click", () => {
    clearStatus("subscribe");
    onAddSendTime();
  });

  els.times.addEventListener("change", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.matches("select[data-part]")) {
      clearStatus("subscribe");
      void refreshScheduleSummary();
    }
  });

  els.times.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const ampm = target.closest(".ampm-btn");
    if (ampm instanceof HTMLButtonElement) {
      clearStatus("subscribe");
      onAmPmClick(ampm);
      return;
    }
    const remove = target.closest(".time-remove");
    if (remove instanceof HTMLButtonElement) {
      clearStatus("subscribe");
      onRemoveSendTime(remove);
    }
  });

  els.email.addEventListener("input", () => clearStatus("subscribe"));

  els.subscribe.addEventListener("click", () => {
    void onSaveAndSubscribe();
  });

  els.toggleKey.addEventListener("click", onToggleGeminiVisibility);

  els.geminiKey.addEventListener("input", () => clearStatus("ai"));
  // Autosave key as the user pastes / edits (still local-only).
  els.geminiKey.addEventListener("change", () => {
    void persistGeminiKeyQuiet();
  });
  els.geminiKey.addEventListener("blur", () => {
    void persistGeminiKeyQuiet();
  });

  els.autoAnalyze.addEventListener("change", () => {
    void onAutoAnalyzeChange();
  });

  els.testAi.addEventListener("click", () => {
    void onTestAi();
  });

  els.clearSettings.addEventListener("click", () => {
    void onClearAllSettings();
  });

  els.refreshQuotes.addEventListener("click", () => {
    void refreshQuotes({ quiet: false });
  });

  // Persist private lots as the user edits inline fields.
  els.watchlist.addEventListener("input", onHoldingsInput);
  els.watchlist.addEventListener("change", () => {
    void persistHoldingsFromDom();
  });
}

/**
 * Smart status helper — lifecycle-aware, section-scoped.
 *
 * @param {string} message
 * @param {"info"|"ok"|"warn"|"error"} [kind]
 * @param {StatusSection} [section]
 * @param {StatusLifecycle} [lifecycle]
 */
function setStatus(
  message,
  kind = "info",
  section = "global",
  lifecycle = "persistent"
) {
  if (lifecycle === "transient") {
    showTransientToast(message, kind, section, 3000);
    return;
  }

  // persistent + error: stay until the next action / clearStatus()
  clearToastTimers();
  const target = statusEl(section);
  const text = String(message || "").replace(/\s+/g, " ").trim();
  const clipped = text.length > 140 ? `${text.slice(0, 137)}…` : text;

  for (const key of /** @type {StatusSection[]} */ ([
    "watchlist",
    "subscribe",
    "ai",
    "global",
  ])) {
    const el = statusEl(key);
    el.classList.remove("is-toast", "is-visible", "is-fading-out");
    if (key === section) continue;
    el.hidden = true;
    el.textContent = "";
    delete el.dataset.lifecycle;
  }

  if (!clipped) {
    target.hidden = true;
    target.textContent = "";
    delete target.dataset.lifecycle;
    return;
  }

  target.hidden = false;
  target.textContent = clipped;
  target.dataset.kind = kind === "error" || lifecycle === "error" ? "error" : kind;
  target.dataset.lifecycle = lifecycle;
  target.title = text;
}

/**
 * Transient log: fade in → hold 3s → fade out → hidden.
 * @param {string} message
 * @param {"info"|"ok"|"warn"|"error"} [kind]
 * @param {StatusSection} [section]
 * @param {number} [durationMs]
 */
function showTransientToast(
  message,
  kind = "ok",
  section = "watchlist",
  durationMs = 3000
) {
  clearToastTimers();
  const target = statusEl(section);
  const text = String(message || "").replace(/\s+/g, " ").trim();
  if (!text) return;

  for (const key of /** @type {StatusSection[]} */ ([
    "watchlist",
    "subscribe",
    "ai",
    "global",
  ])) {
    if (key === section) continue;
    const el = statusEl(key);
    el.hidden = true;
    el.textContent = "";
    el.classList.remove("is-toast", "is-visible", "is-fading-out");
    delete el.dataset.lifecycle;
  }

  target.hidden = false;
  target.textContent = text;
  target.dataset.kind = kind;
  target.dataset.lifecycle = "transient";
  target.title = text;
  target.classList.add("is-toast");
  target.classList.remove("is-visible", "is-fading-out");

  requestAnimationFrame(() => {
    target.classList.add("is-visible");
  });

  toastHideTimer = window.setTimeout(() => {
    target.classList.remove("is-visible");
    target.classList.add("is-fading-out");
    toastFadeTimer = window.setTimeout(() => {
      target.hidden = true;
      target.textContent = "";
      target.classList.remove("is-toast", "is-visible", "is-fading-out");
      delete target.dataset.lifecycle;
    }, 280);
  }, durationMs);
}

/** Clear a section status (e.g. when the user edits after an error). */
function clearStatus(section) {
  const el = statusEl(section);
  if (!el) return;
  // Only interrupt transient timers when clearing the active toast section.
  if (el.dataset.lifecycle === "transient") clearToastTimers();
  el.hidden = true;
  el.textContent = "";
  el.classList.remove("is-toast", "is-visible", "is-fading-out");
  delete el.dataset.lifecycle;
}

function clearToastTimers() {
  window.clearTimeout(toastHideTimer);
  window.clearTimeout(toastFadeTimer);
  toastHideTimer = 0;
  toastFadeTimer = 0;
}

/** @param {StatusSection} section */
function statusEl(section) {
  if (section === "watchlist") return els.statusWatchlist;
  if (section === "subscribe") return els.statusSubscribe;
  if (section === "ai") return els.statusAi;
  return els.statusGlobal;
}

/** @param {number} count */
function updateCountBadge(count) {
  els.watchlistCount.textContent = `${count} / ${MAX_WATCHLIST}`;
}

// ---------------------------------------------------------------------------
// Hydration / rendering
// ---------------------------------------------------------------------------

async function hydrateFromStorage() {
  const state = await getLocalState();

  els.email.value = state.delivery.email || "";
  applyScheduleToDom(normalizeSchedule(state.delivery.schedule));
  els.geminiKey.value = state.geminiApiKey || (await getGeminiKey());
  els.autoAnalyze.checked = state.autoAnalyze !== false;

  renderWatchlist(state.watchlist, state.holdings, quoteCache);
}

// ---------------------------------------------------------------------------
// Custom schedule builder
// ---------------------------------------------------------------------------

/** @param {ReturnType<typeof normalizeSchedule>} schedule */
function applyScheduleToDom(schedule) {
  const cfg = normalizeSchedule(schedule);
  setChipDays(cfg.days);
  syncPresetChipHighlight(cfg.days);
  renderTimeRows(cfg.times);
  void refreshScheduleSummary();
}

/**
 * Hour + minute (00–59) + AM/PM. Colon is a visual separator, not part of the value.
 * Zero times is allowed in the UI; Save & Subscribe enforces at least one.
 * @param {string[]} times
 */
function renderTimeRows(times) {
  const list = (Array.isArray(times) ? times : []).slice(0, MAX_SEND_TIMES);
  els.times.innerHTML = "";

  if (!list.length) {
    const empty = document.createElement("p");
    empty.className = "empty-times";
    empty.textContent = "No send times yet — add one below.";
    els.times.appendChild(empty);
    els.addTime.disabled = false;
    els.addTime.textContent = "+ Add a send time";
    return;
  }

  list.forEach((time, index) => {
    const parts = splitTimeParts(time);
    const row = document.createElement("div");
    row.className = "time-row";
    row.dataset.index = String(index);

    const hour = document.createElement("select");
    hour.dataset.part = "hour";
    hour.setAttribute("aria-label", `Send time ${index + 1} hour`);
    for (let h = 1; h <= 12; h += 1) {
      const opt = document.createElement("option");
      opt.value = String(h);
      opt.textContent = String(h);
      if (h === parts.hour12) opt.selected = true;
      hour.appendChild(opt);
    }

    const colon = document.createElement("span");
    colon.className = "time-colon";
    colon.textContent = ":";
    colon.setAttribute("aria-hidden", "true");

    const minute = document.createElement("select");
    minute.dataset.part = "minute";
    minute.setAttribute("aria-label", `Send time ${index + 1} minutes`);
    for (let m = 0; m < 60; m += 1) {
      const opt = document.createElement("option");
      opt.value = String(m);
      opt.textContent = String(m).padStart(2, "0");
      if (m === parts.minute) opt.selected = true;
      minute.appendChild(opt);
    }

    const ampm = document.createElement("div");
    ampm.className = "ampm-toggle";
    ampm.setAttribute("role", "group");
    ampm.setAttribute("aria-label", `Send time ${index + 1} AM or PM`);

    for (const period of /** @type {const} */ (["AM", "PM"])) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ampm-btn";
      btn.dataset.period = period;
      btn.textContent = period;
      btn.setAttribute("aria-pressed", period === parts.period ? "true" : "false");
      if (period === parts.period) btn.classList.add("is-active");
      ampm.appendChild(btn);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost time-remove";
    remove.textContent = "✕";
    remove.title = "Remove this send time";
    remove.setAttribute("aria-label", `Remove send time ${index + 1}`);

    row.append(hour, colon, minute, ampm, remove);
    els.times.appendChild(row);
  });

  els.addTime.disabled = list.length >= MAX_SEND_TIMES;
  els.addTime.textContent =
    list.length >= MAX_SEND_TIMES
      ? `Maximum ${MAX_SEND_TIMES} times/day`
      : "+ Add another time";
  els.addTime.title =
    list.length >= MAX_SEND_TIMES
      ? `Maximum ${MAX_SEND_TIMES} times per day`
      : "Add another send time";
}

async function refreshScheduleSummary() {
  const schedule = readScheduleFromDom();
  els.scheduleSummary.textContent = formatScheduleLabel(schedule);
  const hint = await getDeliveryStatusHint();
  els.scheduleNext.textContent = formatDeliveryStatusLine(schedule, hint);
  els.scheduleNext.title = formatNextEmailLabel(schedule);
}

/** @param {HTMLButtonElement} btn */
function onAmPmClick(btn) {
  const toggle = btn.closest(".ampm-toggle");
  if (!toggle) return;
  for (const sibling of toggle.querySelectorAll(".ampm-btn")) {
    if (!(sibling instanceof HTMLButtonElement)) continue;
    const active = sibling === btn;
    sibling.classList.toggle("is-active", active);
    sibling.setAttribute("aria-pressed", active ? "true" : "false");
  }
  void refreshScheduleSummary();
}

function onAddSendTime() {
  const times = readTimesFromDom();
  if (times.length >= MAX_SEND_TIMES) {
    setStatus(
      `You can schedule up to ${MAX_SEND_TIMES} times per day (max 14 per week).`,
      "warn",
      "subscribe",
      "error"
    );
    return;
  }

  const suggestion = suggestNextSendTime(times);
  if (!suggestion) {
    setStatus("No more unique send times available.", "warn", "subscribe", "error");
    return;
  }

  renderTimeRows([...times, suggestion]);
  void refreshScheduleSummary();
}

/** @param {HTMLButtonElement} btn */
function onRemoveSendTime(btn) {
  const row = btn.closest(".time-row");
  if (!row) return;
  row.remove();
  renderTimeRows(readTimesFromDom());
  void refreshScheduleSummary();
}

/** @param {string} preset */
function onDayPreset(preset) {
  /** @type {number[]} */
  let days = [];
  if (preset === "daily") days = [0, 1, 2, 3, 4, 5, 6];
  else if (preset === "weekdays") days = [1, 2, 3, 4, 5];
  else if (preset === "weekends") days = [0, 6];
  else return;

  // Force Mon–Sun chips to match the macro selection visually.
  setChipDays(days);
  syncPresetChipHighlight(days);
  void refreshScheduleSummary();
}

/** @param {HTMLButtonElement} chip */
function onDayChipClick(chip) {
  const day = Number(chip.dataset.day);
  if (!Number.isInteger(day)) return;

  const next = !chip.classList.contains("is-active");
  if (next) {
    chip.classList.add("is-active");
    chip.setAttribute("aria-pressed", "true");
  } else {
    chip.classList.remove("is-active");
    chip.setAttribute("aria-pressed", "false");
  }
  syncPresetChipHighlight(readSelectedDays());
  void refreshScheduleSummary();
}

/**
 * Programmatically sync each Sun–Sat chip's active/pressed state.
 * @param {number[]} days
 */
function setChipDays(days) {
  const selected = new Set(
    (Array.isArray(days) ? days : [])
      .map((d) => Number(d))
      .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
  );

  const chips = els.days.querySelectorAll(".day-chip");
  for (const chip of chips) {
    if (!(chip instanceof HTMLButtonElement)) continue;
    const day = Number(chip.dataset.day);
    const active = selected.has(day);
    // Explicit add/remove so macro clicks always repaint (not only toggle).
    if (active) {
      chip.classList.add("is-active");
      chip.setAttribute("aria-pressed", "true");
    } else {
      chip.classList.remove("is-active");
      chip.setAttribute("aria-pressed", "false");
    }
  }
}

/** Highlight the matching macro pill (Every day / Weekdays / Weekends). */
function syncPresetChipHighlight(days) {
  const key = [...new Set(days)].sort((a, b) => a - b).join(",");
  const map = {
    daily: "0,1,2,3,4,5,6",
    weekdays: "1,2,3,4,5",
    weekends: "0,6",
  };

  document.querySelectorAll(".preset-chip").forEach((btn) => {
    if (!(btn instanceof HTMLButtonElement)) return;
    const preset = btn.dataset.preset || "";
    const active = Boolean(map[preset] && map[preset] === key);
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

/** @returns {number[]} */
function readSelectedDays() {
  return [...els.days.querySelectorAll(".day-chip.is-active")]
    .map((chip) => Number(/** @type {HTMLElement} */ (chip).dataset.day))
    .filter((day) => Number.isInteger(day) && day >= 0 && day <= 6)
    .sort((a, b) => a - b);
}

/** @returns {string[]} */
function readTimesFromDom() {
  /** @type {string[]} */
  const times = [];
  for (const row of els.times.querySelectorAll(".time-row")) {
    if (!(row instanceof HTMLElement)) continue;
    const hourSelect = row.querySelector('select[data-part="hour"]');
    const minuteSelect = row.querySelector('select[data-part="minute"]');
    const periodBtn = row.querySelector(".ampm-btn.is-active");
    if (!(hourSelect instanceof HTMLSelectElement)) continue;
    if (!(minuteSelect instanceof HTMLSelectElement)) continue;
    const period =
      periodBtn instanceof HTMLButtonElement
        ? periodBtn.dataset.period || "AM"
        : "AM";
    times.push(
      joinTimeParts(
        Number(hourSelect.value),
        Number(minuteSelect.value),
        period
      )
    );
  }
  return times;
}

/** @returns {ReturnType<typeof normalizeSchedule>} */
function readScheduleFromDom() {
  return normalizeSchedule({
    days: readSelectedDays(),
    times: readTimesFromDom(),
  });
}

/**
 * Render each ticker with inline private portfolio fields + live quote strip.
 * @param {string[]} watchlist
 * @param {Record<string, { shares?: number|null, buyPrice?: number|null }>} [holdings]
 * @param {Record<string, QuoteSnapshot>} [quotes]
 */
function renderWatchlist(watchlist, holdings = {}, quotes = quoteCache) {
  els.watchlist.innerHTML = "";
  updateCountBadge(watchlist.length);

  // Column headers only appear once there is at least one ticker.
  els.listHead.hidden = watchlist.length === 0;

  if (!watchlist.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No tickers yet — add a symbol to start.";
    els.watchlist.appendChild(empty);
    return;
  }

  for (const ticker of watchlist) {
    const lot = holdings[ticker] || {};
    const quote = quotes[ticker] || null;
    const li = document.createElement("li");
    li.className = "ticker-card";
    li.dataset.ticker = ticker;

    const row = document.createElement("div");
    row.className = "ticker-row";

    const symbol = document.createElement("span");
    symbol.className = "symbol";
    symbol.textContent = ticker;

    const shares = document.createElement("input");
    shares.type = "number";
    shares.min = "0";
    shares.step = "any";
    shares.placeholder = "0";
    shares.title = "Number of shares owned (private)";
    shares.setAttribute("aria-label", `${ticker} number of shares owned`);
    shares.dataset.ticker = ticker;
    shares.dataset.field = "shares";
    shares.value =
      lot.shares === null || lot.shares === undefined ? "" : String(lot.shares);

    const buyPrice = document.createElement("input");
    buyPrice.type = "number";
    buyPrice.min = "0";
    buyPrice.step = "any";
    buyPrice.placeholder = "0.00";
    buyPrice.title = "Average buy price (private)";
    buyPrice.setAttribute("aria-label", `${ticker} average buy price`);
    buyPrice.dataset.ticker = ticker;
    buyPrice.dataset.field = "buyPrice";
    buyPrice.value =
      lot.buyPrice === null || lot.buyPrice === undefined
        ? ""
        : String(lot.buyPrice);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost remove";
    remove.setAttribute("aria-label", `Remove ${ticker}`);
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      void onRemoveTicker(ticker);
    });

    row.append(symbol, shares, buyPrice, remove);

    const meta = document.createElement("div");
    meta.className = "quote-meta";
    meta.append(...buildQuoteMetaNodes(quote));

    li.append(row, meta);

    const ai = buildAiBlurbNode(ticker, quote);
    if (ai) li.appendChild(ai);

    els.watchlist.appendChild(li);
  }
}

/**
 * @param {string} ticker
 * @param {QuoteSnapshot | null} quote
 * @returns {HTMLElement|null}
 */
function buildAiBlurbNode(ticker, quote) {
  const entry = aiExplainCache[ticker];
  if (!entry) return null;
  if (!quote || (quote.error && quote.price == null)) return null;

  const blurb = document.createElement("p");
  blurb.className = "ai-blurb";
  blurb.dataset.ticker = ticker;

  if (entry.status === "loading") {
    blurb.classList.add("is-loading");
    blurb.setAttribute("aria-busy", "true");
    blurb.textContent = "Gemini is explaining this grade…";
    return blurb;
  }
  if (entry.status === "error") {
    blurb.classList.add("is-error");
    blurb.textContent = entry.text || "AI explanation unavailable.";
    return blurb;
  }

  blurb.textContent = entry.text;
  return blurb;
}

/**
 * Patch one card's AI blurb without rebuilding holdings inputs.
 * @param {string} ticker
 */
function patchAiBlurb(ticker) {
  const card = els.watchlist.querySelector(
    `.ticker-card[data-ticker="${CSS.escape(ticker)}"]`
  );
  if (!(card instanceof HTMLElement)) return;
  const quote = quoteCache[ticker] || null;
  const existing = card.querySelector(".ai-blurb");
  const next = buildAiBlurbNode(ticker, quote);
  if (!next) {
    existing?.remove();
    return;
  }
  if (existing) existing.replaceWith(next);
  else card.appendChild(next);
}

/**
 * @param {QuoteSnapshot | null} quote
 * @returns {Node[]}
 */
function buildQuoteMetaNodes(quote) {
  if (quotesLoading && (!quote || (quote.price == null && !quote.error))) {
    const loading = document.createElement("span");
    loading.className = "quote-loading";
    loading.setAttribute("aria-busy", "true");
    loading.textContent = "Fetching live data…";
    return [loading];
  }

  if (!quote) {
    const pending = document.createElement("span");
    pending.className = "quote-pending";
    pending.textContent = "Price — · Grade —";
    return [pending];
  }

  if (quote.error && quote.price == null) {
    const err = document.createElement("span");
    err.className = "quote-error";
    err.textContent = "Quote unavailable";
    err.title = String(quote.error);
    return [err];
  }

  const priceEl = document.createElement("span");
  priceEl.className = "quote-price";
  if (typeof quote.price === "number") {
    const cur = quote.currency || "USD";
    priceEl.textContent = `${formatPrice(quote.price)} ${cur}`;
  } else {
    priceEl.textContent = "Price n/a";
  }

  const gradeEl = document.createElement("span");
  const grade = String(quote.grade || "HOLD");
  gradeEl.className = `quote-grade grade-${grade.toLowerCase()}`;
  gradeEl.textContent = quote.verdict || grade;

  return [priceEl, gradeEl];
}

/** @param {number} value */
function formatPrice(value) {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: value >= 1000 ? 2 : 4,
  });
}

/**
 * Fetch live yfinance snapshot + grades for the current watchlist.
 * @param {{ quiet?: boolean }} [opts]
 */
async function refreshQuotes(opts = {}) {
  const quiet = Boolean(opts.quiet);
  const state = await getLocalState();
  const watchlist = state.watchlist || [];

  if (!watchlist.length) {
    quoteCache = {};
    quotesLoading = false;
    if (!quiet) {
      setStatus("Add a ticker before refreshing quotes.", "warn", "watchlist", "error");
    }
    return;
  }

  quotesLoading = true;
  els.refreshQuotes.disabled = true;
  els.refreshQuotes.classList.add("is-loading");
  els.refreshQuotes.setAttribute("aria-busy", "true");
  els.refreshQuotes.textContent = "Loading…";
  renderWatchlist(watchlist, state.holdings, quoteCache);
  setStatus("Fetching live prices & grades…", "info", "watchlist", "persistent");

  try {
    const data = await fetchWatchlistSnapshot(watchlist);
    /** @type {Record<string, QuoteSnapshot>} */
    const next = {};
    for (const q of data?.quotes || []) {
      if (q?.ticker) next[q.ticker] = q;
    }
    quoteCache = next;
    renderWatchlist(watchlist, state.holdings, quoteCache);
    const n = Object.keys(next).length;
    setStatus(
      `Updated ${n} quote${n === 1 ? "" : "s"}.`,
      "ok",
      "watchlist",
      "transient"
    );
    void explainGradesAfterRefresh(watchlist, next);
  } catch (error) {
    console.error("[Stock Agent] quote refresh failed", error);
    setStatus(
      error?.message || "Could not fetch live quotes.",
      "error",
      "watchlist",
      "error"
    );
  } finally {
    quotesLoading = false;
    els.refreshQuotes.disabled = false;
    els.refreshQuotes.classList.remove("is-loading");
    els.refreshQuotes.removeAttribute("aria-busy");
    els.refreshQuotes.textContent = "Refresh";
    renderWatchlist(watchlist, state.holdings, quoteCache);
  }
}

/**
 * After quotes land, ask local Gemini for a short "why this grade" blurb per ticker.
 * @param {string[]} watchlist
 * @param {Record<string, QuoteSnapshot>} quotes
 */
async function explainGradesAfterRefresh(watchlist, quotes) {
  const key =
    els.geminiKey.value.trim() || String((await getGeminiKey()) || "").trim();
  if (!key) {
    aiExplainCache = {};
    return;
  }
  if (!els.autoAnalyze.checked) {
    aiExplainCache = {};
    return;
  }

  const generation = ++aiExplainGeneration;
  /** @type {Record<string, { status: 'loading'|'ok'|'error', text: string }>} */
  const nextCache = {};
  for (const ticker of watchlist) {
    const quote = quotes[ticker];
    if (!quote || (quote.error && quote.price == null)) continue;
    nextCache[ticker] = { status: "loading", text: "" };
  }
  aiExplainCache = nextCache;
  renderWatchlist(watchlist, (await getLocalState()).holdings, quoteCache);

  if (!Object.keys(nextCache).length) return;

  setStatus("Gemini explaining grades…", "info", "ai", "persistent");

  let okCount = 0;
  let failCount = 0;
  for (const ticker of Object.keys(nextCache)) {
    if (generation !== aiExplainGeneration) return;
    try {
      const text = await explainQuoteGrade(key, /** @type {Record<string, unknown>} */ (quotes[ticker]));
      if (generation !== aiExplainGeneration) return;
      aiExplainCache[ticker] = { status: "ok", text };
      okCount += 1;
    } catch (error) {
      console.warn("[AI explain] failed", ticker, error);
      if (generation !== aiExplainGeneration) return;
      aiExplainCache[ticker] = {
        status: "error",
        text: formatGeminiError(error),
      };
      failCount += 1;
    }
    patchAiBlurb(ticker);
  }

  if (generation !== aiExplainGeneration) return;
  if (okCount && !failCount) {
    setStatus(
      `AI explained ${okCount} grade${okCount === 1 ? "" : "s"} locally.`,
      "ok",
      "ai",
      "transient"
    );
  } else if (okCount && failCount) {
    setStatus(
      `AI explained ${okCount}; ${failCount} failed (quota or model).`,
      "warn",
      "ai",
      "persistent"
    );
  } else if (failCount) {
    setStatus(
      formatGeminiError(new Error(aiExplainCache[Object.keys(aiExplainCache)[0]]?.text || "AI failed")),
      "error",
      "ai",
      "error"
    );
  }
}

// ---------------------------------------------------------------------------
// Ticker autocomplete (static NASDAQ/NYSE/AMEX + major TSX list)
// ---------------------------------------------------------------------------

function hideTickerSuggest() {
  els.tickerSuggest.hidden = true;
  els.tickerSuggest.innerHTML = "";
  suggestActiveIndex = -1;
  els.tickerInput.setAttribute("aria-expanded", "false");
}

/** @returns {string} */
function getActiveSuggestSymbol() {
  const items = els.tickerSuggest.querySelectorAll("[data-symbol]");
  if (suggestActiveIndex < 0 || suggestActiveIndex >= items.length) return "";
  return /** @type {HTMLElement} */ (items[suggestActiveIndex]).dataset.symbol || "";
}

/** @param {number} delta */
function moveSuggestHighlight(delta) {
  const items = [...els.tickerSuggest.querySelectorAll(".ticker-suggest-item")];
  if (!items.length) return;
  suggestActiveIndex = (suggestActiveIndex + delta + items.length) % items.length;
  items.forEach((el, i) => {
    el.classList.toggle("is-active", i === suggestActiveIndex);
  });
  items[suggestActiveIndex]?.scrollIntoView({ block: "nearest" });
}

async function updateTickerSuggest() {
  const query = els.tickerInput.value.trim();
  if (!query) {
    hideTickerSuggest();
    return;
  }

  let rows = [];
  try {
    rows = await suggestTickers(query, 12);
  } catch (error) {
    console.warn("[Stock Agent] ticker suggest failed", error);
    hideTickerSuggest();
    return;
  }

  if (!rows.length) {
    hideTickerSuggest();
    return;
  }

  els.tickerSuggest.innerHTML = "";
  suggestActiveIndex = -1;
  for (const row of rows) {
    const li = document.createElement("li");
    li.setAttribute("role", "option");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ticker-suggest-item";
    btn.dataset.symbol = row.symbol;

    const sym = document.createElement("span");
    sym.className = "ticker-suggest-sym";
    sym.textContent = row.symbol;

    const meta = document.createElement("span");
    meta.className = "ticker-suggest-meta";
    const label = row.name && row.name !== row.symbol ? row.name : "";
    meta.textContent = [label, row.exchange].filter(Boolean).join(" · ");

    btn.append(sym, meta);
    li.append(btn);
    els.tickerSuggest.appendChild(li);
  }

  els.tickerSuggest.hidden = false;
  els.tickerInput.setAttribute("aria-expanded", "true");
}

// ---------------------------------------------------------------------------
// Watchlist mutations
// ---------------------------------------------------------------------------

async function onAddTicker() {
  const ticker = els.tickerInput.value.trim().toUpperCase();
  if (!ticker) {
    setStatus("Enter a ticker symbol first.", "warn", "watchlist", "error");
    return;
  }

  const state = await getLocalState();
  const watchlist = [...state.watchlist];

  if (watchlist.includes(ticker)) {
    setStatus(`${ticker} is already on your watchlist.`, "warn", "watchlist", "error");
    return;
  }
  if (watchlist.length >= MAX_WATCHLIST) {
    setStatus(
      `Watchlist capped at ${MAX_WATCHLIST} tickers.`,
      "warn",
      "watchlist",
      "error"
    );
    return;
  }

  watchlist.push(ticker);
  const result = await setWatchlist(watchlist);
  els.tickerInput.value = "";
  els.tickerInput.focus();
  renderWatchlist(result.watchlist, result.holdings, quoteCache);
  setStatus(`Added ${ticker}.`, "ok", "watchlist", "transient");
  void refreshQuotes({ quiet: true });
}

/**
 * @param {string} ticker
 */
async function onRemoveTicker(ticker) {
  const state = await getLocalState();
  const watchlist = state.watchlist.filter((item) => item !== ticker);
  const result = await setWatchlist(watchlist);
  delete quoteCache[ticker];
  delete aiExplainCache[ticker];
  renderWatchlist(result.watchlist, result.holdings, quoteCache);
  setStatus(`Removed ${ticker}.`, "ok", "watchlist", "transient");
}

// ---------------------------------------------------------------------------
// Private holdings (client-side only)
// ---------------------------------------------------------------------------

/** @param {Event} event */
function onHoldingsInput(event) {
  const target = /** @type {HTMLElement} */ (event.target);
  if (!(target instanceof HTMLInputElement) || !target.dataset.ticker) return;

  window.clearTimeout(holdingsSaveTimer);
  holdingsSaveTimer = window.setTimeout(() => {
    void persistHoldingsFromDom();
  }, 280);
}

/**
 * Collect inline Shares / Avg buy inputs and write to chrome.storage.local.
 * This path never builds a cloud payload.
 */
async function persistHoldingsFromDom() {
  /** @type {Record<string, { shares: number|null, buyPrice: number|null }>} */
  const holdings = {};

  const inputs = els.watchlist.querySelectorAll("input[data-ticker]");
  for (const input of inputs) {
    if (!(input instanceof HTMLInputElement)) continue;
    const ticker = input.dataset.ticker;
    const field = input.dataset.field;
    if (!ticker || (field !== "shares" && field !== "buyPrice")) continue;

    if (!holdings[ticker]) {
      holdings[ticker] = { shares: null, buyPrice: null };
    }

    const raw = input.value.trim();
    if (raw === "") {
      holdings[ticker][field] = null;
    } else {
      const num = Number(raw);
      holdings[ticker][field] = Number.isFinite(num) ? num : null;
    }
  }

  // Merge with any lots for tickers not currently painted (safety).
  const existing = await getHoldings();
  const merged = { ...existing, ...holdings };
  await setHoldings(merged);
  setStatus("Private lots saved on this device only.", "ok", "watchlist", "transient");
}

// ---------------------------------------------------------------------------
// Gemini key / AI panel (BYOK — local only, client → Google)
// ---------------------------------------------------------------------------

function onToggleGeminiVisibility() {
  const revealing = els.geminiKey.type === "password";
  els.geminiKey.type = revealing ? "text" : "password";
  els.toggleKey.setAttribute("aria-pressed", revealing ? "true" : "false");
  els.toggleKey.setAttribute(
    "aria-label",
    revealing ? "Hide Gemini API key" : "Show Gemini API key"
  );
}

async function persistGeminiKeyQuiet() {
  const key = els.geminiKey.value.trim();
  await setGeminiKey(key);
  if (key) {
    setStatus("Gemini key saved locally.", "ok", "ai", "persistent");
  }
}

async function onAutoAnalyzeChange() {
  await setAutoAnalyze(els.autoAnalyze.checked);
  setStatus(
    els.autoAnalyze.checked
      ? "Auto-explain on — Gemini will summarize grades after each refresh."
      : "Auto-explain off — quotes only, no AI.",
    "ok",
    "ai",
    "persistent"
  );
  if (!els.autoAnalyze.checked) {
    aiExplainCache = {};
    aiExplainGeneration += 1;
    const state = await getLocalState();
    renderWatchlist(state.watchlist, state.holdings, quoteCache);
  }
}

/**
 * Ping Gemini with the pasted key (browser → Google only; never our servers).
 */
async function onTestAi() {
  const key = els.geminiKey.value.trim();
  if (!key) {
    setStatus("Paste a Gemini API key first.", "warn", "ai", "error");
    els.geminiKey.focus();
    return;
  }

  els.testAi.disabled = true;
  setStatus("Testing Gemini connection…", "info", "ai", "persistent");

  try {
    await setGeminiKey(key);
    const { model, text } = await pingGemini(key);
    console.log("[Test AI] Gemini OK", { model, text });
    setStatus(`AI OK — ${model} accepted your key.`, "ok", "ai", "persistent");
  } catch (error) {
    console.error("[Test AI] failed", error);
    setStatus(formatGeminiError(error), "error", "ai", "error");
  } finally {
    els.testAi.disabled = false;
  }
}

/**
 * @param {string} key
 * @returns {Promise<{ model: string, text: string }>}
 */
async function pingGemini(key) {
  return generateGeminiText(
    key,
    "Reply with exactly: STOCK_AGENT_OK",
    { maxOutputTokens: 16, temperature: 0 }
  );
}

/** @param {unknown} error @returns {string} */
function formatGeminiError(error) {
  const raw = error instanceof Error ? error.message : String(error || "");
  const lower = raw.toLowerCase();

  if (lower.includes("quota") || lower.includes("rate limit") || lower.includes("resource_exhausted")) {
    return "Gemini free-tier quota hit. Wait a minute or try another key in AI Studio.";
  }
  if (lower.includes("api key") || lower.includes("invalid") || lower.includes("permission")) {
    return "Invalid Gemini API key. Use Get API key → and paste a fresh key.";
  }
  if (lower.includes("not found") || lower.includes("is not found")) {
    return "No free Gemini model available for this key. Enable Gemini in AI Studio.";
  }
  // Keep the popup compact — never dump Google's full error blob into the UI.
  const short = raw.replace(/\s+/g, " ").trim();
  return short.length > 110 ? `${short.slice(0, 107)}…` : short || "Gemini test failed";
}

async function onClearAllSettings() {
  const confirmed = window.confirm(
    "Clear all Stock Agent settings on this device?\n\nThis removes your watchlist, private lots, Gemini key, and email schedule from local storage."
  );
  if (!confirmed) return;

  await clearAllLocalSettings();
  els.email.value = "";
  applyScheduleToDom(defaultSchedule());
  els.geminiKey.value = "";
  els.geminiKey.type = "password";
  els.toggleKey.setAttribute("aria-pressed", "false");
  els.toggleKey.setAttribute("aria-label", "Show Gemini API key");
  els.autoAnalyze.checked = true;
  quoteCache = {};
  aiExplainCache = {};
  aiExplainGeneration += 1;
  renderWatchlist([], {}, quoteCache);
  setStatus("All local settings cleared.", "ok", "global", "persistent");
}

// ---------------------------------------------------------------------------
// Email scheduler — live POST /api/subscribe
// ---------------------------------------------------------------------------

/**
 * Save delivery prefs locally, strip private fields, upsert to cloud.
 */
async function onSaveAndSubscribe() {
  const email = els.email.value.trim();
  const schedule = readScheduleFromDom();

  if (!email || !email.includes("@")) {
    setStatus("Enter a valid email address.", "warn", "subscribe", "error");
    els.email.focus();
    return;
  }

  if (!schedule.times.length) {
    setStatus("Add at least one send time.", "warn", "subscribe", "error");
    return;
  }

  if (!schedule.days.length) {
    setStatus("Pick at least one delivery day.", "warn", "subscribe", "error");
    return;
  }

  els.subscribe.disabled = true;
  setStatus("Saving subscription…", "info", "subscribe", "persistent");

  try {
    const state = await getLocalState();

    if (!state.watchlist.length) {
      setStatus(
        "Add at least one ticker before subscribing.",
        "warn",
        "subscribe",
        "error"
      );
      return;
    }

    const delivery = await setDelivery({
      email,
      schedule,
      enabled: true,
    });

    const localView = {
      ...state,
      delivery,
      watchlist: state.watchlist,
      holdings: state.holdings,
      geminiApiKey: state.geminiApiKey,
    };

    const outbound = assertNoPrivateLeak(buildCloudPayload(localView));
    console.log("[SUBSCRIBE] sanitized outbound payload:", outbound);

    const response = await subscribeDelivery(localView);

    await cacheCloudProfile({
      watchlist: outbound.watchlist,
      delivery: {
        email: outbound.email,
        schedule: outbound.schedule,
        enabled: outbound.enabled,
      },
      userId: response?.id || response?.userId || state.userId,
    });

    const scheduleLabel = formatScheduleLabel(outbound.schedule);
    let message = `Saved & Subscribed successfully! ${scheduleLabel} → ${outbound.email}`;
    if (response?.report_sent_now || response?.report_send_status === "sending") {
      message =
        `Saved & Subscribed — send window is open, report is emailing now. ` +
        `${scheduleLabel} → ${outbound.email}`;
    } else if (response?.report_send_status === "failed") {
      message =
        `Saved & Subscribed, but could not start the immediate email. ` +
        `Cron will retry while your window is open. ${scheduleLabel} → ${outbound.email}`;
    } else if (response?.report_send_status === "daily_cap") {
      message =
        `Saved & Subscribed (daily email cap reached). ${scheduleLabel} → ${outbound.email}`;
    } else if (response?.report_send_status === "already_sent") {
      message =
        `Saved & Subscribed (already sent for this time slot). ${scheduleLabel} → ${outbound.email}`;
    }

    const sendStatus = String(response?.report_send_status || "not_due");
    await setDeliveryStatusHint({
      status: sendStatus,
      at: new Date().toISOString(),
      detail: message,
    });
    await refreshScheduleSummary();

    setStatus(message, "ok", "subscribe", "persistent");
  } catch (error) {
    console.error("[SUBSCRIBE] failed", error);
    setStatus(error?.message || "Subscribe failed", "error", "subscribe", "error");
  } finally {
    els.subscribe.disabled = false;
  }
}
