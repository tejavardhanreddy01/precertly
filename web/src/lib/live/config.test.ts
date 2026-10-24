import { LIVE_VARIABLES, readLiveConfig } from "../../../scripts/live-config.mjs";

const good = {
  VITE_LIVE_API_URL: "https://abc123def.execute-api.us-east-2.amazonaws.com",
  VITE_LIVE_HOSTED_UI_URL: "https://example-prefix.auth.us-east-2.amazoncognito.com",
  VITE_LIVE_CLIENT_ID: "testclientid1234567890",
};

describe("live configuration", () => {
  it("is off when none of the three variables is set (or all are empty)", () => {
    expect(readLiveConfig({})).toEqual({ enabled: false });
    expect(readLiveConfig({ VITE_LIVE_API_URL: "", VITE_LIVE_HOSTED_UI_URL: "", VITE_LIVE_CLIENT_ID: "" })).toEqual({
      enabled: false,
    });
  });

  it("accepts a valid configuration and normalizes the API URL to its origin", () => {
    expect(readLiveConfig(good)).toEqual({
      enabled: true,
      apiOrigin: "https://abc123def.execute-api.us-east-2.amazonaws.com",
      hostedUiOrigin: "https://example-prefix.auth.us-east-2.amazoncognito.com",
      clientId: "testclientid1234567890",
    });
    const slash = readLiveConfig({ ...good, VITE_LIVE_API_URL: `${good.VITE_LIVE_API_URL}/` });
    expect(slash).toMatchObject({ apiOrigin: good.VITE_LIVE_API_URL });
  });

  it.each(LIVE_VARIABLES)("refuses a half-set configuration when %s is the one missing", (missing) => {
    const env: Record<string, string> = { ...good };
    delete env[missing];
    expect(() => readLiveConfig(env)).toThrow(/half configured/);
    expect(() => readLiveConfig(env)).toThrow(missing);
  });

  it.each([
    "http://abc123def.execute-api.us-east-2.amazonaws.com",
    "https://abc123def.execute-api.us-east-2.amazonaws.com/prod",
    "https://abc123def.execute-api.us-east-2.amazonaws.com//",
    "https://abc123def.execute-api.us-east-2.amazonaws.com:8443",
    "https://user@abc123def.execute-api.us-east-2.amazonaws.com",
    "https://*.execute-api.us-east-2.amazonaws.com",
    "https://abc123def.execute-api.us-east-2.amazonaws.com.evil.example",
    "https://evil.example/abc123def.execute-api.us-east-2.amazonaws.com",
    "https://abc123def.example.com",
    " https://abc123def.execute-api.us-east-2.amazonaws.com",
    "https://abc123def.execute-api.us-east-2.amazonaws.com?x=1",
    "https://ABC123DEF.execute-api.us-east-2.amazonaws.com",
  ])("refuses the API URL %s without repeating it", (url) => {
    const run = () => readLiveConfig({ ...good, VITE_LIVE_API_URL: url });
    expect(run).toThrow(/VITE_LIVE_API_URL must be/);
    try {
      run();
    } catch (e) {
      expect(String((e as Error).message)).not.toContain("evil");
      expect(String((e as Error).message)).not.toContain("abc123def");
    }
  });

  it.each([
    "http://example-prefix.auth.us-east-2.amazoncognito.com",
    "https://example-prefix.auth.us-east-2.amazoncognito.com/login",
    "https://*.auth.us-east-2.amazoncognito.com",
    "https://example-prefix.auth.us-east-2.amazoncognito.com:443",
    "https://example-prefix.auth.us-east-2.amazoncognito.com.evil.example",
    "https://example-prefix.us-east-2.amazoncognito.com",
    "https://-bad.auth.us-east-2.amazoncognito.com",
    "https://example-prefix.auth.us-east-2.amazoncognito.com/?a=b",
  ])("refuses the hosted UI URL %s", (url) => {
    expect(() => readLiveConfig({ ...good, VITE_LIVE_HOSTED_UI_URL: url })).toThrow(/VITE_LIVE_HOSTED_UI_URL must be/);
  });

  it.each(["short", "has space 1234567890", "has-dash-1234567890", "x".repeat(65), "päd1234567890"])(
    "refuses the client id %j",
    (id) => {
      expect(() => readLiveConfig({ ...good, VITE_LIVE_CLIENT_ID: id })).toThrow(/VITE_LIVE_CLIENT_ID must be/);
    },
  );

  it("accepts a client id of 10 and of 64 letters and digits", () => {
    expect(readLiveConfig({ ...good, VITE_LIVE_CLIENT_ID: "a1B2c3D4e5" })).toMatchObject({ enabled: true });
    expect(readLiveConfig({ ...good, VITE_LIVE_CLIENT_ID: "a".repeat(64) })).toMatchObject({ enabled: true });
  });

  it("is off in this test run, so the app under test is recorded-only", () => {
    expect(__LIVE_ENABLED__).toBe(false);
    expect(__LIVE_API_ORIGIN__).toBe("");
    expect(__LIVE_HOSTED_UI_ORIGIN__).toBe("");
    expect(__LIVE_CLIENT_ID__).toBe("");
  });
});
