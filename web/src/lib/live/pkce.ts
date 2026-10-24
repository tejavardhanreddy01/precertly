// Proof Key for Code Exchange (RFC 7636) and the per-attempt random values, from the browser's own
// crypto only: getRandomValues for randomness and subtle.digest for the S256 challenge.

/** The unreserved characters RFC 7636 allows in a code verifier. */
export const VERIFIER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
export const VERIFIER_LENGTH = 64;
export const STATE_BYTES = 32;

type RandomBytes = (length: number) => Uint8Array;

const browserRandom: RandomBytes = (length) => crypto.getRandomValues(new Uint8Array(length));

/** Base64url without padding (RFC 4648, section 5). */
export function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * A random string over an alphabet. Bytes at or above the largest multiple of the alphabet
 * length are skipped, so no character is more likely than another.
 */
export function randomString(length: number, alphabet: string, random: RandomBytes = browserRandom): string {
  const limit = 256 - (256 % alphabet.length);
  let out = "";
  while (out.length < length) {
    for (const byte of random(length * 2)) {
      if (byte < limit) {
        out += alphabet.charAt(byte % alphabet.length);
        if (out.length === length) break;
      }
    }
  }
  return out;
}

export function createVerifier(random: RandomBytes = browserRandom): string {
  return randomString(VERIFIER_LENGTH, VERIFIER_ALPHABET, random);
}

/** An unguessable value tying the redirect back to this attempt. */
export function createState(random: RandomBytes = browserRandom): string {
  return base64url(random(STATE_BYTES));
}

/** The S256 code challenge for a verifier. */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}
