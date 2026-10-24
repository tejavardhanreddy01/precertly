import { safeReturnHash, STORAGE_KEY, EXPIRY_MARGIN_MS } from "./session";
import { MESSAGES } from "./messages";
import { challengeFor } from "./pkce";
import { CLIENT_ID, FAKE_TOKEN, HOSTED_UI, SITE, makeSession, tokenResponse } from "./testing";

const attempt = (over: Record<string, unknown> = {}) =>
  JSON.stringify({ state: "STATE-1", verifier: "V".repeat(64), returnHash: "#/live/facet-01", createdAt: 1_000_000, ...over });

describe("starting sign-in", () => {
  it("leaves for the hosted UI with a PKCE S256 request, scope openid, and the exact redirect", async () => {
    const h = makeSession();
    await h.session.beginSignIn("#/live/facet-01");
    expect(h.location.assigned).toHaveLength(1);
    const url = new URL(h.location.assigned[0] ?? "");
    expect(url.origin).toBe(HOSTED_UI);
    expect(url.pathname).toBe("/oauth2/authorize");
    const p = url.searchParams;
    expect(p.get("response_type")).toBe("code");
    expect(p.get("client_id")).toBe(CLIENT_ID);
    expect(p.get("redirect_uri")).toBe(`${SITE}/`);
    expect(p.get("scope")).toBe("openid");
    expect(p.get("code_challenge_method")).toBe("S256");
    const stored = JSON.parse(h.storage.data.get(STORAGE_KEY) ?? "{}");
    expect(p.get("state")).toBe(stored.state);
    expect(p.get("code_challenge")).toBe(await challengeFor(stored.verifier));
    expect(stored.returnHash).toBe("#/live/facet-01");
    // The verifier is never sent to the hosted UI in the redirect.
    expect(h.location.assigned[0]).not.toContain(stored.verifier);
  });

  it("makes a new state and verifier for every attempt", async () => {
    const a = makeSession();
    const b = makeSession();
    await a.session.beginSignIn("#/live");
    await b.session.beginSignIn("#/live");
    const sa = JSON.parse(a.storage.data.get(STORAGE_KEY) ?? "{}");
    const sb = JSON.parse(b.storage.data.get(STORAGE_KEY) ?? "{}");
    expect(sa.state).not.toBe(sb.state);
    expect(sa.verifier).not.toBe(sb.verifier);
  });

  it("does not leave when storage is blocked, and says so", async () => {
    const h = makeSession({ storageThrows: true });
    await h.session.beginSignIn("#/live");
    expect(h.location.assigned).toEqual([]);
    expect(h.session.getSnapshot().notice).toBe(MESSAGES.signInUnavailable);
    expect(h.session.getSnapshot().busy).toBe(false);
  });

  it("ignores a second click while the first is leaving", async () => {
    const h = makeSession();
    await Promise.all([h.session.beginSignIn("#/live"), h.session.beginSignIn("#/live")]);
    expect(h.location.assigned).toHaveLength(1);
  });
});

