import { base64url, challengeFor, createState, createVerifier, randomString, VERIFIER_ALPHABET, VERIFIER_LENGTH } from "./pkce";

describe("PKCE", () => {
  it("matches the RFC 7636 Appendix B vector", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("makes a verifier of the right length from the allowed alphabet only", () => {
    for (let i = 0; i < 50; i += 1) {
      const v = createVerifier();
      expect(v).toHaveLength(VERIFIER_LENGTH);
      expect(VERIFIER_LENGTH).toBeGreaterThanOrEqual(43);
      expect(v).toMatch(/^[A-Za-z0-9\-._~]{64}$/);
    }
  });

  it("does not repeat a verifier or a state", () => {
    const verifiers = new Set(Array.from({ length: 200 }, () => createVerifier()));
    const states = new Set(Array.from({ length: 200 }, () => createState()));
    expect(verifiers.size).toBe(200);
    expect(states.size).toBe(200);
  });

  it("makes a state of 32 random bytes, base64url without padding", () => {
    const s = createState();
    expect(s).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(base64url(new Uint8Array(32))).toHaveLength(43);
  });

  it("skips random bytes that would favor some characters", () => {
    // 66 characters: bytes 198 to 255 are skipped, so 255 never maps to the alphabet's start.
    const feed = [255, 254, 198, 0, 1, 2];
    let i = 0;
    const out = randomString(3, VERIFIER_ALPHABET, (n) => Uint8Array.from({ length: n }, () => feed[i++ % feed.length] ?? 0));
    expect(out).toBe(VERIFIER_ALPHABET.slice(0, 3));
  });

  it("encodes base64url with the URL-safe characters", () => {
    expect(base64url(Uint8Array.from([0xfb, 0xff, 0xfe]))).toBe("-__-");
  });
});
