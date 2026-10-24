// Fakes shared by the live tests: a place for the browser's pieces to be replaced.
import { LiveSession, type SessionDeps, type SessionLocation } from "./session";

export const API = "https://abc123def.execute-api.us-east-2.amazonaws.com";
export const HOSTED_UI = "https://example-prefix.auth.us-east-2.amazoncognito.com";
export const CLIENT_ID = "testclientid1234567890";
export const SITE = "https://site.example.test";
/** A recognizable fake token: tests look for it everywhere it must not appear. */
export const FAKE_TOKEN = "FAKE.ACCESS.TOKEN-for-tests-9f8e7d";

export function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

export function fakeLocation(search = "", hash = ""): SessionLocation & { assigned: string[] } {
  const assigned: string[] = [];
  return { origin: SITE, pathname: "/", search, hash, assign: (url) => void assigned.push(url), assigned };
}

export function tokenResponse(overrides: Record<string, unknown> = {}): Response {
  return new Response(
    JSON.stringify({
      access_token: FAKE_TOKEN,
      id_token: "ID.TOKEN.SHOULD.BE.IGNORED",
      refresh_token: "REFRESH-SHOULD-BE-IGNORED",
      token_type: "Bearer",
      expires_in: 3600,
      ...overrides,
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

export interface Harness {
  session: LiveSession;
  storage: ReturnType<typeof fakeStorage>;
  location: ReturnType<typeof fakeLocation>;
  fetch: ReturnType<typeof vi.fn>;
  replaced: string[];
  clock: { now: number };
}

export function makeSession(
  options: { search?: string; hash?: string; stored?: Record<string, string>; fetch?: typeof fetch; storageThrows?: boolean } = {},
): Harness {
  const storage = fakeStorage(options.stored);
  const location = fakeLocation(options.search, options.hash);
  const replaced: string[] = [];
  const clock = { now: 1_000_000 };
  const fetchMock = vi.fn(options.fetch ?? (async () => tokenResponse()));
  const deps: SessionDeps = {
    hostedUiOrigin: HOSTED_UI,
    clientId: CLIENT_ID,
    fetch: fetchMock as unknown as typeof fetch,
    now: () => clock.now,
    location,
    replaceUrl: (url) => void replaced.push(url),
    storage: () => {
      if (options.storageThrows) throw new Error("blocked");
      return storage;
    },
  };
  return { session: new LiveSession(deps), storage, location, fetch: fetchMock, replaced, clock };
}
