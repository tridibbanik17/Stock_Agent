/**
 * Extension API target
 * --------------------
 * Local unpacked development → localhost FastAPI.
 * Production / real users → hosted Render (or other) URL.
 *
 * After first Render deploy:
 *   1. Set PROD_API_BASE to your HTTPS service URL (no trailing slash)
 *   2. Set USE_LOCAL_API = false
 *   3. Reload the unpacked extension (or ship a new build)
 *   4. Set the same URL as PUBLIC_API_BASE_URL in Render + GitHub Actions
 *
 * Override anytime in the console:
 *   globalThis.__STOCK_AGENT_API_BASE__ = "https://…"
 */

/** @type {boolean} */
export const USE_LOCAL_API = true;

/**
 * Replace after deploy, e.g. https://stock-agent-api.onrender.com
 * Must also appear under host_permissions in manifest.json (*.onrender.com covers this).
 * @type {string}
 */
export const PROD_API_BASE = "https://stock-agent-api.onrender.com";

/** @type {string} */
export const LOCAL_API_BASE = "http://127.0.0.1:8000";

/** @returns {string} */
export function resolveApiBase() {
  if (typeof globalThis.__STOCK_AGENT_API_BASE__ === "string") {
    const override = globalThis.__STOCK_AGENT_API_BASE__.trim().replace(/\/$/, "");
    if (override) return override;
  }
  return USE_LOCAL_API ? LOCAL_API_BASE : PROD_API_BASE.replace(/\/$/, "");
}