describe("the return from the hosted UI", () => {
  it("does nothing, and makes no request, for an ordinary address", async () => {
    const h = makeSession({ search: "", stored: { [STORAGE_KEY]: attempt() } });
    await h.session.handleCallback();
    expect(h.fetch).not.toHaveBeenCalled();
    expect(h.replaced).toEqual([]);
    // It does not touch a stored attempt it has no callback for.
    expect(h.storage.data.has(STORAGE_KEY)).toBe(true);
    expect(h.session.getSnapshot().signedIn).toBe(false);
  });

  it("exchanges the code once with the verifier, then holds the token in memory", async () => {
    const h = makeSession({ search: "?code=CODE-1&state=STATE-1", stored: { [STORAGE_KEY]: attempt() } });
    await h.session.handleCallback();
    expect(h.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = h.fetch.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe(`${HOSTED_UI}/oauth2/token`);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["content-type"]).toBe("application/x-www-form-urlencoded");
    expect(init.credentials).toBe("omit");
    const body = new URLSearchParams(String(init.body));
    expect(Object.fromEntries(body)).toEqual({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      code: "CODE-1",
      redirect_uri: `${SITE}/`,
      code_verifier: "V".repeat(64),
    });
    expect(h.session.getSnapshot()).toMatchObject({ signedIn: true, expired: false, notice: null, busy: false });
    expect(h.session.bearer()).toBe(`Bearer ${FAKE_TOKEN}`);
    // The stored attempt is gone and the address is clean, back on the page the visitor came from.
    expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
    expect(h.replaced).toEqual(["/#/live/facet-01"]);
  });

  it("handles a second callback with the same code once (StrictMode runs effects twice)", async () => {
    const h = makeSession({ search: "?code=CODE-1&state=STATE-1", stored: { [STORAGE_KEY]: attempt() } });
    await Promise.all([h.session.handleCallback(), h.session.handleCallback()]);
    await h.session.handleCallback();
    expect(h.fetch).toHaveBeenCalledTimes(1);
    expect(h.replaced).toHaveLength(1);
  });

  it("exchanges nothing when the state does not match, and cleans up", async () => {
    const h = makeSession({ search: "?code=CODE-1&state=OTHER", stored: { [STORAGE_KEY]: attempt() } });
    await h.session.handleCallback();
    expect(h.fetch).not.toHaveBeenCalled();
    expect(h.session.getSnapshot()).toMatchObject({ signedIn: false, notice: MESSAGES.signInUnverified });
    expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
    expect(h.replaced).toEqual(["/#/live/facet-01"]);
  });

  it("compares the state exactly, not by prefix or case", async () => {
    for (const state of ["STATE-1 ", "state-1", "STATE-", ""]) {
      const h = makeSession({ search: `?code=C&state=${encodeURIComponent(state)}`, stored: { [STORAGE_KEY]: attempt() } });
      await h.session.handleCallback();
      expect(h.fetch).not.toHaveBeenCalled();
    }
  });

  it("exchanges nothing when no attempt is stored, or the attempt is old, or the code is missing", async () => {
    const cases: [string, Record<string, string>][] = [
      ["?code=C&state=STATE-1", {}],
      ["?code=C&state=STATE-1", { [STORAGE_KEY]: "not json" }],
      ["?code=C&state=STATE-1", { [STORAGE_KEY]: JSON.stringify({ state: "STATE-1" }) }],
      ["?code=C&state=STATE-1", { [STORAGE_KEY]: attempt({ createdAt: 1_000_000 - 16 * 60_000 }) }],
      ["?state=STATE-1", { [STORAGE_KEY]: attempt() }],
      ["?code=&state=STATE-1", { [STORAGE_KEY]: attempt() }],
    ];
    for (const [search, stored] of cases) {
      const h = makeSession({ search, stored });
      await h.session.handleCallback();
      expect(h.fetch).not.toHaveBeenCalled();
      expect(h.session.getSnapshot().notice).toBe(MESSAGES.signInUnverified);
      expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
    }
  });

  it("shows a fixed message for an error callback and never repeats error_description", async () => {
    const h = makeSession({
      search: "?error=access_denied&error_description=%3Cscript%3Eevil%3C%2Fscript%3E+secret-host.example&state=STATE-1",
      stored: { [STORAGE_KEY]: attempt() },
    });
    await h.session.handleCallback();
    expect(h.fetch).not.toHaveBeenCalled();
    expect(h.session.getSnapshot().notice).toBe(MESSAGES.signInCancelled);
    expect(JSON.stringify(h.session.getSnapshot())).not.toContain("evil");
    expect(JSON.stringify(h.replaced)).not.toContain("evil");
    expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
  });

  it("fails with a fixed message when the token endpoint fails, and still cleans up", async () => {
    for (const make of [
      async () => new Response("secret detail from the server", { status: 400 }),
      async () => {
        throw new Error("network down https://leaky.example");
      },
      async () => new Response("not json", { status: 200 }),
    ]) {
      const h = makeSession({ search: "?code=C&state=STATE-1", stored: { [STORAGE_KEY]: attempt() }, fetch: make });
      await h.session.handleCallback();
      expect(h.session.getSnapshot()).toMatchObject({ signedIn: false, busy: false, notice: MESSAGES.signInFailed });
      expect(JSON.stringify(h.session.getSnapshot())).not.toMatch(/secret|leaky/);
      expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
    }
  });

  it.each([
    ["wrong token type", { token_type: "MAC" }],
    ["no access token", { access_token: undefined }],
    ["empty access token", { access_token: "" }],
    ["non-string access token", { access_token: 5 }],
    ["string expiry", { expires_in: "3600" }],
    ["zero expiry", { expires_in: 0 }],
    ["negative expiry", { expires_in: -5 }],
    ["infinite expiry", { expires_in: null }],
  ])("rejects a malformed token response: %s", async (_name, over) => {
    const h = makeSession({
      search: "?code=C&state=STATE-1",
      stored: { [STORAGE_KEY]: attempt() },
      fetch: async () => tokenResponse(over),
    });
    await h.session.handleCallback();
    expect(h.session.getSnapshot()).toMatchObject({ signedIn: false, notice: MESSAGES.signInFailed });
    expect(h.session.bearer()).toBeNull();
    expect(h.storage.data.has(STORAGE_KEY)).toBe(false);
  });

  it("goes to the root when the hash to return to is unsafe", async () => {
    for (const returnHash of ["javascript:alert(1)", "#//evil.example", "#/nope", "#/live/a/b", "", "#/live/a b", "#/cases/x\n"]) {
      const h = makeSession({ search: "?code=C&state=STATE-1", stored: { [STORAGE_KEY]: attempt({ returnHash }) } });
      await h.session.handleCallback();
      expect(h.replaced).toEqual(["/#/"]);
    }
  });

  it("never keeps the id or refresh token", async () => {
    const h = makeSession({ search: "?code=C&state=STATE-1", stored: { [STORAGE_KEY]: attempt() } });
    await h.session.handleCallback();
    const everything = JSON.stringify([h.session.getSnapshot(), [...h.storage.data], h.replaced, h.location.assigned]);
    expect(everything).not.toContain("ID.TOKEN");
    expect(everything).not.toContain("REFRESH");
  });
});

