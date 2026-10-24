// Sign-in for live mode: the authorization code flow with PKCE (S256) against the Cognito hosted
// UI, with no SDK. The access token lives in this object's memory and nowhere else: not in any
// storage, cookie, address, DOM node, log or error message. The id and refresh tokens are
// discarded. There is no silent refresh: when the token is about to expire the visitor signs in
// again, which the hosted-UI session usually makes one click. The token is never decoded; the
// server verifies it and the page needs no claim from it.

import { isKnownRoute, parseHash } from "../router";
import { MESSAGES } from "./messages";
import { challengeFor, createState, createVerifier } from "./pkce";

export const STORAGE_KEY = "precertly.live.signin";
/** A token this close to its end is treated as expired, so a request never starts with it. */
export const EXPIRY_MARGIN_MS = 30_000;
/** How long a sign-in attempt stays valid between leaving for the hosted UI and coming back. */
const ATTEMPT_LIFETIME_MS = 15 * 60_000;
const EXCHANGE_TIMEOUT_MS = 15_000;

export interface SessionLocation {
  origin: string;
  pathname: string;
  search: string;
  hash: string;
  assign(url: string): void;
}

export interface SessionDeps {
  hostedUiOrigin: string;
  clientId: string;
  fetch: typeof fetch;
  now: () => number;
  location: SessionLocation;
  /** Replaces the address (path and hash, no query) and tells the router. */
  replaceUrl: (url: string) => void;
  /** May throw: storage can be blocked. */
  storage: () => Pick<Storage, "getItem" | "setItem" | "removeItem">;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
}

export interface SessionSnapshot {
  signedIn: boolean;
  /** True after a token ran out, until the visitor signs in again or out. */
  expired: boolean;
  /** A fixed sentence about the last sign-in attempt, or null. */
  notice: string | null;
  /** True while the browser is on its way to the hosted UI or exchanging a code. */
  busy: boolean;
}

interface StoredAttempt {
  state: string;
  verifier: string;
  returnHash: string;
  createdAt: number;
}

/** A hash to return to after sign-in: it must start with #/ and name a page this build has. */
export function safeReturnHash(hash: unknown): string {
  if (typeof hash !== "string" || hash.length > 512 || !hash.startsWith("#/")) return "#/";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(hash)) return "#/";
  return isKnownRoute(parseHash(hash).path, true) ? hash : "#/";
}

function readAttempt(raw: string | null): StoredAttempt | null {
  if (raw === null) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (typeof v !== "object" || v === null) return null;
    const o = v as Record<string, unknown>;
    if (
      typeof o.state === "string" &&
      typeof o.verifier === "string" &&
      typeof o.returnHash === "string" &&
      typeof o.createdAt === "number"
    ) {
      return { state: o.state, verifier: o.verifier, returnHash: o.returnHash, createdAt: o.createdAt };
    }
  } catch {
    // Not JSON: treated as no stored attempt.
  }
  return null;
}

export class LiveSession {
  readonly #deps: SessionDeps;
  #token: string | null = null;
  #expiresAt = 0;
  #timer: unknown = null;
  #callback: Promise<void> | null = null;
  #snapshot: SessionSnapshot = { signedIn: false, expired: false, notice: null, busy: false };
  readonly #listeners = new Set<() => void>();

