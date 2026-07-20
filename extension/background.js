/**
 * MV3 service worker — lightweight message router.
 * Popup now talks to storage.js directly for most UX paths.
 * SYNC_DELIVERY remains a dry-run (console.log) until the API is live.
 */

import {
  assertNoPrivateLeak,
  buildCloudPayload,
  cacheCloudProfile,
  getLocalState,
  setGeminiKey,
  setHoldings,
  setWatchlist,
} from "./lib/storage.js";

chrome.runtime.onInstalled.addListener(async () => {
  const state = await getLocalState();
  if (!Array.isArray(state.watchlist)) {
    await chrome.storage.local.set({ watchlist: [] });
  }
  console.info("[Stock Agent] installed — private holdings stay on-device");
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) =>
      sendResponse({
        ok: false,
        error: error?.message || "Unknown background error",
      })
    );
  return true;
});

async function handleMessage(message) {
  switch (message?.type) {
    case "GET_STATE":
      return getLocalState();

    case "SET_HOLDINGS":
      return setHoldings(message.holdings || {});

    case "SET_GEMINI_KEY":
      await setGeminiKey(message.geminiApiKey ?? message.geminiKey ?? "");
      return { saved: true };

    case "UPDATE_WATCHLIST_LOCAL":
      return setWatchlist(message.watchlist || []);

    case "SYNC_DELIVERY":
      return syncDeliveryDryRun(message.delivery, message.watchlist);

    default:
      throw new Error(`Unhandled message type: ${message?.type}`);
  }
}

/**
 * Dry-run cloud sync: sanitize → assert → console.log (no HTTPS yet).
 */
async function syncDeliveryDryRun(deliveryPatch, watchlistOverride) {
  const state = await getLocalState();
  const delivery = { ...state.delivery, ...(deliveryPatch || {}) };
  const watchlist = Array.isArray(watchlistOverride)
    ? watchlistOverride
    : state.watchlist;

  const localView = { ...state, delivery, watchlist };
  const outbound = assertNoPrivateLeak(buildCloudPayload(localView));

  console.log("[SYNC_DELIVERY] sanitized payload (dry-run — not sent):", outbound);

  await cacheCloudProfile({
    watchlist: outbound.watchlist,
    delivery: {
      email: outbound.email,
      schedule: outbound.schedule,
      enabled: outbound.enabled,
    },
    userId: state.userId,
  });

  return {
    userId: state.userId,
    delivery: {
      email: outbound.email,
      schedule: outbound.schedule,
      enabled: outbound.enabled,
    },
    watchlist: outbound.watchlist,
  };
}
