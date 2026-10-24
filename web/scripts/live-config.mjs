// The live-mode build configuration, validated in one place. The Vite config, the check-dist
// script and the tests all read it from here, so a value that the build accepts is a value that
// check-dist accepts and that the site's Content Security Policy is built from.
//
// Three environment variables, all or none:
//   VITE_LIVE_API_URL        https://<id>.execute-api.<region>.amazonaws.com   (a trailing slash is dropped)
//   VITE_LIVE_HOSTED_UI_URL  https://<prefix>.auth.<region>.amazoncognito.com
//   VITE_LIVE_CLIENT_ID      letters and digits, 10 to 64
// None set: a recorded-only build, with no live code and no live host in dist. These are public
// identifiers, not secrets: once built they are in the published JavaScript. Error messages name
// the variable and never repeat its value.

export const LIVE_VARIABLES = ["VITE_LIVE_API_URL", "VITE_LIVE_HOSTED_UI_URL", "VITE_LIVE_CLIENT_ID"];

const API_ORIGIN = /^https:\/\/[a-z0-9]{6,32}\.execute-api\.[a-z0-9-]{4,30}\.amazonaws\.com$/;
const HOSTED_UI_ORIGIN = /^https:\/\/[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])\.auth\.[a-z0-9-]{4,30}\.amazoncognito\.com$/;
const CLIENT_ID = /^[A-Za-z0-9]{10,64}$/;

/** The origin of an https URL with no path (a single trailing slash is allowed), or null. */
function originOnly(value) {
  if (typeof value !== "string" || value !== value.trim()) return null;
  const bare = value.endsWith("/") ? value.slice(0, -1) : value;
  return bare;
}

/** @param {unknown} value @returns {string | null} the normalized API origin, or null */
export function normalizeApiOrigin(value) {
  const bare = originOnly(value);
  return bare !== null && API_ORIGIN.test(bare) ? bare : null;
}

/** @param {unknown} value @returns {string | null} the normalized hosted-UI origin, or null */
export function normalizeHostedUiOrigin(value) {
  const bare = originOnly(value);
  return bare !== null && HOSTED_UI_ORIGIN.test(bare) ? bare : null;
}

/** @param {unknown} value @returns {boolean} */
export function isClientId(value) {
  return typeof value === "string" && CLIENT_ID.test(value);
}

/**
 * @param {Record<string, string | undefined>} env
 * @returns {{ enabled: false } | { enabled: true, apiOrigin: string, hostedUiOrigin: string, clientId: string }}
 */
export function readLiveConfig(env) {
  const given = LIVE_VARIABLES.filter((name) => (env[name] ?? "") !== "");
  if (given.length === 0) return { enabled: false };
  if (given.length !== LIVE_VARIABLES.length) {
    const missing = LIVE_VARIABLES.filter((name) => !given.includes(name));
    throw new Error(
      `Live mode is half configured: set all of ${LIVE_VARIABLES.join(", ")} or none. Missing: ${missing.join(", ")}.`,
    );
  }
  const apiOrigin = normalizeApiOrigin(env.VITE_LIVE_API_URL);
  if (apiOrigin === null) {
    throw new Error(
      "VITE_LIVE_API_URL must be https://<id>.execute-api.<region>.amazonaws.com with no path, port, credentials or wildcard.",
    );
  }
  const hostedUiOrigin = normalizeHostedUiOrigin(env.VITE_LIVE_HOSTED_UI_URL);
  if (hostedUiOrigin === null) {
    throw new Error(
      "VITE_LIVE_HOSTED_UI_URL must be https://<prefix>.auth.<region>.amazoncognito.com with no path, port, credentials or wildcard.",
    );
  }
  const clientId = env.VITE_LIVE_CLIENT_ID ?? "";
  if (!isClientId(clientId)) {
    throw new Error("VITE_LIVE_CLIENT_ID must be 10 to 64 letters and digits.");
  }
  return { enabled: true, apiOrigin, hostedUiOrigin, clientId };
}