  constructor(deps: SessionDeps) {
    this.#deps = deps;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  getSnapshot = (): SessionSnapshot => this.#snapshot;

  /**
   * The Authorization header value, or null when there is no token or it is about to expire. This
   * is the only way the token leaves this object, and only the origin-guarded client calls it.
   */
  bearer(): string | null {
    if (this.#token === null) return null;
    if (this.#deps.now() >= this.#expiresAt - EXPIRY_MARGIN_MS) {
      this.#drop(true);
      return null;
    }
    return `Bearer ${this.#token}`;
  }

  /** True when a token is held and not about to expire. Checks the clock, like bearer(). */
  isActive(): boolean {
    return this.bearer() !== null;
  }

  /** Leaves for the hosted UI. Sets a notice instead when storage is blocked. */
  async beginSignIn(returnHash: string): Promise<void> {
    if (this.#snapshot.busy) return;
    this.#set({ busy: true, notice: null, expired: false });
    try {
      const state = createState();
      const verifier = createVerifier();
      const challenge = await challengeFor(verifier);
      const attempt: StoredAttempt = {
        state,
        verifier,
        returnHash: safeReturnHash(returnHash),
        createdAt: this.#deps.now(),
      };
      try {
        this.#deps.storage().setItem(STORAGE_KEY, JSON.stringify(attempt));
      } catch {
        this.#set({ busy: false, notice: MESSAGES.signInUnavailable });
        return;
      }
      const url = new URL("/oauth2/authorize", this.#deps.hostedUiOrigin);
      url.search = new URLSearchParams({
        response_type: "code",
        client_id: this.#deps.clientId,
        redirect_uri: this.#redirectUri(),
        scope: "openid",
        state,
        code_challenge: challenge,
        code_challenge_method: "S256",
      }).toString();
      // A top-level navigation, not a form post (the Content Security Policy has form-action 'none').
      this.#deps.location.assign(url.toString());
    } catch {
      this.#set({ busy: false, notice: MESSAGES.signInFailed });
    }
  }

  /**
   * Handles a return from the hosted UI, if the address is one. Safe to call more than once: the
   * work is done once and later calls get the same promise (React StrictMode runs effects twice
   * in development). Does nothing, and makes no request, for an address without sign-in parameters.
   */
  handleCallback(): Promise<void> {
    this.#callback ??= this.#runCallback();
    return this.#callback;
  }

  signOut(): void {
    this.#drop(false);
    this.#set({ notice: MESSAGES.signedOutNotice, expired: false });
    const url = new URL("/logout", this.#deps.hostedUiOrigin);
    url.search = new URLSearchParams({
      client_id: this.#deps.clientId,
      logout_uri: this.#redirectUri(),
    }).toString();
    this.#deps.location.assign(url.toString());
  }

  /** Forgets the token and any pending timer. Used by sign-out, expiry and tests. */
  dispose(): void {
    this.#drop(false);
    this.#listeners.clear();
  }

  #redirectUri(): string {
    return `${this.#deps.location.origin}/`;
  }

  async #runCallback(): Promise<void> {
    const { location } = this.#deps;
    const params = new URLSearchParams(location.search);
    if (!params.has("code") && !params.has("error") && !params.has("state")) return;
    const code = params.get("code");
    const state = params.get("state");
    const failed = params.has("error");

    // Read and delete the stored attempt, and clean the address, before anything else happens: a
    // failure then leaves neither the code in the address nor the verifier in storage.
    const attempt = this.#takeAttempt();
    const path = location.pathname.startsWith("/") && !location.pathname.startsWith("//") ? location.pathname : "/";
    this.#deps.replaceUrl(`${path}${safeReturnHash(attempt?.returnHash)}`);

    if (failed) {
      this.#set({ notice: MESSAGES.signInCancelled });
      return;
    }
    if (
      attempt === null ||
      code === null ||
      code === "" ||
      state === null ||
      state !== attempt.state ||
      this.#deps.now() - attempt.createdAt > ATTEMPT_LIFETIME_MS
    ) {
      this.#set({ notice: MESSAGES.signInUnverified });
      return;
    }

    this.#set({ busy: true, notice: null });
    const token = await this.#exchange(code, attempt.verifier);
    if (token === null) {
      this.#set({ busy: false, notice: MESSAGES.signInFailed });
      return;
    }
    this.#hold(token.accessToken, token.expiresInSeconds);
  }

  /** Reads the stored attempt and deletes it, so it can be used once. Null if there is none. */
  #takeAttempt(): StoredAttempt | null {
    try {
      const storage = this.#deps.storage();
      const attempt = readAttempt(storage.getItem(STORAGE_KEY));
      storage.removeItem(STORAGE_KEY);
      return attempt;
    } catch {
      return null;
    }
  }

  async #exchange(code: string, verifier: string): Promise<{ accessToken: string; expiresInSeconds: number } | null> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), EXCHANGE_TIMEOUT_MS);
    try {
      const response = await this.#deps.fetch(new URL("/oauth2/token", this.#deps.hostedUiOrigin), {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: this.#deps.clientId,
          code,
          redirect_uri: this.#redirectUri(),
          code_verifier: verifier,
        }).toString(),
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const body: unknown = await response.json();
      if (typeof body !== "object" || body === null) return null;
      const o = body as Record<string, unknown>;
      if (
        typeof o.token_type !== "string" ||
        o.token_type.toLowerCase() !== "bearer" ||
        typeof o.access_token !== "string" ||
        o.access_token === "" ||
        typeof o.expires_in !== "number" ||
        !Number.isFinite(o.expires_in) ||
        o.expires_in <= 0
      ) {
        return null;
      }
      // id_token and refresh_token, if present, are ignored and never read.
      return { accessToken: o.access_token, expiresInSeconds: o.expires_in };
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  #hold(accessToken: string, expiresInSeconds: number): void {
    this.#token = accessToken;
    this.#expiresAt = this.#deps.now() + expiresInSeconds * 1000;
    this.#clearTimer();
    const set = this.#deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    const wait = Math.max(this.#expiresAt - EXPIRY_MARGIN_MS - this.#deps.now(), 0);
    this.#timer = set(() => {
      this.#timer = null;
      this.bearer();
    }, Math.min(wait, 2 ** 31 - 1));
    this.#set({ signedIn: true, expired: false, busy: false, notice: null });
  }

  /** Forgets the token. With `expired` the snapshot says the sign-in ran out. */
  #drop(expired: boolean): void {
    const had = this.#token !== null;
    this.#token = null;
    this.#expiresAt = 0;
    this.#clearTimer();
    if (had) this.#set({ signedIn: false, expired });
  }

  #clearTimer(): void {
    if (this.#timer === null) return;
    (this.#deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>)))(this.#timer);
    this.#timer = null;
  }

  #set(patch: Partial<SessionSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...patch };
    for (const l of [...this.#listeners]) l();
  }
}

/** The real browser wiring. Used by the live boot module only. */
export function browserSessionDeps(config: { hostedUiOrigin: string; clientId: string }): SessionDeps {
  return {
    ...config,
    fetch: window.fetch.bind(window),
    now: () => Date.now(),
    location: {
      get origin() {
        return window.location.origin;
      },
      get pathname() {
        return window.location.pathname;
      },
      get search() {
        return window.location.search;
      },
      get hash() {
        return window.location.hash;
      },
      assign: (url) => window.location.assign(url),
    },
    replaceUrl: (url) => {
      window.history.replaceState(null, "", url);
      // replaceState does not fire hashchange; the router listens for it.
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    },
    storage: () => window.sessionStorage,
  };
}
