import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// scripts/check-dist.mjs run on small, made-up dist folders, with clearly fake placeholder values
// of the right shape.
// Vitest runs from web/, where the script lives.
const SCRIPT = resolve(process.cwd(), "scripts/check-dist.mjs");
const API = "https://abcdefghij.execute-api.us-east-2.amazonaws.com";
const AUTH = "https://placeholder-prefix.auth.us-east-2.amazoncognito.com";
const CLIENT = "PlaceholderClientId0123456789";
const LIVE_ENV = { VITE_LIVE_API_URL: API, VITE_LIVE_HOSTED_UI_URL: AUTH, VITE_LIVE_CLIENT_ID: CLIENT };
const NONE = { VITE_LIVE_API_URL: "", VITE_LIVE_HOSTED_UI_URL: "", VITE_LIVE_CLIENT_ID: "" };

const dirs: string[] = [];
afterAll(() => dirs.forEach((d) => rmSync(d, { recursive: true, force: true })));

function dist(script: string): string {
  const dir = mkdtempSync(join(tmpdir(), "check-dist-"));
  dirs.push(dir);
  mkdirSync(join(dir, "assets"));
  writeFileSync(join(dir, "index.html"), '<!doctype html><script type="module" src="./assets/index.js"></script>');
  writeFileSync(join(dir, "assets", "index.js"), script);
  return dir;
}

function check(dir: string, env: Record<string, string>, extra: string[] = []) {
  const result = spawnSync("node", [SCRIPT, "--dir", dir, ...extra], {
    // The script reads the three variables from its own environment, nothing else of ours.
    env: { PATH: process.env.PATH ?? "", ...env },
    encoding: "utf8",
  });
  return { code: result.status, out: `${result.stdout}${result.stderr}` };
}

const LIVE_JS = `fetch("${API}");open("${AUTH}");var id="${CLIENT}";`;

describe("check-dist", () => {
  it("passes a recorded-only dist when no live variable is set", () => {
    const r = check(dist("console.log(1)"), NONE);
    expect(r.code).toBe(0);
    expect(r.out).toContain("recorded-only");
  });

  it("treats unset variables like empty ones", () => {
    expect(check(dist("console.log(1)"), {}).code).toBe(0);
  });

  it.each(["execute-api", "amazoncognito"])("fails a recorded-only dist that contains %s", (marker) => {
    const r = check(dist(`var x="${marker}";`), NONE);
    expect(r.code).toBe(1);
    expect(r.out).toContain(`contains "${marker}"`);
  });

  it("fails a recorded-only dist that carries live code, without printing the host", () => {
    const r = check(dist(LIVE_JS), NONE);
    expect(r.code).toBe(1);
    expect(r.out).not.toContain("abcdefghij");
    expect(r.out).not.toContain("placeholder-prefix");
    expect(r.out).toContain("[a live host, value not shown]");
  });

  it("passes a live dist when the variables are set and the three values are in a script", () => {
    const r = check(dist(LIVE_JS), LIVE_ENV);
    expect(r.code).toBe(0);
    expect(r.out).toContain("live mode");
    // Values are never printed.
    expect(r.out).not.toContain("abcdefghij");
    expect(r.out).not.toContain("placeholder-prefix");
    expect(r.out).not.toContain(CLIENT);
  });

  it.each([
    ["the gateway origin", `open("${AUTH}");var id="${CLIENT}";`, "gateway origin"],
    ["the hosted-UI origin", `fetch("${API}");var id="${CLIENT}";`, "hosted-UI origin"],
    ["the client id", `fetch("${API}");open("${AUTH}");`, "client id"],
  ])("fails a live dist that lacks %s (a recorded-only build published by mistake)", (_n, js, what) => {
    const r = check(dist(js), LIVE_ENV);
    expect(r.code).toBe(1);
    expect(r.out).toContain(what);
  });

  it("allows only the two configured live hosts", () => {
    const r = check(dist(`${LIVE_JS}fetch("https://other.execute-api.us-east-2.amazonaws.com/x");`), LIVE_ENV);
    expect(r.code).toBe(1);
    expect(r.out).toContain("[a live host, value not shown]");
    expect(r.out).not.toContain("other.execute-api");
  });

  it("still fails a URL on any other host in either mode", () => {
    expect(check(dist('fetch("https://evil.example/x")'), NONE).code).toBe(1);
    expect(check(dist(`${LIVE_JS}fetch("https://evil.example/x")`), LIVE_ENV).code).toBe(1);
  });

  it.each([
    ["half set", { VITE_LIVE_API_URL: API }],
    ["a malformed gateway", { ...LIVE_ENV, VITE_LIVE_API_URL: "https://evil.example" }],
    ["a malformed hosted UI", { ...LIVE_ENV, VITE_LIVE_HOSTED_UI_URL: `${AUTH}/login` }],
    ["a malformed client id", { ...LIVE_ENV, VITE_LIVE_CLIENT_ID: "short" }],
  ])("fails when the variables are %s, before looking at dist, without repeating them", (_n, env) => {
    const r = check(dist(LIVE_JS), env);
    expect(r.code).toBe(1);
    expect(r.out).not.toContain("evil.example");
    expect(r.out).not.toContain("abcdefghij");
  });

  it("reads another directory from --dir or from CHECK_DIST_DIR", () => {
    const dir = dist("console.log(1)");
    expect(check(dir, NONE).code).toBe(0);
    const viaEnv = spawnSync("node", [SCRIPT], {
      env: { PATH: process.env.PATH ?? "", CHECK_DIST_DIR: dir, ...NONE },
      encoding: "utf8",
    });
    expect(viaEnv.status).toBe(0);
  });

  it("fails clearly when the directory does not exist", () => {
    const r = check(join(tmpdir(), "no-such-dist-directory"), NONE);
    expect(r.code).toBe(1);
    expect(r.out).toContain("is missing");
  });

  it("fails an inline script, as before", () => {
    const dir = dist("1");
    writeFileSync(join(dir, "index.html"), "<!doctype html><script>alert(1)</script>");
    expect(check(dir, NONE).code).toBe(1);
  });
});
