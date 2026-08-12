﻿﻿﻿﻿/**
 * Popup dashboard controller
 * --------------------------
 * Local-only: holdings, Gemini key (via storage.js → chrome.storage.local)
 * Cloud-eligible: email, watchlist, schedule → POST /api/subscribe
 */

import { fetchWatchlistSnapshot, subscribeDelivery } from "../lib/api.js";
import { explainQuotesOnce, generateGeminiText } from "../lib/gemini.js";
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
  getCachedQuotes,
  getCachedQuotesFetchedAt,
  joinTimeParts,
  normalizeSchedule,
  setDelivery,
  setDeliveryStatusHint,
  expireDeliveryStatusHint,
  setGeminiKey,
  setHoldings,
  setCachedQuotes,
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
  testAi: /** @type {HTMLButtonElement} */ ($("test-ai")),
  explainGrades: /** @type {HTMLButtonElement} */ ($("explain-grades")),
  clearSettings: /** @type {HTMLButtonElement} */ ($("clear-settings")),
  watchlistCount: /** @type {HTMLElement} */ ($("watchlist-count")),
  listHead: /** @type {HTMLElement} */ ($("list-head")),
  sortRow: /** @type {HTMLElement} */ ($("sort-row")),
  refreshQuotes: /** @type {HTMLButtonElement} */ ($("refresh-quotes")),
  quotesUpdated: /** @type {HTMLElement} */ ($("quotes-updated")),
  portfolioSummary: /** @type {HTMLElement} */ ($("portfolio-summary")),
  statusWatchlist: /** @type {HTMLElement} */ ($("status-watchlist")),
  statusWatchlistAdd: /** @type {HTMLElement} */ ($("status-watchlist-add")),
  statusSubscribe: /** @type {HTMLElement} */ ($("status-subscribe")),
  statusAi: /** @type {HTMLElement} */ ($("status-ai")),
  statusGlobal: /** @type {HTMLElement} */ ($("status-global")),
};

/** @type {Record<string, QuoteSnapshot>} */
let quoteCache = {};
/** True while any quote fetch is in flight. */
let quotesLoading = false;
/** Tickers still waiting on a sequential snapshot fetch. */
let quotesPending = /** @type {Set<string>} */ (new Set());
/** When quotes were last successfully refreshed (local clock). */
let quotesUpdatedAt = /** @type {Date|null} */ (null);
/** Skip auto-refresh on popup reopen if cache is newer than this. */
const QUOTE_FRESH_MS = 5 * 60 * 1000;
/** @type {'symbol'|'grade'|'pnl'} */
let watchlistSort = "symbol";
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
 *   asOf?: string|null,
 * }} QuoteSnapshot
 */

/** @typedef {'watchlist'|'watchlistAdd'|'subscribe'|'ai'|'global'} StatusSection */
/**
 * transient  — action logs; auto-hide after 3s with fade
 * persistent — config success; stays until the next user action
 * error      — never auto-hides; cleared when the user edits that section
 * @typedef {'transient'|'persistent'|'error'} StatusLifecycle
 */

/** Debounce timer for private holdings writes while typing. */
let holdingsSaveTimer = 0;
/** Last shares/avg-buy ticker edited — for inline save confirmation. */
let holdingsLastEditedTicker = /** @type {string|null} */ (null);

/** Toast hide timers (transient logs). */
let toastHideTimer = 0;
let toastFadeTimer = 0;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

// Apply saved theme immediately (before DOM renders) to avoid flash.
(async function applyThemeEarly() {
  try {
    const result = await chrome.storage.local.get("theme");
    const theme = result.theme || "dark";
    document.documentElement.setAttribute("data-theme", theme);
  } catch {
    // Default to dark if storage unavailable.
  }
})();

init().catch((error) => {
  console.error("[Stock Agent] popup init failed", error);
  setStatus(error?.message || "Failed to load dashboard", "error", "global", "error");
});

