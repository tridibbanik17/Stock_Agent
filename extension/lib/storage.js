/**
 * Two-tier Chrome storage layer
 * -----------------------------
 * PRIVATE (chrome.storage.local — never transmitted):
 *   holdings: { [ticker: string]: { shares: number|null, buyPrice: number|null } }
 *   geminiApiKey: string
 *   autoAnalyze: boolean
 *
 * DELIVERY / CLOUD-ELIGIBLE (cached locally; may sync later):
 *   watchlist: string[]  (max 25)
 *   delivery: { email, schedule: ScheduleConfig, enabled }
 *   userId: string|null
 *
 * ScheduleConfig.days uses JS getDay() convention: 0=Sun … 6=Sat.
 */

/** @typedef {{ shares: number|null, buyPrice: number|null }} HoldingLot */
/** @typedef {Record<string, HoldingLot>} HoldingsMap */
/** @typedef {'daily'|'weekdays'|'weekly'|'custom'} ScheduleFrequency */
/** @typedef {{
 *   frequency: ScheduleFrequency,
 *   days: number[],
 *   times: string[],
 *   timezone: string
 * }} ScheduleConfig */

/** Soft cap so a single user cannot enqueue an unbounded fan-out. */
export const MAX_SEND_TIMES = 8;
/** @typedef {{ email: string, schedule: ScheduleConfig, enabled: boolean }} DeliveryPrefs */
/** @typedef {{
 *   holdings: HoldingsMap,
 *   geminiApiKey: string,
 *   autoAnalyze: boolean,
 *   watchlist: string[],
 *   delivery: DeliveryPrefs,
 *   userId: string|null
 * }} LocalState */
/** @typedef {{
 *   email: string,
 *   watchlist: string[],
 *   schedule: ScheduleConfig,
 *   enabled: boolean,
 *   userId: string|null
 * }} CloudPayload */

export const MAX_WATCHLIST = 25;

/** @type {ReadonlyArray<{ value: number, short: string, label: string }>} */
export const WEEKDAY_OPTIONS = Object.freeze([
  { value: 0, short: "Sun", label: "Sunday" },
  { value: 1, short: "Mon", label: "Monday" },
  { value: 2, short: "Tue", label: "Tuesday" },
  { value: 3, short: "Wed", label: "Wednesday" },
  { value: 4, short: "Thu", label: "Thursday" },
  { value: 5, short: "Fri", label: "Friday" },
  { value: 6, short: "Sat", label: "Saturday" },
]);

/** Migrate kickoff-era preset strings → full ScheduleConfig. */
const LEGACY_PRESET_MAP = Object.freeze({
  saturday_morning: { frequency: "weekly", days: [6], time: "09:00" },
  weekdays_9am: { frequency: "weekdays", days: [1, 2, 3, 4, 5], time: "09:00" },
  daily_9am: { frequency: "daily", days: [0, 1, 2, 3, 4, 5, 6], time: "09:00" },
});

/** @deprecated Prefer ScheduleConfig objects. */
export const SCHEDULE_PRESETS = LEGACY_PRESET_MAP;

const STORAGE_KEYS = Object.freeze({
  holdings: "holdings",
  geminiApiKey: "geminiApiKey",
  autoAnalyze: "autoAnalyze",
  watchlist: "watchlist",
  delivery: "delivery",
  userId: "userId",
});

/** @returns {string} */
export function detectTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** @returns {ScheduleConfig} */
export function defaultSchedule() {
  return {
    frequency: "weekly",
    days: [6],
    times: ["09:00"],
    timezone: detectTimezone(),
  };
}

/**
 * Infer frequency label from the selected day set (UI no longer asks for it).
 * @param {number[]} days
 * @returns {ScheduleFrequency}
 */
export function inferFrequency(days) {
  const sorted = [...new Set(days)]
    .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
    .sort((a, b) => a - b);
  if (!sorted.length) return "custom";
  if (sorted.length === 7) return "daily";
  if (sorted.length === 5 && sorted.join(",") === "1,2,3,4,5") return "weekdays";
  if (sorted.length === 1) return "weekly";
  return "custom";
}

/**
 * Normalize any legacy string / partial object into a valid ScheduleConfig.
 * Accepts legacy `time: "09:00"` and upgrades it to `times: ["09:00"]`.
 * Days are source of truth; frequency is derived.
 * @param {unknown} raw
 * @returns {ScheduleConfig}
 */
