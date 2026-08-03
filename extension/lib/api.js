/**
 * Thin HTTPS client for cloud delivery preferences only.
 * Never accepts or forwards holdings / Gemini keys.
 */

import { resolveApiBase } from "./config.js";
import { assertNoPrivateLeak, buildCloudPayload } from "./storage.js";

export const API_BASE = resolveApiBase();

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
  } catch (networkError) {
    const error = new Error(
      `Cannot reach Stock Agent API at ${API_BASE}. ` +
        (API_BASE.includes("localhost") || API_BASE.includes("127.0.0.1")
          ? "Is the FastAPI server running locally?"
          : "Is the hosted API awake? Free Render services sleep after idle.")
    );
    error.cause = networkError;
    throw error;
  }

  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text };
    }
  }

  if (!response.ok) {
    const detail = formatApiDetail(body?.detail || body?.message || response.statusText);
    const error = new Error(detail);
    error.status = response.status;
    error.body = body;
    throw error;
  }

  return body;
}

/** @param {unknown} detail */
function formatApiDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String(/** @type {{ msg: string }} */ (item).msg);
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return String(detail || "Request failed");
}

/**
 * Upsert delivery preferences via POST /api/subscribe.
 * Sends manageToken when present so strangers cannot overwrite the profile.
 * @param {import("./storage.js").LocalState | object} localState
 */
export async function subscribeDelivery(localState) {
  const payload = assertNoPrivateLeak(buildCloudPayload(localState));

  if (!payload.email) {
    throw new Error("Email is required to sync delivery preferences");
  }
  if (!payload.watchlist.length) {
    throw new Error("Add at least one ticker before enabling email delivery");
  }

  /** @type {Record<string, unknown>} */
  const body = {
    email: payload.email,
    watchlist: payload.watchlist,
    schedule: payload.schedule,
    enabled: payload.enabled,
    emailOnGradeChangeOnly: Boolean(payload.emailOnGradeChangeOnly),
  };
  if (payload.manageToken) {
    body.manageToken = payload.manageToken;
  }

  return request("/api/subscribe", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Email a one-time recovery link to reclaim manageToken.
 * @param {string} email
 */
export async function recoverSubscription(email) {
  const normalized = String(email || "").trim().toLowerCase();
  if (!normalized || !normalized.includes("@")) {
    throw new Error("Enter a valid email to recover ownership");
  }
  return request("/api/subscribe/recover", {
    method: "POST",
    body: JSON.stringify({ email: normalized }),
  });
}

/** @deprecated Prefer subscribeDelivery */
export async function syncDeliveryProfile(localState) {
  return subscribeDelivery(localState);
}

/** Snapshot quotes for the popup dashboard (tickers only — no holdings). */
export async function fetchWatchlistSnapshot(tickers) {
  const watchlist = (tickers || []).map((t) => String(t).toUpperCase());
  return request("/api/quotes/snapshot", {
    method: "POST",
    body: JSON.stringify({ watchlist }),
  });
}