describe("the token", () => {
  async function signedIn() {
    const h = makeSession({ search: "?code=C&state=STATE-1", stored: { [STORAGE_KEY]: attempt() } });
    await h.session.handleCallback();
    return h;
  }

  it("is never in storage, the address, the snapshot, the console or any message", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const h = await signedIn();
    expect(h.session.bearer()).not.toBeNull();
    const surfaces = JSON.stringify({
      snapshot: h.session.getSnapshot(),
      storage: [...h.storage.data],
      replaced: h.replaced,
      assigned: h.location.assigned,
      messages: MESSAGES,
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
      cookie: document.cookie,
      title: document.title,
      body: document.body.innerHTML,
      href: window.location.href,
    });
    expect(surfaces).not.toContain(FAKE_TOKEN);
    for (const spy of spies) expect(JSON.stringify(spy.mock.calls)).not.toContain(FAKE_TOKEN);
    vi.restoreAllMocks();
  });

  it("is dropped, with an expired flag, once it is within the safety margin of its end", async () => {
    const h = await signedIn();
    h.clock.now += 3600_000 - EXPIRY_MARGIN_MS - 1;
    expect(h.session.bearer()).not.toBeNull();
    h.clock.now += 1;
    expect(h.session.bearer()).toBeNull();
    expect(h.session.getSnapshot()).toMatchObject({ signedIn: false, expired: true });
    expect(h.session.isActive()).toBe(false);
  });

  it("tells subscribers when it changes", async () => {
    const h = makeSession({ search: "?code=C&state=STATE-1", stored: { [STORAGE_KEY]: attempt() } });
    const seen: boolean[] = [];
    const off = h.session.subscribe(() => seen.push(h.session.getSnapshot().signedIn));
    await h.session.handleCallback();
    off();
    expect(seen).toContain(true);
  });

  it("is cleared by sign-out, which goes to the hosted UI logout for the site's root", async () => {
    const h = await signedIn();
    h.session.signOut();
    expect(h.session.bearer()).toBeNull();
    expect(h.session.getSnapshot()).toMatchObject({ signedIn: false, expired: false });
    const url = new URL(h.location.assigned.at(-1) ?? "");
    expect(url.origin).toBe(HOSTED_UI);
    expect(url.pathname).toBe("/logout");
    expect(url.searchParams.get("client_id")).toBe(CLIENT_ID);
    expect(url.searchParams.get("logout_uri")).toBe(`${SITE}/`);
  });
});

describe("safeReturnHash", () => {
  it("keeps a hash that starts with #/ and names a page of the build", () => {
    for (const hash of ["#/", "#/live", "#/live/facet-01", "#/cases/facet-01?c=x", "#/about"]) {
      expect(safeReturnHash(hash)).toBe(hash);
    }
  });

  it("falls back to #/ for anything else", () => {
    for (const hash of [undefined, null, 5, "", "#", "/live", "live", "#live", "#/nope", "#/live/a/b", "#/" + "a".repeat(600)]) {
      expect(safeReturnHash(hash)).toBe("#/");
    }
  });
});
