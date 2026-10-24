// The one place the live pages talk to the network. Every request goes through here and is
// refused unless its URL is on the configured API origin; the Authorization header is added only
// on that origin, and only from the session. Credentials are never sent (the gateway allows none).

import type { LiveSession } from "./session";

export class ExpiredError extends Error {
  constructor() {
    super("sign-in expired");
    this.name = "ExpiredError";
  }
}

export class NetworkError extends Error {
  constructor() {
    super("network error");
    this.name = "NetworkError";
  }
}

export class AbortedError extends Error {
  constructor() {
    super("aborted");
    this.name = "AbortedError";
  }
}

export interface ApiResponse {
  status: number;
  /** The parsed JSON body, or undefined when there is none or it is not JSON. */
  body: unknown;
}

export interface ApiClient {
  /** A request with no token (the status probe). */
  open(method: "GET", path: string, signal: AbortSignal): Promise<ApiResponse>;
  /** A request with the access token. Throws ExpiredError, without sending anything, when it has none. */
  authed(method: "GET" | "POST", path: string, signal: AbortSignal, json?: unknown): Promise<ApiResponse>;
}

/** More than this is treated as no body: no real response from this API is near it. */
const MAX_BODY_CHARS = 2_000_000;

export function createApiClient(deps: {
  apiOrigin: string;
  session: Pick<LiveSession, "bearer">;
  fetch: typeof fetch;
}): ApiClient {
  const { apiOrigin } = deps;

  function target(path: string): URL {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      throw new Error("refused: not a path on the API origin");
    }
    const url = new URL(path, apiOrigin);
    if (url.origin !== apiOrigin) throw new Error("refused: not the API origin");
    return url;
  }

  async function send(
    method: string,
    path: string,
    signal: AbortSignal,
    headers: Record<string, string>,
    json?: unknown,
  ): Promise<ApiResponse> {
    const url = target(path);
    let response: Response;
    try {
      response = await deps.fetch(url, {
        method,
        headers: json === undefined ? headers : { ...headers, "content-type": "application/json" },
        body: json === undefined ? undefined : JSON.stringify(json),
        mode: "cors",
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal,
      });
    } catch {
      // An abort and a network failure look alike to a caller that only cares about its own signal.
      throw signal.aborted ? new AbortedError() : new NetworkError();
    }
    let body: unknown;
    try {
      const text = await response.text();
      body = text.length > 0 && text.length <= MAX_BODY_CHARS ? JSON.parse(text) : undefined;
    } catch {
      if (signal.aborted) throw new AbortedError();
      body = undefined;
    }
    return { status: response.status, body };
  }

  return {
    open: (method, path, signal) => send(method, path, signal, {}),
    authed: (method, path, signal, json) => {
      const bearer = deps.session.bearer();
      if (bearer === null) return Promise.reject(new ExpiredError());
      return send(method, path, signal, { authorization: bearer }, json);
    },
  };
}