async function init() {
  bindEvents();
  await hydrateFromStorage();
  // Reuse local cache on quick reopen; only fetch when stale or incomplete.
  void maybeAutoRefreshQuotes();
  window.setInterval(() => updateQuotesUpdatedLabel(), 30_000);
}

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function bindEvents() {
  // Theme toggle
  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      // Rotation animation
      themeBtn.classList.add("is-animating");
      setTimeout(() => themeBtn.classList.remove("is-animating"), 400);
      // Persist preference
      chrome.storage.local.set({ theme: next });
    });
  }

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
    clearStatus("watchlistAdd");
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

  els.testAi.addEventListener("click", () => {
    void onTestAi();
  });

  els.explainGrades.addEventListener("click", () => {
    void onExplainGrades();
  });

  els.clearSettings.addEventListener("click", () => {
    void onClearAllSettings();
  });

  els.refreshQuotes.addEventListener("click", () => {
    void refreshQuotes({ quiet: false });
  });

  els.sortRow.addEventListener("click", (event) => {
    const btn = /** @type {HTMLElement} */ (event.target).closest("[data-sort]");
    if (!(btn instanceof HTMLButtonElement)) return;
    const mode = btn.dataset.sort;
    if (mode !== "symbol" && mode !== "grade" && mode !== "pnl") return;
    watchlistSort = mode;
    chrome.storage.local.set({ watchlistSort: mode });
    for (const chip of els.sortRow.querySelectorAll(".sort-chip")) {
      if (!(chip instanceof HTMLButtonElement)) continue;
      const active = chip === btn;
      chip.classList.toggle("is-active", active);
      chip.setAttribute("aria-pressed", active ? "true" : "false");
    }
    void rerenderWatchlist();
  });

  // Persist private lots as the user edits inline fields.
  els.watchlist.addEventListener("input", onHoldingsInput);
  els.watchlist.addEventListener("change", () => {
    void persistHoldingsFromDom(holdingsLastEditedTicker);
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
    "watchlistAdd",
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
    "watchlistAdd",
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
  if (section === "watchlistAdd") return els.statusWatchlistAdd;
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

  // Restore sort preference.
  try {
    const sortResult = await chrome.storage.local.get("watchlistSort");
    const saved = sortResult.watchlistSort;
    if (saved === "symbol" || saved === "grade" || saved === "pnl") {
      watchlistSort = saved;
      for (const chip of els.sortRow.querySelectorAll(".sort-chip")) {
        if (!(chip instanceof HTMLButtonElement)) continue;
        const active = chip.dataset.sort === saved;
        chip.classList.toggle("is-active", active);
        chip.setAttribute("aria-pressed", active ? "true" : "false");
      }
    }
  } catch { /* ignore */ }

  // Show last stable grades immediately so a cold Yahoo fetch doesn't flash low scores.
  quoteCache = await getCachedQuotes();
  quotesUpdatedAt = await getCachedQuotesFetchedAt();
  if (!quotesUpdatedAt) {
    const asOfTimes = Object.values(quoteCache)
      .map((q) => (q?.asOf ? Date.parse(String(q.asOf)) : NaN))
      .filter((t) => Number.isFinite(t));
    if (asOfTimes.length) {
      quotesUpdatedAt = new Date(Math.max(...asOfTimes));
    }
  }
  updateQuotesUpdatedLabel();
  renderWatchlist(state.watchlist, state.holdings, quoteCache);
}

/**
 * On popup open: show cache, fetch only if missing tickers or cache is stale.
 * Manual Refresh always forces a full update.
 */
async function maybeAutoRefreshQuotes() {
  const state = await getLocalState();
  const watchlist = state.watchlist || [];
  if (!watchlist.length) return;

  const missing = watchlist.filter((ticker) => {
    const q = quoteCache[ticker];
    return !q || (q.price == null && !q.error);
  });
  if (missing.length) {
    void refreshQuotes({ quiet: true, tickers: missing });
    return;
  }

  const ageMs = quotesUpdatedAt
    ? Date.now() - quotesUpdatedAt.getTime()
    : Number.POSITIVE_INFINITY;
  if (ageMs < QUOTE_FRESH_MS) return;

  void refreshQuotes({ quiet: true });
}

function updateQuotesUpdatedLabel() {
  if (!els.quotesUpdated) return;
  if (!quotesUpdatedAt) {
    els.quotesUpdated.textContent = "Not updated yet";
    return;
  }
  const sec = Math.max(0, Math.floor((Date.now() - quotesUpdatedAt.getTime()) / 1000));
  if (sec < 15) {
    els.quotesUpdated.textContent = "Updated just now";
  } else if (sec < 60) {
    els.quotesUpdated.textContent = `Updated ${sec}s ago`;
  } else if (sec < 3600) {
    els.quotesUpdated.textContent = `Updated ${Math.floor(sec / 60)}m ago`;
  } else {
    els.quotesUpdated.textContent = `Updated ${Math.floor(sec / 3600)}h ago`;
  }
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
    els.addTime.hidden = false;
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

  els.addTime.hidden = list.length >= MAX_SEND_TIMES;
  els.addTime.disabled = list.length >= MAX_SEND_TIMES;
  els.addTime.textContent = "+ Add another time";
  els.addTime.title =
    list.length >= MAX_SEND_TIMES
      ? `Maximum ${MAX_SEND_TIMES} times per day`
      : "Add another send time";
}

async function refreshScheduleSummary() {
  const schedule = readScheduleFromDom();
  els.scheduleSummary.textContent = formatScheduleLabel(schedule);
  const hint = await expireDeliveryStatusHint(await getDeliveryStatusHint());
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

  // Column headers / sort only appear once there is at least one ticker.
  els.listHead.hidden = watchlist.length === 0;
  els.sortRow.hidden = watchlist.length === 0;

  if (!watchlist.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No tickers yet — add a symbol to start.";
    els.watchlist.appendChild(empty);
    updatePortfolioSummary([], {}, quotes);
    return;
  }

  const ordered = sortWatchlistTickers(watchlist, holdings, quotes);

  for (const ticker of ordered) {
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
    shares.step = "0.0001";
    shares.inputMode = "decimal";
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
    buyPrice.step = "0.01";
    buyPrice.inputMode = "decimal";
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
    meta.append(...buildQuoteMetaNodes(quote, ticker));

    li.append(row, meta);

    const pnl = buildPnLNode(lot, quote);
    if (pnl) li.appendChild(pnl);

    const ai = buildAiBlurbNode(ticker, quote);
    if (ai) li.appendChild(ai);

    els.watchlist.appendChild(li);
  }

  updatePortfolioSummary(watchlist, holdings, quotes);
}

async function rerenderWatchlist() {
  const state = await getLocalState();
  renderWatchlist(state.watchlist, state.holdings, quoteCache);
}

/**
 * @param {string[]} watchlist
 * @param {Record<string, { shares?: number|null, buyPrice?: number|null }>} holdings
 * @param {Record<string, QuoteSnapshot>} quotes
 * @returns {string[]}
 */
function sortWatchlistTickers(watchlist, holdings, quotes) {
  const list = [...watchlist];
  if (watchlistSort === "symbol") {
    return list.sort((a, b) => a.localeCompare(b));
  }
  if (watchlistSort === "grade") {
    return list.sort((a, b) => gradeRank(quotes[b]) - gradeRank(quotes[a]) || a.localeCompare(b));
  }
  // P&L: highest gain first; tickers without a position sink to the bottom.
  return list.sort((a, b) => {
    const pa = computePnL(holdings[a], quotes[a]);
    const pb = computePnL(holdings[b], quotes[b]);
    const va = pa ? pa.pnlPct : Number.NEGATIVE_INFINITY;
    const vb = pb ? pb.pnlPct : Number.NEGATIVE_INFINITY;
    if (vb !== va) return vb - va;
    return a.localeCompare(b);
  });
}

/** @param {QuoteSnapshot|null|undefined} quote */
function gradeRank(quote) {
  if (typeof quote?.score === "number") return quote.score;
  const g = String(quote?.grade || quote?.verdict || "").toUpperCase();
  if (g.includes("STRONG")) return 4;
  if (g.includes("HOLD")) return 3;
  if (g.includes("AVOID")) return 1;
  return 0;
}

/**
 * @param {{ shares?: number|null, buyPrice?: number|null }} lot
 * @param {QuoteSnapshot|null|undefined} quote
 * @returns {{ value: number, cost: number, pnl: number, pnlPct: number, currency: string }|null}
 */
function computePnL(lot, quote) {
  const shares = Number(lot?.shares);
  const buy = Number(lot?.buyPrice);
  const price = quote?.price;
  if (!Number.isFinite(shares) || shares <= 0) return null;
  if (!Number.isFinite(buy) || buy <= 0) return null;
  if (typeof price !== "number" || !Number.isFinite(price)) return null;
  const value = shares * price;
  const cost = shares * buy;
  if (cost <= 0) return null;
  const pnl = value - cost;
  return {
    value,
    cost,
    pnl,
    pnlPct: (pnl / cost) * 100,
    currency: String(quote?.currency || "USD"),
  };
}

/**
 * Sum local lots by quote currency — never mixes CAD and USD without FX.
 * @param {string[]} watchlist
 * @param {Record<string, { shares?: number|null, buyPrice?: number|null }>} holdings
 * @param {Record<string, QuoteSnapshot>} quotes
 * @returns {Record<string, { value: number, cost: number, pnl: number, positions: number }>}
 */
function computePortfolioTotals(watchlist, holdings, quotes) {
  /** @type {Record<string, { value: number, cost: number, pnl: number, positions: number }>} */
  const byCur = {};
  for (const ticker of watchlist || []) {
    const stats = computePnL(holdings[ticker], quotes[ticker]);
    if (!stats) continue;
    const cur = stats.currency || "USD";
    if (!byCur[cur]) {
      byCur[cur] = { value: 0, cost: 0, pnl: 0, positions: 0 };
    }
    byCur[cur].value += stats.value;
    byCur[cur].cost += stats.cost;
    byCur[cur].pnl += stats.pnl;
    byCur[cur].positions += 1;
  }
  return byCur;
}

/**
 * Sticky banner: per-currency totals from on-device lots only.
 * @param {string[]} watchlist
 * @param {Record<string, { shares?: number|null, buyPrice?: number|null }>} holdings
 * @param {Record<string, QuoteSnapshot>} quotes
 */
function updatePortfolioSummary(watchlist, holdings = {}, quotes = quoteCache) {
  if (!els.portfolioSummary) return;
  const byCur = computePortfolioTotals(watchlist, holdings, quotes);
  const currencies = Object.keys(byCur).sort((a, b) => {
    if (a === "USD") return -1;
    if (b === "USD") return 1;
    if (a === "CAD") return -1;
    if (b === "CAD") return 1;
    return a.localeCompare(b);
  });

  els.portfolioSummary.innerHTML = "";
  if (!currencies.length) {
    els.portfolioSummary.hidden = true;
    return;
  }

  els.portfolioSummary.hidden = false;

  const label = document.createElement("div");
  label.className = "portfolio-summary-label";
  label.textContent = "Portfolio (local lots)";
  els.portfolioSummary.appendChild(label);

  for (const cur of currencies) {
    const t = byCur[cur];
    const pct = t.cost > 0 ? (t.pnl / t.cost) * 100 : 0;
    const sign = t.pnl >= 0 ? "+" : "−";
    const row = document.createElement("div");
    row.className =
      "portfolio-summary-row" + (t.pnl >= 0 ? " is-gain" : " is-loss");
    row.textContent =
      `${cur}  Value ${formatMoney(t.value)} · ` +
      `Profit/loss ${sign}${formatMoney(Math.abs(t.pnl))} (${sign}${Math.abs(pct).toFixed(1)}%)`;
    els.portfolioSummary.appendChild(row);
  }

  if (currencies.length > 1) {
    const note = document.createElement("p");
    note.className = "portfolio-summary-note";
    note.textContent = "CAD and USD kept separate — no FX conversion.";
    els.portfolioSummary.appendChild(note);
  }
}

/**
 * @param {{ shares?: number|null, buyPrice?: number|null }} lot
 * @param {QuoteSnapshot|null|undefined} quote
 * @returns {HTMLElement|null}
 */
function buildPnLNode(lot, quote) {
  const stats = computePnL(lot, quote);
  if (!stats) return null;
  const el = document.createElement("p");
  el.className = "position-pnl";
  const sign = stats.pnl >= 0 ? "+" : "−";
  const absPnl = Math.abs(stats.pnl);
  const absPct = Math.abs(stats.pnlPct);
  el.classList.add(stats.pnl >= 0 ? "is-gain" : "is-loss");
  el.textContent =
    `Value ${formatMoney(stats.value)} ${stats.currency} · ` +
    `Profit/loss ${sign}${formatMoney(absPnl)} (${sign}${absPct.toFixed(1)}%)`;
  return el;
}

/**
 * Update one card's P&L line without rebuilding inputs (preserves decimal typing).
 * @param {string} ticker
 * @param {{ shares?: number|null, buyPrice?: number|null }} lot
 * @param {QuoteSnapshot|null|undefined} quote
 */
function patchPnL(ticker, lot, quote) {
  const card = els.watchlist.querySelector(
    `.ticker-card[data-ticker="${CSS.escape(ticker)}"]`
  );
  if (!(card instanceof HTMLElement)) return;
  const existing = card.querySelector(".position-pnl");
  const next = buildPnLNode(lot, quote);
  if (!next) {
    existing?.remove();
    return;
  }
  if (existing) existing.replaceWith(next);
  else {
    const ai = card.querySelector(".ai-blurb");
    if (ai) card.insertBefore(next, ai);
    else card.appendChild(next);
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
function buildQuoteMetaNodes(quote, ticker = "") {
  const pending =
    quotesPending.has(ticker) ||
    (quotesLoading && (!quote || (quote.price == null && !quote.error)));
  if (pending) {
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
    err.textContent = "Unknown or invalid symbol";
    err.title = String(quote.error);
    return [err];
  }

  const priceEl = document.createElement("span");
  priceEl.className = "quote-price";
  if (typeof quote.price === "number") {
    const cur = quote.currency || "USD";
    priceEl.textContent = `Price ${formatPrice(quote.price)} ${cur}`;
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

/** Money totals / P&L — always 2 decimal places. */
function formatMoney(value) {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * True when a quote has enough trend data that the grade won't jump later.
 * @param {QuoteSnapshot|null|undefined} quote
 */
function isGradeStable(quote) {
  if (!quote || quote.price == null) return false;
  if (quote.error && quote.price == null) return false;
  return quote.aboveSma200 === true || quote.aboveSma200 === false;
}

/**
 * Prefer a stable prior grade over a fresh incomplete Yahoo response.
 * @param {QuoteSnapshot|null|undefined} prev
 * @param {QuoteSnapshot|null|undefined} next
 * @returns {QuoteSnapshot|null|undefined}
 */
function mergeQuoteSnapshot(prev, next) {
  if (!next) return prev;
  if (!prev) return next;
  if (isGradeStable(next)) return next;
  if (isGradeStable(prev)) {
    return {
      ...prev,
      price: next.price ?? prev.price,
      currency: next.currency || prev.currency,
      asOf: next.asOf || prev.asOf,
    };
  }
  return next;
}

/** Serialize quote refreshes so add/refresh don't race the pending set. */
let quotesRefreshChain = Promise.resolve();

/**
 * Fetch live yfinance snapshot + grades one ticker at a time (progressive UI).
 * Pass `tickers` to refresh only those symbols (e.g. after Add Ticker).
 * @param {{ quiet?: boolean, tickers?: string[], skipStatus?: boolean }} [opts]
 * @returns {Promise<{ okCount: number, failed: string[] }>}
 */
function refreshQuotes(opts = {}) {
  const run = () => refreshQuotesInternal(opts);
  const next = quotesRefreshChain.then(run, run);
  quotesRefreshChain = next.catch(() => ({ okCount: 0, failed: [] }));
  return next;
}

/**
 * @param {{ quiet?: boolean, tickers?: string[], skipStatus?: boolean }} [opts]
 * @returns {Promise<{ okCount: number, failed: string[] }>}
 */
async function refreshQuotesInternal(opts = {}) {
  const quiet = Boolean(opts.quiet);
  const skipStatus = Boolean(opts.skipStatus);
  const onlyTickers = Array.isArray(opts.tickers)
    ? opts.tickers.map((t) => String(t || "").trim().toUpperCase()).filter(Boolean)
    : null;
  const state = await getLocalState();
  const watchlist = state.watchlist || [];
  const isPartial = Boolean(onlyTickers?.length);
  const targets = isPartial
    ? onlyTickers.filter((t) => watchlist.includes(t))
    : watchlist.slice();

  if (!watchlist.length) {
    quoteCache = {};
    quotesPending = new Set();
    quotesLoading = false;
    await setCachedQuotes({});
    if (!quiet && !skipStatus) {
      setStatus("Add a ticker before refreshing quotes.", "warn", "watchlist", "error");
    }
    return { okCount: 0, failed: [] };
  }

  if (!targets.length) return { okCount: 0, failed: [] };

  if (!isPartial) {
    quotesLoading = true;
    aiExplainCache = {};
    aiExplainGeneration += 1;
    updateExplainButtonLabel(0);
    els.refreshQuotes.disabled = true;
    els.refreshQuotes.classList.add("is-loading");
    els.refreshQuotes.setAttribute("aria-busy", "true");
    els.refreshQuotes.textContent = "Loading…";
    quotesPending = new Set(targets);
  } else {
    for (const t of targets) {
      quotesPending.add(t);
      delete aiExplainCache[t];
    }
  }

  renderWatchlist(watchlist, state.holdings, quoteCache);
  if (!quiet && !skipStatus) {
    setStatus(
      isPartial
        ? `Fetching ${targets[0]}…`
        : `Fetching ${targets.length} ticker${targets.length === 1 ? "" : "s"}…`,
      "info",
      "watchlist",
      "persistent"
    );
  }

  let okCount = 0;
  /** @type {string[]} */
  const failed = [];
  try {
    // Chunked parallel fetch: split tickers into small batches (4 each),
    // fire all chunks concurrently. As each chunk resolves, immediately
    // reveal those tickers — so first results appear in ~4s, not 30s+.
    const CHUNK_SIZE = 4;
    const chunks = [];
    for (let i = 0; i < targets.length; i += CHUNK_SIZE) {
      chunks.push(targets.slice(i, i + CHUNK_SIZE));
    }

    // Process all chunks in parallel, reveal results as each completes.
    let revealed = 0;
    const chunkPromises = chunks.map((chunk, chunkIdx) =>
      fetchWatchlistSnapshot(chunk)
        .then((data) => ({ chunk, data, error: null }))
        .catch((error) => ({ chunk, data: null, error }))
    );

    // Use Promise.allSettled-like pattern: race all promises and process
    // as each resolves (earliest-first) for true streaming UX.
    const pending = chunkPromises.map((p, i) =>
      p.then((result) => ({ ...result, idx: i }))
    );
    const remaining = [...pending];

    while (remaining.length > 0) {
      const resolved = await Promise.race(remaining);
      // Remove the resolved promise from the remaining set
      const resolvedIdx = remaining.findIndex(
        (p) => p === pending[resolved.idx]
      );
      if (resolvedIdx !== -1) remaining.splice(resolvedIdx, 1);

      // Process this chunk's results
      const { chunk, data, error } = resolved;
      for (const ticker of chunk) {
        let quote = null;
        if (error) {
          quote = {
            ticker,
            price: null,
            error: error instanceof Error ? error.message : "fetch_failed",
          };
        } else {
          quote = (data?.quotes || []).find((q) => q?.ticker === ticker) ||
            { ticker, price: null, error: "empty_snapshot" };
        }

        const merged = mergeQuoteSnapshot(quoteCache[ticker], quote);
        quoteCache[ticker] = /** @type {QuoteSnapshot} */ (merged);
        if (merged?.price != null) okCount += 1;
        else failed.push(ticker);

        quotesPending.delete(ticker);
        revealed += 1;

        if (!quiet && !skipStatus) {
          setStatus(
            `Loaded ${revealed} / ${targets.length}`,
            "info",
            "watchlist",
            "persistent"
          );
        }
      }
      // Re-render after each chunk for immediate visual feedback
      renderWatchlist(watchlist, state.holdings, quoteCache);
    }

    await setCachedQuotes(
      /** @type {Record<string, Record<string, unknown>>} */ (quoteCache)
    );
    quotesUpdatedAt = new Date();
    updateQuotesUpdatedLabel();

    if (!skipStatus && (!quiet || isPartial)) {
      setStatus(
        isPartial
          ? okCount
            ? `Updated ${targets[0]}.`
            : `Could not update ${targets[0]}.`
          : `Updated ${okCount} quote${okCount === 1 ? "" : "s"}.`,
        okCount ? "ok" : "warn",
        "watchlist",
        "transient"
      );
    }
  } finally {
    for (const t of targets) quotesPending.delete(t);
    if (!isPartial) {
      quotesLoading = false;
      quotesPending = new Set();
      els.refreshQuotes.disabled = false;
      els.refreshQuotes.classList.remove("is-loading");
      els.refreshQuotes.removeAttribute("aria-busy");
      els.refreshQuotes.textContent = "Refresh";
    }
    renderWatchlist(watchlist, state.holdings, quoteCache);
  }

  return { okCount, failed };
}

/**
 * Optional: explain only tickers that still need a blurb (keeps successes on retry).
 * Progressive: updates UI after each chunk so explanations stream in visually.
 */
async function onExplainGrades() {
  const key =
    els.geminiKey.value.trim() || String((await getGeminiKey()) || "").trim();
  if (!key) {
    setStatus("Paste a Gemini API key first.", "warn", "ai", "error");
    els.geminiKey.focus();
    return;
  }

  const state = await getLocalState();
  const watchlist = state.watchlist || [];
  const quotes = watchlist
    .map((t) => quoteCache[t])
    .filter((q) => q && !(q.error && q.price == null));
  if (!quotes.length) {
    setStatus("Refresh quotes first, then explain grades.", "warn", "ai", "error");
    return;
  }

  /** @type {Array<Record<string, unknown>>} */
  const pending = [];
  /** @type {Record<string, { status: 'loading'|'ok'|'error', text: string }>} */
  const nextCache = {};
  for (const q of quotes) {
    const ticker = q.ticker;
    const prev = aiExplainCache[ticker];
    if (prev?.status === "ok" && prev.text) {
      nextCache[ticker] = prev;
    } else {
      pending.push(/** @type {Record<string, unknown>} */ (q));
      nextCache[ticker] = { status: "loading", text: "" };
    }
  }

  const alreadyOk = quotes.length - pending.length;
  if (!pending.length) {
    setStatus(
      `All ${quotes.length} grades already explained. Refresh quotes to clear.`,
      "ok",
      "ai",
      "transient"
    );
    updateExplainButtonLabel(0);
    return;
  }

  // Sort pending by the user's current watchlist sort order so explanations
  // stream in from top to bottom as they appear on screen.
  const sortedTickers = sortWatchlistTickers(
    pending.map((q) => String(q.ticker || "")),
    state.holdings,
    quoteCache
  );
  const pendingSorted = sortedTickers
    .map((t) => pending.find((q) => String(q.ticker || "") === t))
    .filter(Boolean);

  const generation = ++aiExplainGeneration;
  aiExplainCache = nextCache;
  renderWatchlist(watchlist, state.holdings, quoteCache);
  updateExplainButtonLabel(pendingSorted.length);

  els.explainGrades.disabled = true;
  setStatus(
    alreadyOk
      ? `Explaining ${pendingSorted.length} remaining grade${pendingSorted.length === 1 ? "" : "s"}…`
      : `Gemini explaining ${pendingSorted.length} grade${pendingSorted.length === 1 ? "" : "s"}…`,
    "info",
    "ai",
    "persistent"
  );

  let calls = 0;
  const chunkSize = 2; // EXPLAIN_CHUNK_SIZE

  try {
    for (let i = 0; i < pendingSorted.length; i += chunkSize) {
      if (generation !== aiExplainGeneration) return;
      const chunk = pendingSorted.slice(i, i + chunkSize);

      try {
        const map = await explainQuotesOnce(key, chunk);
        calls += 1;
        if (generation !== aiExplainGeneration) return;

        for (const q of chunk) {
          const ticker = String(q.ticker || "");
          const text = map[ticker] || map[ticker.toUpperCase()];
          aiExplainCache[ticker] = text
            ? { status: "ok", text }
            : { status: "error", text: "No explanation returned." };
          patchAiBlurb(ticker);
        }
      } catch (chunkError) {
        calls += 1;
        if (generation !== aiExplainGeneration) return;
        // Retry each ticker individually for this failed chunk.
        for (const q of chunk) {
          const ticker = String(q.ticker || "");
          try {
            const map = await explainQuotesOnce(key, [q]);
            calls += 1;
            if (generation !== aiExplainGeneration) return;
            const text = map[ticker] || map[ticker.toUpperCase()];
            aiExplainCache[ticker] = text
              ? { status: "ok", text }
              : { status: "error", text: "No explanation returned." };
          } catch {
            calls += 1;
            aiExplainCache[ticker] = {
              status: "error",
              text: formatGeminiError(chunkError),
            };
          }
          patchAiBlurb(ticker);
        }
      }

      // Update progress status after each chunk.
      const okCount = Object.values(aiExplainCache).filter(
        (e) => e?.status === "ok"
      ).length;
      const stillMissing = quotes.length - okCount;
      updateExplainButtonLabel(stillMissing);
      setStatus(
        `Explained ${okCount} / ${quotes.length} grades…`,
        "info",
        "ai",
        "persistent"
      );
    }

    if (generation !== aiExplainGeneration) return;

    const okCount = Object.values(aiExplainCache).filter(
      (e) => e?.status === "ok"
    ).length;
    const total = quotes.length;
    const stillMissing = total - okCount;
    const callLabel = `${calls} Gemini call${calls === 1 ? "" : "s"}`;
    updateExplainButtonLabel(stillMissing);

    if (stillMissing === 0) {
      setStatus(
        `AI explained ${okCount} grade${okCount === 1 ? "" : "s"} (${callLabel}).`,
        "ok",
        "ai",
        "transient"
      );
    } else {
      setStatus(
        `AI explained ${okCount}/${total} grades (${callLabel}). Press again for the ${stillMissing} left.`,
        "warn",
        "ai",
        "error"
      );
    }
  } catch (error) {
    console.error("[AI explain progressive] failed", error);
    if (generation !== aiExplainGeneration) return;
    const msg = formatGeminiError(error);
    for (const q of pendingSorted) {
      const ticker = String(q.ticker || "");
      if (!aiExplainCache[ticker] || aiExplainCache[ticker].status !== "ok") {
        aiExplainCache[ticker] = { status: "error", text: msg };
        patchAiBlurb(ticker);
      }
    }
    updateExplainButtonLabel(pendingSorted.length);
    setStatus(msg, "error", "ai", "error");
  } finally {
    els.explainGrades.disabled = false;
  }
}

/** @param {number} missingCount */
function updateExplainButtonLabel(missingCount) {
  if (missingCount > 0 && missingCount < 99) {
    const allMissing =
      Object.keys(aiExplainCache).length === 0 ||
      Object.values(aiExplainCache).every((e) => e?.status !== "ok");
    els.explainGrades.textContent = allMissing
      ? "Explain watchlist grades"
      : `Explain remaining (${missingCount})`;
  } else {
    els.explainGrades.textContent = "Explain watchlist grades";
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
    setStatus("Enter a ticker symbol first.", "warn", "watchlistAdd", "error");
    return;
  }

  const state = await getLocalState();
  const watchlist = [...state.watchlist];

  if (watchlist.includes(ticker)) {
    setStatus(`${ticker} is already on your watchlist.`, "warn", "watchlistAdd", "error");
    return;
  }
  if (watchlist.length >= MAX_WATCHLIST) {
    setStatus(
      `Watchlist capped at ${MAX_WATCHLIST} tickers.`,
      "warn",
      "watchlistAdd",
      "error"
    );
    return;
  }

  watchlist.push(ticker);
  const result = await setWatchlist(watchlist);
  els.tickerInput.value = "";
  els.tickerInput.focus();
  renderWatchlist(result.watchlist, result.holdings, quoteCache);
  setStatus(`Checking ${ticker}…`, "info", "watchlistAdd", "persistent");

  // Confirm Yahoo has a price before keeping the symbol.
  const fetchResult = await refreshQuotes({
    quiet: true,
    tickers: [ticker],
    skipStatus: true,
  });
  const quote = quoteCache[ticker];
  const hasPrice = quote && typeof quote.price === "number" && Number.isFinite(quote.price);

  if (!hasPrice) {
    // Distinguish a network/API failure (Render cold start, timeout, 5xx) from
    // a genuinely unknown ticker. Network failures keep the ticker; bad tickers are removed.
    const errorStr = String(quote?.error || "").toLowerCase();
    // Also treat price_unavailable on Indian tickers as non-fatal:
    // Render's US servers are often geo-blocked by Yahoo Finance for .NS/.BO data.
    const isIndianTicker = ticker.endsWith(".NS") || ticker.endsWith(".BO");
    const isNetworkError =
      errorStr.includes("fetch_failed") ||
      errorStr.includes("cannot reach") ||
      errorStr.includes("network") ||
      errorStr.includes("timeout") ||
      errorStr.includes("503") ||
      errorStr.includes("502") ||
      errorStr.includes("500") ||
      errorStr.includes("failed to fetch") ||
      errorStr.includes("awake") ||
      // price_unavailable / empty_snapshot: keep Indian tickers (Render geo-block from Yahoo)
      // but still reject genuinely bad US/CA tickers that return no price.
      (isIndianTicker && (errorStr.includes("price_unavailable") || errorStr.includes("empty_snapshot") || errorStr === "")) ||
      (!quote && (fetchResult?.failed || []).includes(ticker));

    if (isNetworkError) {
      setStatus(
        `${ticker} added. Live price unavailable right now (API may be waking up) — grades will load on next refresh.`,
        "warn",
        "watchlistAdd",
        "persistent"
      );
      return;
    }

    const pruned = (await getLocalState()).watchlist.filter((t) => t !== ticker);
    const after = await setWatchlist(pruned);
    delete quoteCache[ticker];
    delete aiExplainCache[ticker];
    await setCachedQuotes(
      /** @type {Record<string, Record<string, unknown>>} */ (quoteCache)
    );
    renderWatchlist(after.watchlist, after.holdings, quoteCache);
    els.tickerInput.value = ticker;
    els.tickerInput.focus();
    setStatus(
      `${ticker} isn’t a valid tradeable symbol (no live price). Not added — check the spelling.`,
      "warn",
      "watchlistAdd",
      "error"
    );
    return;
  }

  setStatus(`Added ${ticker}.`, "ok", "watchlistAdd", "transient");
}

/**
 * @param {string} ticker
 */
async function onRemoveTicker(ticker) {
  const card = els.watchlist.querySelector(
    `.ticker-card[data-ticker="${CSS.escape(ticker)}"]`
  );
  const removeIndex = card
    ? [...els.watchlist.children].indexOf(card)
    : -1;

  const state = await getLocalState();
  const watchlist = state.watchlist.filter((item) => item !== ticker);
  const result = await setWatchlist(watchlist);
  delete quoteCache[ticker];
  delete aiExplainCache[ticker];
  renderWatchlist(result.watchlist, result.holdings, quoteCache);
  showRemovedFlash(ticker, removeIndex);
}

/**
 * Brief confirmation in the slot the ticker occupied (not under the whole list).
 * @param {string} ticker
 * @param {number} index
 */
function showRemovedFlash(ticker, index) {
  els.watchlist
    .querySelectorAll(".ticker-removed-flash")
    .forEach((el) => el.remove());

  const flash = document.createElement("li");
  flash.className = "ticker-removed-flash";
  flash.setAttribute("role", "status");
  flash.textContent = `Removed ${ticker}.`;

  const cards = [...els.watchlist.querySelectorAll(".ticker-card")];
  const empty = els.watchlist.querySelector("li.empty");

  if (index >= 0 && index < cards.length) {
    els.watchlist.insertBefore(flash, cards[index]);
  } else if (cards.length && index >= cards.length) {
    els.watchlist.appendChild(flash);
  } else if (empty) {
    els.watchlist.insertBefore(flash, empty);
  } else {
    els.watchlist.appendChild(flash);
  }

  flash.scrollIntoView({ block: "nearest", behavior: "smooth" });
  requestAnimationFrame(() => flash.classList.add("is-visible"));

  window.setTimeout(() => {
    flash.classList.add("is-fading-out");
    window.setTimeout(() => flash.remove(), 280);
  }, 2200);
}

// ---------------------------------------------------------------------------
// Private holdings (client-side only)
// ---------------------------------------------------------------------------

/** @param {Event} event */
function onHoldingsInput(event) {
  const target = /** @type {HTMLElement} */ (event.target);
  if (!(target instanceof HTMLInputElement) || !target.dataset.ticker) return;

  holdingsLastEditedTicker = target.dataset.ticker || null;
  window.clearTimeout(holdingsSaveTimer);
  holdingsSaveTimer = window.setTimeout(() => {
    void persistHoldingsFromDom(holdingsLastEditedTicker);
  }, 280);
}

/**
 * Collect inline Shares / Avg buy inputs and write to chrome.storage.local.
 * This path never builds a cloud payload.
 * @param {string|null} [focusTicker] ticker whose card should show the save flash
 */
async function persistHoldingsFromDom(focusTicker = null) {
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
  // Patch P&L only — full re-render would wipe in-progress decimals like "29.".
  for (const ticker of Object.keys(merged)) {
    patchPnL(ticker, merged[ticker] || {}, quoteCache[ticker]);
  }
  const state = await getLocalState();
  updatePortfolioSummary(state.watchlist, merged, quoteCache);
  if (focusTicker) showLotsSavedFlash(focusTicker);
}

/**
 * Inline save confirmation on the card being edited.
 * @param {string} ticker
 */
function showLotsSavedFlash(ticker) {
  const card = els.watchlist.querySelector(
    `.ticker-card[data-ticker="${CSS.escape(ticker)}"]`
  );
  if (!(card instanceof HTMLElement)) return;

  card.querySelectorAll(".lots-saved-flash").forEach((el) => el.remove());
  els.watchlist
    .querySelectorAll(".lots-saved-flash")
    .forEach((el) => el.remove());

  const flash = document.createElement("p");
  flash.className = "lots-saved-flash";
  flash.setAttribute("role", "status");
  flash.textContent = "Private lots saved on this device only.";

  const pnl = card.querySelector(".position-pnl");
  const ai = card.querySelector(".ai-blurb");
  if (pnl) pnl.after(flash);
  else if (ai) card.insertBefore(flash, ai);
  else card.appendChild(flash);

  requestAnimationFrame(() => flash.classList.add("is-visible"));
  window.setTimeout(() => {
    flash.classList.add("is-fading-out");
    window.setTimeout(() => flash.remove(), 280);
  }, 2200);
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
  if (lower.includes("did not return") || lower.includes("usable grade")) {
    return "Gemini reply was unreadable. Try explaining grades again in a moment.";
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

    setStatus(
      "Saving… If you’re in a send window, waiting for delivery confirmation.",
      "info",
      "subscribe",
      "persistent"
    );
    await setDeliveryStatusHint({
      status: "sending",
      at: new Date().toISOString(),
      detail: "",
    });
    await refreshScheduleSummary();

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
    const sendStatus = String(response?.report_send_status || "not_due");
    let message = `Saved — ${scheduleLabel} → ${outbound.email}`;
    let statusKind = /** @type {"ok"|"warn"|"error"} */ ("ok");

    if (response?.report_sent_now || sendStatus === "sent") {
      message = `Saved — email sent to ${outbound.email}. Check inbox (and spam).`;
    } else if (sendStatus === "failed") {
      message =
        `Saved — email send failed. Check spam / Resend config, then try Save & Subscribe again while the window is open.`;
      statusKind = "error";
    } else if (sendStatus === "sending") {
      // Legacy API: should not happen after sync-send deploy.
      message = `Saved — send was queued; confirm delivery in your inbox.`;
      statusKind = "warn";
    } else if (sendStatus === "daily_cap") {
      message = `Saved — daily email cap reached (max 2 today).`;
      statusKind = "warn";
    } else if (sendStatus === "already_sent") {
      message = `Saved — already sent for this time slot today.`;
    } else {
      message = `Saved — ${scheduleLabel} → ${outbound.email}. Next send follows your schedule.`;
    }

    await setDeliveryStatusHint({
      status: sendStatus === "sending" ? "none" : sendStatus,
      at: new Date().toISOString(),
      detail: "",
    });
    await refreshScheduleSummary();

    setStatus(message, statusKind, "subscribe", "persistent");
  } catch (error) {
    console.error("[SUBSCRIBE] failed", error);
    setStatus(error?.message || "Subscribe failed", "error", "subscribe", "error");
  } finally {
    els.subscribe.disabled = false;
  }
}