export function normalizeSchedule(raw) {
  const base = defaultSchedule();

  if (typeof raw === "string" && LEGACY_PRESET_MAP[raw]) {
    const legacy = LEGACY_PRESET_MAP[raw];
    return normalizeSchedule({ ...legacy, timezone: base.timezone });
  }

  if (!isPlainObject(raw)) return base;

  const daysProvided = Array.isArray(raw.days);
  const timesProvided = Array.isArray(raw.times) || raw.time != null;

  let days = daysProvided
    ? raw.days.map((d) => Number(d)).filter((d) => d >= 0 && d <= 6 && Number.isInteger(d))
    : [];
  days = [...new Set(days)].sort((a, b) => a - b);

  // Legacy payloads that only stored frequency (no days array).
  if (!daysProvided && !days.length && typeof raw.frequency === "string") {
    if (raw.frequency === "daily") days = [0, 1, 2, 3, 4, 5, 6];
    else if (raw.frequency === "weekdays") days = [1, 2, 3, 4, 5];
    else if (raw.frequency === "weekly") days = [6];
  }
  // Empty selection is allowed in the UI; only default when days were omitted.
  if (!daysProvided && !days.length) days = [...base.days];

  const frequency = inferFrequency(days);

  /** @type {string[]} */
  let times = [];
  if (Array.isArray(raw.times)) {
    times = raw.times.map(normalizeTime).filter(Boolean);
  } else if (raw.time != null) {
    const single = normalizeTime(raw.time);
    if (single) times = [single];
  }
  times = [...new Set(times)].sort().slice(0, MAX_SEND_TIMES);
  if (!timesProvided && !times.length) times = [...base.times];

  const timezone =
    typeof raw.timezone === "string" && raw.timezone.trim()
      ? raw.timezone.trim()
      : detectTimezone();

  return { frequency, days, times, timezone };
}

/**
 * Human-readable summary for UI status lines.
 * @param {ScheduleConfig|unknown} schedule
 * @returns {string}
 */
export function formatScheduleLabel(schedule) {
  const cfg = normalizeSchedule(schedule);
  if (!cfg.days.length && !cfg.times.length) {
    return "Select at least one day and one send time";
  }
  if (!cfg.days.length) return "No days selected";
  if (!cfg.times.length) return "No send times selected";

  const timeLabel = formatTimesLabel(cfg.times);
  const dayNames = cfg.days
    .map((d) => WEEKDAY_OPTIONS.find((opt) => opt.value === d)?.short || String(d))
    .join(", ");

  switch (cfg.frequency) {
    case "daily":
      return `Every day at ${timeLabel}`;
    case "weekdays":
      return `Weekdays at ${timeLabel}`;
    case "weekly":
      return `Every ${WEEKDAY_OPTIONS.find((o) => o.value === cfg.days[0])?.label || "week"} at ${timeLabel}`;
    case "custom":
      return `${dayNames} at ${timeLabel}`;
    default:
      return `${dayNames} at ${timeLabel}`;
  }
}

/**
 * Split "HH:MM" into frictionless picker parts.
 * @param {string} timeHhMm
 * @returns {{ hour12: number, minute: number, period: 'AM'|'PM' }}
 */
export function splitTimeParts(timeHhMm) {
  const normalized = normalizeTime(timeHhMm) || "09:00";
  const [hh, mm] = normalized.split(":").map(Number);
  const period = hh >= 12 ? "PM" : "AM";
  const hour12 = hh % 12 || 12;
  const minute = Number.isInteger(mm) && mm >= 0 && mm <= 59 ? mm : 0;
  return { hour12, minute, period };
}

/**
 * Build "HH:MM" (24h) from picker parts.
 * @param {number} hour12
 * @param {number} minute
 * @param {'AM'|'PM'|string} period
 * @returns {string}
 */
export function joinTimeParts(hour12, minute, period) {
  let hour = Number(hour12);
  let min = Number(minute);
  if (!Number.isFinite(hour) || hour < 1 || hour > 12) hour = 9;
  if (!Number.isFinite(min) || min < 0 || min > 59) min = 0;
  min = Math.round(min);
  const isPm = String(period).toUpperCase() === "PM";
  if (hour === 12) hour = isPm ? 12 : 0;
  else if (isPm) hour += 12;
  return `${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

/**
 * Pick the next unused HH:MM so "Add another time" never no-ops.
 * @param {string[]} existing
 * @returns {string|null}
 */
export function suggestNextSendTime(existing) {
  const taken = new Set((existing || []).map(normalizeTime).filter(Boolean));
  const preferred = [
    "09:00",
    "12:00",
    "17:00",
    "07:00",
    "08:00",
    "18:00",
    "20:00",
    "06:30",
  ];
  for (const slot of preferred) {
    if (!taken.has(slot)) return slot;
  }
  for (let hour = 0; hour < 24; hour += 1) {
    for (let minute = 0; minute < 60; minute += 1) {
      const slot = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
      if (!taken.has(slot)) return slot;
    }
  }
  return null;
}

/** @type {LocalState} */
const DEFAULTS = Object.freeze({
  holdings: {},
  geminiApiKey: "",
  autoAnalyze: true,
  watchlist: [],
  delivery: Object.freeze({
    email: "",
    schedule: Object.freeze({
      frequency: "weekly",
      days: Object.freeze([6]),
      times: Object.freeze(["09:00"]),
      timezone: "UTC",
    }),
    enabled: false,
  }),
  userId: null,
});

/** Keys that must never appear on an outbound cloud payload. */
const FORBIDDEN_CLOUD_KEYS = Object.freeze([
  "holdings",
  "shares",
  "buyPrice",
  "avgBuyPrice",
  "geminiApiKey",
  "geminiKey",
  "apiKey",
  "autoAnalyze",
  "netWorth",
  "portfolio",
]);

// ---------------------------------------------------------------------------
// Holdings (PRIVATE)
// ---------------------------------------------------------------------------

/**
 * Read private share / buy-price lots from chrome.storage.local.
 * @returns {Promise<HoldingsMap>}
 */
export async function getHoldings() {
  const result = await chrome.storage.local.get(STORAGE_KEYS.holdings);
  const holdings = result[STORAGE_KEYS.holdings];
  return isPlainObject(holdings) ? /** @type {HoldingsMap} */ (holdings) : {};
}

/**
 * Overwrite private holdings map. Never sent over the network.
 * @param {HoldingsMap} holdings
 * @returns {Promise<HoldingsMap>}
 */
export async function setHoldings(holdings) {
  if (!isPlainObject(holdings)) {
    throw new TypeError("setHoldings expects an object keyed by ticker");
  }

  /** @type {HoldingsMap} */
  const sanitized = {};
  for (const [rawTicker, lot] of Object.entries(holdings)) {
    const ticker = normalizeTicker(rawTicker);
    if (!ticker || !isPlainObject(lot)) continue;
    sanitized[ticker] = {
      shares: toNullableNumber(lot.shares),
      buyPrice: toNullableNumber(lot.buyPrice),
    };
  }

  await chrome.storage.local.set({ [STORAGE_KEYS.holdings]: sanitized });
  return sanitized;
}

/** @deprecated Prefer setHoldings — kept for background.js compatibility. */
export const setPrivateHoldings = setHoldings;

// ---------------------------------------------------------------------------
// Gemini API key (PRIVATE — BYOK)
// ---------------------------------------------------------------------------

/**
 * @returns {Promise<string>}
 */
export async function getGeminiKey() {
  const result = await chrome.storage.local.get(STORAGE_KEYS.geminiApiKey);
  const key = result[STORAGE_KEYS.geminiApiKey];
  return typeof key === "string" ? key : "";
}

/**
 * Persist the user's Gemini key on-device only.
 * @param {string} geminiApiKey
 * @returns {Promise<string>}
 */
export async function setGeminiKey(geminiApiKey) {
  const value = String(geminiApiKey ?? "").trim();
  await chrome.storage.local.set({ [STORAGE_KEYS.geminiApiKey]: value });
  return value;
}

/** @deprecated Prefer setGeminiKey */
export const setGeminiApiKey = setGeminiKey;

/**
 * @returns {Promise<boolean>}
 */
export async function getAutoAnalyze() {
  const result = await chrome.storage.local.get(STORAGE_KEYS.autoAnalyze);
  if (typeof result[STORAGE_KEYS.autoAnalyze] === "boolean") {
    return result[STORAGE_KEYS.autoAnalyze];
  }
  return DEFAULTS.autoAnalyze;
}

/**
 * @param {boolean} enabled
 * @returns {Promise<boolean>}
 */
export async function setAutoAnalyze(enabled) {
  const value = Boolean(enabled);
  await chrome.storage.local.set({ [STORAGE_KEYS.autoAnalyze]: value });
  return value;
}

/**
 * Wipe all local extension settings (private + delivery cache).
 * Does not call any network endpoint.
 */
export async function clearAllLocalSettings() {
  await chrome.storage.local.remove(Object.values(STORAGE_KEYS));
}

// ---------------------------------------------------------------------------
// Watchlist + delivery prefs (cloud-eligible, still cached locally)
// ---------------------------------------------------------------------------

/**
 * @returns {Promise<string[]>}
 */
export async function getWatchlist() {
  const result = await chrome.storage.local.get(STORAGE_KEYS.watchlist);
  const list = result[STORAGE_KEYS.watchlist];
  if (!Array.isArray(list)) return [];
  return list.map(normalizeTicker).filter(Boolean).slice(0, MAX_WATCHLIST);
}

/**
 * Save watchlist and prune orphaned private holdings for removed tickers.
 * @param {string[]} watchlist
 * @returns {Promise<{ watchlist: string[], holdings: HoldingsMap }>}
 */
export async function setWatchlist(watchlist) {
  const cleaned = (Array.isArray(watchlist) ? watchlist : [])
    .map(normalizeTicker)
    .filter(Boolean)
    .filter((ticker, index, arr) => arr.indexOf(ticker) === index)
    .slice(0, MAX_WATCHLIST);

  await chrome.storage.local.set({ [STORAGE_KEYS.watchlist]: cleaned });

  // Keep private lots aligned with the visible watchlist (still local-only).
  const holdings = await getHoldings();
  /** @type {HoldingsMap} */
  const pruned = {};
  for (const ticker of cleaned) {
    if (holdings[ticker]) pruned[ticker] = holdings[ticker];
  }
  await setHoldings(pruned);

  return { watchlist: cleaned, holdings: pruned };
}

/**
 * @returns {Promise<DeliveryPrefs>}
 */
export async function getDelivery() {
  const result = await chrome.storage.local.get(STORAGE_KEYS.delivery);
  const delivery = result[STORAGE_KEYS.delivery];
  if (!isPlainObject(delivery)) {
    return {
      email: "",
      schedule: defaultSchedule(),
      enabled: false,
    };
  }
  return {
    email: typeof delivery.email === "string" ? delivery.email : "",
    schedule: normalizeSchedule(delivery.schedule),
    enabled: Boolean(delivery.enabled),
  };
}

/**
 * @param {Partial<DeliveryPrefs>} patch
 * @returns {Promise<DeliveryPrefs>}
 */
export async function setDelivery(patch) {
  const current = await getDelivery();
  /** @type {DeliveryPrefs} */
  const next = {
    email: String(patch.email ?? current.email).trim().toLowerCase(),
    schedule: normalizeSchedule(
      patch.schedule !== undefined ? patch.schedule : current.schedule
    ),
    enabled:
      typeof patch.enabled === "boolean" ? patch.enabled : current.enabled,
  };
  await chrome.storage.local.set({ [STORAGE_KEYS.delivery]: next });
  return next;
}

/**
 * Load the full local state bag for UI hydration.
 * @param {string[]|null} [keys]
 * @returns {Promise<LocalState>}
 */
export async function getLocalState(keys = null) {
  const requested =
    keys ??
    Object.values(STORAGE_KEYS);

  const stored = await chrome.storage.local.get(requested);

  return {
    holdings: isPlainObject(stored.holdings) ? stored.holdings : {},
    geminiApiKey:
      typeof stored.geminiApiKey === "string" ? stored.geminiApiKey : "",
    autoAnalyze:
      typeof stored.autoAnalyze === "boolean"
        ? stored.autoAnalyze
        : DEFAULTS.autoAnalyze,
    watchlist: Array.isArray(stored.watchlist)
      ? stored.watchlist.map(normalizeTicker).filter(Boolean).slice(0, MAX_WATCHLIST)
      : [],
    delivery: isPlainObject(stored.delivery)
      ? {
          email: String(stored.delivery.email || ""),
          schedule: normalizeSchedule(stored.delivery.schedule),
          enabled: Boolean(stored.delivery.enabled),
        }
      : {
          email: "",
          schedule: defaultSchedule(),
          enabled: false,
        },
    userId: typeof stored.userId === "string" ? stored.userId : null,
  };
}

/**
 * Cache cloud-eligible fields after a (future) successful API sync.
 * @param {{ watchlist?: string[], delivery?: DeliveryPrefs, userId?: string|null }} patch
 */
export async function cacheCloudProfile(patch) {
  /** @type {Record<string, unknown>} */
  const write = {};
  if (Array.isArray(patch.watchlist)) {
    write.watchlist = patch.watchlist
      .map(normalizeTicker)
      .filter(Boolean)
      .slice(0, MAX_WATCHLIST);
  }
  if (patch.delivery && isPlainObject(patch.delivery)) {
    write.delivery = {
      email: String(patch.delivery.email || "").trim().toLowerCase(),
      schedule: normalizeSchedule(patch.delivery.schedule),
      enabled: Boolean(patch.delivery.enabled),
    };
  }
  if ("userId" in patch) write.userId = patch.userId ?? null;
  if (Object.keys(write).length) await chrome.storage.local.set(write);
  return write;
}

// ---------------------------------------------------------------------------
// Cloud payload builders / privacy guards
// ---------------------------------------------------------------------------

/**
 * Build the ONLY object that may leave the browser.
 * Explicitly reconstructs from allow-listed fields — holdings & keys are omitted.
 *
 * @param {Partial<LocalState> & { delivery?: Partial<DeliveryPrefs>, watchlist?: string[] }} state
 * @returns {CloudPayload}
 */
export function buildCloudPayload(state) {
  const email = String(state?.delivery?.email ?? "").trim().toLowerCase();
  const schedule = normalizeSchedule(state?.delivery?.schedule);

  const watchlist = (Array.isArray(state?.watchlist) ? state.watchlist : [])
    .map(normalizeTicker)
    .filter(Boolean)
    .filter((ticker, index, arr) => arr.indexOf(ticker) === index)
    .slice(0, MAX_WATCHLIST);

  /** @type {CloudPayload} */
  const payload = {
    email,
    watchlist,
    schedule,
    enabled: Boolean(state?.delivery?.enabled),
    userId: typeof state?.userId === "string" ? state.userId : null,
  };

  return payload;
}

/**
 * Hard-fail if any private financial or secret field is present.
 * Walks top-level keys and shallow nested objects.
 *
 * @template T
 * @param {T} payload
 * @returns {T}
 */
export function assertNoPrivateLeak(payload) {
  if (!isPlainObject(payload)) {
    throw new TypeError("Cloud payload must be a plain object");
  }

  const stack = [payload];
  while (stack.length) {
    const node = stack.pop();
    for (const [key, value] of Object.entries(node)) {
      if (FORBIDDEN_CLOUD_KEYS.includes(key)) {
        throw new Error(`Refusing to transmit private field: ${key}`);
      }
      if (isPlainObject(value)) stack.push(value);
    }
  }

  // Allow-list enforcement: only these keys may exist at the root.
  const allowed = new Set(["email", "watchlist", "schedule", "enabled", "userId"]);
  for (const key of Object.keys(payload)) {
    if (!allowed.has(key)) {
      throw new Error(`Refusing unexpected cloud field: ${key}`);
    }
  }

  return payload;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** @param {unknown} value */
function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/** @param {unknown} value @returns {string} */
function normalizeTicker(value) {
  return String(value ?? "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9.\-]/g, "");
}

/** @param {unknown} value @returns {number|null} */
function toNullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

/** @param {unknown} value @returns {string} */
function normalizeTime(value) {
  const raw = String(value ?? "").trim();
  const match = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(raw);
  if (!match) return "";
  return `${match[1].padStart(2, "0")}:${match[2]}`;
}

/** @param {string} timeHhMm @returns {string} */
function formatTimeLabel(timeHhMm) {
  const { hour12, minute, period } = splitTimeParts(timeHhMm);
  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

/** @param {string[]} times @returns {string} */
function formatTimesLabel(times) {
  const labels = (times || []).map(formatTimeLabel);
  if (!labels.length) return formatTimeLabel("09:00");
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return `${labels[0]} & ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")} & ${labels[labels.length - 1]}`;
}
